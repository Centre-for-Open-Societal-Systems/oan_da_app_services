"""HTTP client for the live Farmer Registry (OpenG2P Staff API).

Every call is a POST with the OpenG2P staff envelope and the double-submit
CSRF pair the staff middleware requires. Auth order is staff-user IAM login
(when username and password are set), then Keycloak client-credentials, then
a static API key. Reads come from ``/register-data/*``, updates become change
requests, and registration goes through the ingest pipeline.

Copy keys from ``config.example.json`` into site_config.json. The client does
not read the ``_environments`` examples in that file.

Staff API endpoints used:
    register-data/get_subject_record        -> get_farmer (identity row)
    register-data/get_tab_records           -> get_farmer (farmer/location/household tabs), get_farmer_plots (land tab)
    register-data/search_in_a_register      -> list_farmers (filter_by on whitelisted *_name fields)
    change-requests/create_change_request   -> update_farmer (one change request per section)
    input-mechanism-data/ingest-data        -> register_farmer
    ingestion-data/search_in_ingestion_data -> get_ingest_status
"""

from dataclasses import dataclass, field
from typing import ClassVar

import frappe
import requests
from frappe import _

from da_services.integrations.staff_api.auth import auth_and_csrf_headers
from da_services.integrations.staff_api.transport import http_failure, staff_envelope

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

def _positive_timeout(value: object, default: float = 30) -> float:
	if isinstance(value, bool) or not isinstance(value, (int, float, str)):
		return default
	try:
		timeout = float(value)
	except ValueError:
		return default
	return timeout if timeout > 0 else default


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
		text = f"{self.error_message or ''} {self.error_code or ''}".lower().replace("_", " ")
		return (not self.ok) and any(marker in text for marker in _NOT_FOUND_MARKERS)


