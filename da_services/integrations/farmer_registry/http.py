"""HTTP client for the live Farmer Registry (OpenG2P Staff API).

Every call is a POST with the OpenG2P staff envelope, a Keycloak
client-credentials bearer token and the double-submit CSRF pair the staff
middleware requires. Reads come from ``/register-data/*``, updates become
change requests, and registration goes through the ingest pipeline.

site_config.json:
    {
        "farmer_registry_backend": "http",
        "farmer_registry_url": "https://staff-farmer-development.oanstaging.com",
        "farmer_registry_timeout": 30,
        "farmer_registry_keycloak": {
            "token_url": "https://keycloak-development.oanstaging.com/realms/staff/protocol/openid-connect/token",
            "client_id": "da-services",
            "client_secret": "..."
        },
        "farmer_registry_ingest": {
            "data_model": "FARMER_ODK_MODEL",
            "register_id": "a1a4d25a-1cd4-4356-abac-985a0b3c6bcd",
            "intake_form_id": "a1a4d25a-1cd4-4356-abac-8782382649",
            "tabs": {"farmer": "farmer_farmer_tab", "location": "farmer_location_tab",
                     "household": "farmer_household_link_tab", "land": "farmer_land_tab"}
        }
    }

Staff API endpoints used:
    register-data/get_subject_record        -> get_farmer (identity row)
    register-data/get_tab_records           -> get_farmer (farmer/location/household tabs), get_farmer_plots (land tab)
    register-data/search_in_a_register      -> list_farmers (filter_by on whitelisted *_name fields)
    change-requests/create_change_request   -> update_farmer (one change request per section)
    input-mechanism-data/ingest-data        -> register_farmer
    ingestion-data/search_in_ingestion_data -> get_ingest_status
"""

import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import ClassVar

import frappe
import requests
from frappe import _

from .client import (
	FarmerRegistryClient,
	FarmerRegistryClientError,
	FarmerRegistryError,
	FarmerRegistryServerError,
)
from .mapper import (
	land_record_to_plot,
	merge_tab_records,
	registry_record_to_farmer,
	search_row_to_list_item,
	updates_to_change_requests,
)
from .schemas import Farmer, FarmerListItem, Plot

# Farmer register tabs on the registry; overridable through farmer_registry_ingest.tabs
DEFAULT_TABS: dict[str, str] = {
	"farmer": "farmer_farmer_tab",
	"location": "farmer_location_tab",
	"household": "farmer_household_link_tab",
	"land": "farmer_land_tab",
	"crop": "farmer_crop_tab",
	"livestock": "farmer_livestock_tab",
}

# Registry messages that mean "no such record" rather than a failure
_NOT_FOUND_MARKERS = ("not found", "does not exist", "no record")


@dataclass
class StaffResponse:
	"""Parsed OpenG2P staff envelope."""

	status: str
	error_code: str | None
	error_message: str | None
	payload: object
	pagination: dict = field(default_factory=dict)

	@property
	def ok(self) -> bool:
		return self.status == "SUCCESS"

	@property
	def not_found(self) -> bool:
		msg = (self.error_message or "").lower()
		return (not self.ok) and any(m in msg for m in _NOT_FOUND_MARKERS)