class HTTPFarmerRegistryClient(FarmerRegistryClient):
	"""Live Farmer Registry client over the OpenG2P Staff API."""

	REGISTER_MNEMONIC: ClassVar[str] = "Farmer"

	def __init__(self):
		self.base_url = str(frappe.conf.get("farmer_registry_url", "") or "").rstrip("/")
		self.api_key = frappe.conf.get("farmer_registry_api_key")
		self.timeout = _positive_timeout(frappe.conf.get("farmer_registry_timeout"))

		self.headers = {"Content-Type": "application/json", "Accept": "application/json"}
		# Static API key. Staff IAM or client-credentials replace this per request.
		if self.api_key:
			self.headers["Authorization"] = f"Bearer {self.api_key}"

		# Register / intake / tab identifiers for the Farmer register.
		ingest = frappe.conf.get("farmer_registry_ingest", {}) or {}
		self.ingest_data_model = ingest.get("data_model", "FARMER_ODK_MODEL")
		self.ingest_register_id = ingest.get("register_id")
		self.ingest_intake_form_id = ingest.get("intake_form_id")
		self.register_id = ingest.get("register_id")
		self.tabs = {**DEFAULT_TABS, **(ingest.get("tabs") or {})}

		# Staff IAM login and/or Keycloak client-credentials. Tokens are cached
		# on the worker by the shared staff auth helper.
		self.keycloak = frappe.conf.get("farmer_registry_keycloak", {}) or {}

		# Fail fast: a missing or partial config should surface when the client
		# is built, not on the first request from the field.
		self._validate_config()

	# ------------------------------------------------------------------ config

	def _validate_config(self) -> None:
		"""Raise FarmerRegistryError if the live client cannot possibly work."""
		problems = []
		if not self.base_url.startswith(("http://", "https://")):
			problems.append("farmer_registry_url must be an http(s) URL")

		iam_keys = ("iam_base_url", "kc_realm_url", "username", "password")
		cc_keys = ("token_url", "client_id", "client_secret")
		iam_set = [k for k in iam_keys if self.keycloak.get(k)]
		cc_set = [k for k in cc_keys if self.keycloak.get(k)]
		iam_complete = len(iam_set) == len(iam_keys)
		cc_complete = len(cc_set) == len(cc_keys)
		if cc_set and not cc_complete:
			problems.append(
				"farmer_registry_keycloak needs token_url, client_id and client_secret "
				f"(found only: {', '.join(cc_set)})"
			)
		# Realm URLs without a username are optional alongside a service account.
		# A started staff login (username or password present) must be complete.
		staff_login_started = any(self.keycloak.get(k) for k in ("username", "password"))
		if iam_set and not iam_complete and (staff_login_started or not (cc_complete or self.api_key)):
			problems.append(
				"farmer_registry_keycloak staff login needs iam_base_url, kc_realm_url, username and password "
				f"(found only: {', '.join(iam_set)})"
			)
		if not iam_complete and not cc_complete and not self.api_key:
			problems.append(
				"set farmer_registry_keycloak (staff login or service account) or farmer_registry_api_key"
			)

		if not self.register_id:
			problems.append("farmer_registry_ingest.register_id (Farmer register id) is required")
		if not self.ingest_data_model:
			problems.append("farmer_registry_ingest.data_model is required")

		if problems:
			raise FarmerRegistryError("Farmer Registry HTTP client misconfigured: " + "; ".join(problems))

	# ------------------------------------------------------------------ auth

	def _auth_and_csrf_headers(self) -> tuple[dict, dict]:
		"""Headers + cookies for a staff POST. Same pair the Staff API requires."""
		return auth_and_csrf_headers(
			keycloak=self.keycloak,
			timeout=self.timeout,
			headers=self.headers,
			client_error=FarmerRegistryClientError,
			server_error=FarmerRegistryServerError,
			log_title="Farmer Registry Auth Error",
		)

	# ------------------------------------------------------------------ transport

	@staticmethod
	def _raise_for_status(response: requests.Response, action: str) -> None:
		"""Map HTTP errors to retryable vs non-retryable registry exceptions.

		4xx: the request is wrong (auth, roles, validation) — retrying the same
		call cannot succeed. 5xx: the registry is unhealthy — safe to retry.
		"""
		failure = http_failure(response)
		if failure is None:
			return
		kind, code, snippet = failure

		if kind == "client":
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
		return staff_envelope(
			request_payload,
			sender_app="DA_SERVICES",
			timestamp=frappe.utils.now(),
			page=page,
			page_size=page_size,
			filter_by=filter_by,
			search_text=search_text,
			sort_by=sort_by,
			include_instance_id=True,
			include_sort_by=True,
			empty_filter="",
		)

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
			# A lookup failure must not turn a farmer search into a 500. The
			# registry can still filter on the code the caller sent.
			frappe.logger("da_services").warning(
				"Could not resolve administrative area %s to a name", code_or_name, exc_info=True
			)
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

	def find_internal_id(self, reference: str) -> str | None:
		"""Resolve a Farmer-ID such as FR-… to the register's internal record id.

		get_subject_record only accepts the internal id. Search matches
		functional_record_id, which is the number shown to people.
		"""
		if not reference:
			return None
		res = self._staff_post(
			"/register-data/search_in_a_register",
			{"register_id": self.register_id},
			action="find farmer",
			page_size=20,
			search_text=reference,
			filter_by={"functional_record_id": reference},
		)
		if not res.ok:
			if res.not_found:
				return None
			res = self._staff_post(
				"/register-data/search_in_a_register",
				{"register_id": self.register_id},
				action="find farmer",
				page_size=20,
				search_text=reference,
			)
			if not res.ok:
				if res.not_found:
					return None
				self._reject(res, "find farmer")
		for row in res.payload or []:
			if not isinstance(row, dict):
				continue
			if reference in (row.get("functional_record_id"), row.get("internal_record_id")):
				internal = row.get("internal_record_id")
				return str(internal) if internal else None
		return None

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
		incomplete: list[str] = []
		for key in ("farmer", "location", "household"):
			try:
				merge_tab_records(record, self._tab_records(farmer_id, key, action=f"get farmer {key} tab"))
			except FarmerRegistryError as e:
				incomplete.append(key)
				frappe.log_error(
					title="Farmer Registry tab fetch failed",
					message=f"{farmer_id} {key} tab: {e!s}",
				)
		farmer = registry_record_to_farmer(record, farmer_id)
		# Say which sections are missing instead of returning a record that looks
		# like "no phone / no household" when the registry simply failed mid-read.
		farmer.incomplete_sections = incomplete
		return farmer

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