class HTTPFarmerRegistryClient(FarmerRegistryClient):
	"""Live Farmer Registry client over the OpenG2P Staff API."""

	REGISTER_MNEMONIC: ClassVar[str] = "Farmer"

	def __init__(self):
		self.base_url = str(frappe.conf.get("farmer_registry_url", "") or "").rstrip("/")
		self.api_key = frappe.conf.get("farmer_registry_api_key")
		self.timeout = frappe.conf.get("farmer_registry_timeout", 30)  # seconds

		self.headers = {"Content-Type": "application/json", "Accept": "application/json"}
		# A static token/API key is the fallback when no Keycloak client is configured.
		if self.api_key:
			self.headers["Authorization"] = f"Bearer {self.api_key}"

		# Register / intake / tab identifiers for the Farmer register.
		ingest = frappe.conf.get("farmer_registry_ingest", {}) or {}
		self.ingest_data_model = ingest.get("data_model", "FARMER_ODK_MODEL")
		self.ingest_register_id = ingest.get("register_id")
		self.ingest_intake_form_id = ingest.get("intake_form_id")
		self.register_id = ingest.get("register_id")
		self.tabs = {**DEFAULT_TABS, **(ingest.get("tabs") or {})}

		# Keycloak client-credentials for a DA Services service account.
		# The lock keeps concurrent threads from refreshing the same token at once.
		self.keycloak = frappe.conf.get("farmer_registry_keycloak", {}) or {}
		self._token_cache: dict = {}
		self._token_lock = threading.Lock()

		# Fail fast: a missing or partial config should surface when the client
		# is built, not on the first request from the field.
		self._validate_config()

	# ------------------------------------------------------------------ config

	def _validate_config(self) -> None:
		"""Raise FarmerRegistryError if the live client cannot possibly work."""
		problems = []
		if not self.base_url.startswith(("http://", "https://")):
			problems.append("farmer_registry_url must be an http(s) URL")

		kc_keys = ("token_url", "client_id", "client_secret")
		kc_set = [k for k in kc_keys if self.keycloak.get(k)]
		if kc_set and len(kc_set) != len(kc_keys):
			problems.append(
				"farmer_registry_keycloak needs token_url, client_id and client_secret "
				f"(found only: {', '.join(kc_set)})"
			)
		if not kc_set and not self.api_key:
			problems.append("set farmer_registry_keycloak (service account) or farmer_registry_api_key")

		if not self.register_id:
			problems.append("farmer_registry_ingest.register_id (Farmer register id) is required")
		if not self.ingest_data_model:
			problems.append("farmer_registry_ingest.data_model is required")

		if problems:
			raise FarmerRegistryError("Farmer Registry HTTP client misconfigured: " + "; ".join(problems))

	# ------------------------------------------------------------------ auth

	def _get_access_token(self) -> str | None:
		"""Return a bearer token for the Staff API.

		Preference order:
		1. Keycloak client-credentials (service account), cached until near expiry.
		2. Static ``farmer_registry_api_key`` (already on self.headers).

		Refresh is serialised with a lock so two threads cannot both hit
		Keycloak and race on the cache.
		"""
		kc = self.keycloak
		token_url = kc.get("token_url")
		client_id = kc.get("client_id")
		client_secret = kc.get("client_secret")
		if not (token_url and client_id and client_secret):
			return None

		def _fresh(cache: dict) -> bool:
			return bool(cache.get("token")) and cache.get("expires_at", 0) > time.time() + 30

		if _fresh(self._token_cache):
			return self._token_cache["token"]

		with self._token_lock:
			if _fresh(self._token_cache):
				return self._token_cache["token"]

			try:
				resp = requests.post(
					token_url,
					data={
						"grant_type": "client_credentials",
						"client_id": client_id,
						"client_secret": client_secret,
					},
					timeout=self.timeout,
				)
			except requests.RequestException as e:
				frappe.log_error(
					title="Farmer Registry Auth Error",
					message=f"Keycloak unreachable at {token_url}: {e!s}",
				)
				raise FarmerRegistryServerError("Could not reach Keycloak for a registry token") from e

			if resp.status_code >= 400:
				frappe.log_error(
					title="Farmer Registry Auth Error",
					message=f"Keycloak token request failed: HTTP {resp.status_code} {resp.text[:300]}",
				)
				exc = FarmerRegistryClientError if resp.status_code < 500 else FarmerRegistryServerError
				raise exc(
					f"Keycloak refused the token request (HTTP {resp.status_code})",
					status_code=resp.status_code,
				)

			data = resp.json()
			token = data.get("access_token")
			if not token:
				raise FarmerRegistryServerError("Keycloak response had no access_token")

			self._token_cache = {
				"token": token,
				"expires_at": time.time() + int(data.get("expires_in", 300)),
			}
			return token

	def _auth_and_csrf_headers(self) -> tuple[dict, dict]:
		"""Headers + cookies for a state-changing (POST) staff call.

		Adds the bearer token and a double-submit CSRF pair (matching header +
		cookie), which the registry's CsrfMiddleware requires on POST.
		"""
		headers = dict(self.headers)
		token = self._get_access_token()
		if token:
			headers["Authorization"] = f"Bearer {token}"

		csrf = secrets.token_urlsafe(24)
		headers["X-CSRF-Token"] = csrf
		cookies = {"X-CSRF-Token": csrf}
		return headers, cookies

	# ------------------------------------------------------------------ transport

	@staticmethod
	def _raise_for_status(response: requests.Response, action: str) -> None:
		"""Map HTTP errors to retryable vs non-retryable registry exceptions.

		4xx: the request is wrong (auth, roles, validation) — retrying the same
		call cannot succeed. 5xx: the registry is unhealthy — safe to retry.
		"""
		code = response.status_code
		if code < 400:
			return

		detail = ""
		try:
			detail = response.json().get("response_header", {}).get("response_error_message") or ""
		except ValueError:
			pass
		snippet = detail or (response.text or "")[:300]

		if code < 500:
			frappe.log_error(
				title="Farmer Registry rejected request",
				message=f"{action}: HTTP {code} {response.url}\n{snippet}",
			)
			raise FarmerRegistryClientError(
				f"Farmer Registry rejected {action} (HTTP {code}): {snippet}",
				status_code=code,
				body=snippet,
			)

		frappe.log_error(
			title="Farmer Registry server error",
			message=f"{action}: HTTP {code} {response.url}\n{snippet}",
		)
		raise FarmerRegistryServerError(
			f"Farmer Registry failed during {action} (HTTP {code})", status_code=code
		)

	@staticmethod
	def _staff_envelope(
		request_payload: dict,
		*,
		page: int = 1,
		page_size: int = 50,
		filter_by: dict | None = None,
		search_text: str = "",
		sort_by: str = "",
	) -> dict:
		"""Wrap a payload in the OpenG2P staff request envelope."""
		return {
			"request_header": {
				"sender_app_mnemonic": "DA_SERVICES",
				"sender_app_url": "",
				"request_id": str(uuid.uuid4()),
				"request_timestamp": frappe.utils.now(),
				"instance_id": "",
			},
			"request_body": {
				"request_payload": request_payload,
				"pagination_request": {
					"current_page": page,
					"page_size": page_size,
					"sort_by": sort_by,
					"filter_by": filter_by or "",
					"search_text": search_text,
				},
			},
		}

	def _staff_post(self, path: str, request_payload: dict, *, action: str, **pagination) -> StaffResponse:
		"""POST an enveloped request to the Staff API and parse the envelope back.

		Raises FarmerRegistryServerError on network failure, and the 4xx/5xx
		mapping from _raise_for_status. A 200 with response_status ERROR is
		returned as a StaffResponse so callers can treat "not found" specially.
		"""
		url = f"{self.base_url}{path}"
		headers, cookies = self._auth_and_csrf_headers()
		try:
			response = requests.post(
				url,
				headers=headers,
				cookies=cookies,
				json=self._staff_envelope(request_payload, **pagination),
				timeout=self.timeout,
			)
		except requests.RequestException as e:
			frappe.log_error(
				title="Farmer Registry HTTP Error", message=f"Could not reach {url} ({action}): {e!s}"
			)
			raise FarmerRegistryServerError(f"Farmer Registry unreachable: {e!s}") from e

		self._raise_for_status(response, action)
		try:
			data = response.json()
		except ValueError as e:
			raise FarmerRegistryServerError(f"Farmer Registry returned non-JSON for {action}") from e

		header = data.get("response_header") or {}
		body = data.get("response_body") or {}
		return StaffResponse(
			status=header.get("response_status") or "ERROR",
			error_code=header.get("response_error_code"),
			error_message=header.get("response_error_message"),
			payload=body.get("response_payload"),
			pagination=body.get("pagination_response") or {},
		)

	@staticmethod
	def _reject(res: StaffResponse, action: str) -> None:
		"""Turn a response_status=ERROR envelope into a client error."""
		raise FarmerRegistryClientError(
			f"Farmer Registry rejected {action}: {res.error_message or res.error_code or 'unknown error'}",
			status_code=200,
			body=res.error_code,
		)

	# ------------------------------------------------------------------ helpers

	@staticmethod
	def _area_name(code_or_name: str | None) -> str | None:
		"""Registry filters use administrative *names*; translate a DA code when we know it."""
		if not code_or_name:
			return code_or_name
		try:
			name = frappe.db.get_value("DA Administrative Area", {"code": code_or_name}, "name_en")
		except Exception:
			name = None
		return name or code_or_name

	def _tab_records(self, farmer_id: str, tab_key: str, *, action: str) -> list:
		"""Rows of one register tab for a farmer (list of RegisterTabRecordData dicts)."""
		tab_id = self.tabs.get(tab_key)
		if not tab_id:
			return []
		res = self._staff_post(
			"/register-data/get_tab_records",
			{"subject_register_id": self.register_id, "subject_record_id": farmer_id, "tab_id": tab_id},
			action=action,
		)
		if not res.ok:
			if res.not_found:
				return []
			self._reject(res, action)
		return list(res.payload or [])

	# ------------------------------------------------------------------ reads

	def get_farmer(self, farmer_id: str) -> Farmer | None:
		"""Farmer identity row plus the farmer / location / household tabs.

		get_subject_record returns the register row (flattened fields);
		birth, socio-economic, phone, location and household data live in tab
		sections, so those tabs are merged in (best effort: a tab failure is
		logged and skipped, the identity row still comes back).
		"""
		res = self._staff_post(
			"/register-data/get_subject_record",
			{"subject_register_id": self.register_id, "subject_record_id": farmer_id},
			action="get farmer",
		)
		if not res.ok:
			if res.not_found:
				return None
			self._reject(res, "get farmer")

		record = dict(res.payload or {})
		for key in ("farmer", "location", "household"):
			try:
				merge_tab_records(record, self._tab_records(farmer_id, key, action=f"get farmer {key} tab"))
			except FarmerRegistryError as e:
				frappe.log_error(
					title="Farmer Registry tab fetch failed",
					message=f"{farmer_id} {key} tab: {e!s}",
				)
		return registry_record_to_farmer(record, farmer_id)

	def list_farmers(
		self,
		*,
		woreda: str | None = None,
		kebele: str | None = None,
		state: str | None = None,
		limit: int = 50,
		offset: int = 0,
	) -> tuple[list[FarmerListItem], int]:
		"""Search the Farmer register.

		The registry filters on whitelisted *_name fields, so DA codes are
		translated to names through DA Administrative Area. kebele_name is not
		yet whitelisted on the registry side, so Kebele is also applied here on
		the returned rows.
		"""
		limit = max(1, int(limit or 50))
		offset = max(0, int(offset or 0))

		filter_by: dict = {}
		woreda_name = self._area_name(woreda)
		kebele_name = self._area_name(kebele)
		if woreda_name:
			filter_by["woreda_name"] = woreda_name
		if kebele_name:
			filter_by["kebele_name"] = kebele_name
		if state:
			filter_by["state"] = state

		res = self._staff_post(
			"/register-data/search_in_a_register",
			{"register_id": self.register_id},
			action="list farmers",
			page=offset // limit + 1,
			page_size=limit,
			filter_by=filter_by or None,
		)
		if not res.ok:
			self._reject(res, "list farmers")

		rows = list(res.payload or [])
		items = [search_row_to_list_item(row) for row in rows]
		if kebele_name:
			narrowed = [i for i in items if not i.kebele or i.kebele == kebele_name]
			# Only trust the client-side narrowing when the rows carry a kebele.
			if any(i.kebele for i in items):
				items = narrowed

		total = res.pagination.get("number_of_items")
		return items, int(total) if total is not None else len(items)

	def get_farmer_plots(self, farmer_id: str) -> list[Plot]:
		"""Land Details rows for a farmer (Lands tab)."""
		plots: list[Plot] = []
		for section in self._tab_records(farmer_id, "land", action="get farmer plots"):
			for rec in section.get("records") or []:
				plots.append(land_record_to_plot(rec))
		return plots

	# ------------------------------------------------------------------ writes

	def update_farmer(self, farmer_id: str, updates: dict, expected_version: int | None = None) -> dict:
		"""Edit a farmer through registry change requests (DA-41).

		The registry has no direct PATCH: each editable section gets a change
		request that a Woreda officer approves. Fields the registry cannot take
		through this route are rejected up front (non-retryable) so the queue
		records a clear reason instead of silently dropping them.
		"""
		groups, unsupported = updates_to_change_requests(updates)
		if unsupported:
			raise FarmerRegistryClientError(
				"Fields not editable through the Farmer Registry change request: " + ", ".join(unsupported),
				status_code=400,
			)
		if not groups:
			raise FarmerRegistryClientError("No registry-backed fields to update", status_code=400)

		change_request_ids: list[str] = []
		for group in groups:
			res = self._staff_post(
				"/change-requests/create_change_request",
				{
					"register_id": self.register_id,
					"register_mnemonic": self.REGISTER_MNEMONIC,
					"tab_id": self.tabs.get(group["tab"]),
					"section_id": group["section_id"],
					"section_register_id": self.register_id,
					"internal_record_id": farmer_id,
					"change_payload": group["payload"],
				},
				action="update farmer",
			)
			if not res.ok:
				self._reject(res, "update farmer")
			payload = res.payload if isinstance(res.payload, dict) else {}
			cr_id = payload.get("change_request_id")
			if cr_id:
				change_request_ids.append(str(cr_id))

		return {
			"success": True,
			"conflict": False,
			"farmer_id": farmer_id,
			"version": ",".join(change_request_ids) or None,
			"change_request_ids": change_request_ids,
			"message": _("Change request created; pending approval in the Farmer Registry"),
		}

	def register_farmer(self, payload: dict) -> dict:
		"""Register a new farmer via the Staff API ingest-data endpoint.

		POST /input-mechanism-data/ingest-data?data_model&register_id&intake_form_id
		Body is the ingest envelope (header + message.payload) built by
		mapper.da_form_to_ingest_payload(). Async: returns a correlation_id.
		"""
		if not isinstance(payload, dict) or not payload.get("message"):
			raise FarmerRegistryError("Invalid ingest payload: missing 'message'")

		url = f"{self.base_url}/input-mechanism-data/ingest-data"
		params = {"data_model": self.ingest_data_model}
		if self.ingest_register_id:
			params["register_id"] = self.ingest_register_id
		if self.ingest_intake_form_id:
			params["intake_form_id"] = self.ingest_intake_form_id

		headers, cookies = self._auth_and_csrf_headers()

		try:
			response = requests.post(
				url,
				headers=headers,
				cookies=cookies,
				params=params,
				json=payload,
				timeout=self.timeout,
			)
		except requests.RequestException as e:
			# Network-level failure (timeout, DNS, connection): transient, retryable.
			frappe.log_error(
				title="Farmer Registry HTTP Error",
				message=f"Could not reach {url} to register farmer: {e!s}",
			)
			raise FarmerRegistryServerError(f"Farmer Registry unreachable: {e!s}") from e

		self._raise_for_status(response, "register farmer")
		data = response.json()

		header = data.get("response_header", {}) or {}
		body = data.get("response_body", {}) or {}
		status = header.get("response_status")
		correlation_id = (body.get("response_payload") or {}).get("correlation_id")
		return {
			"success": status == "SUCCESS",
			"correlation_id": correlation_id,
			"status": status,
			"error_code": header.get("response_error_code"),
			"error_message": header.get("response_error_message"),
		}

	def get_ingest_status(self, correlation_id: str) -> list[dict]:
		"""Search ingestion rows for a correlation_id."""
		res = self._staff_post(
			"/ingestion-data/search_in_ingestion_data",
			{},
			action="get ingest status",
			page_size=50,
			search_text=correlation_id,
		)
		if not res.ok:
			if res.not_found:
				return []
			self._reject(res, "get ingest status")
		payload = res.payload
		if isinstance(payload, list):
			return payload
		return [payload] if payload else []
