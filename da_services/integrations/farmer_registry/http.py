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

Intake form registration (farmer_registry_register_mode = "intake"):
    intake-form-metadata/render_intake_form             -> render_intake_form
    register-metadata/get_all_registers                 -> get_registers
    documents/upload_documents                          -> upload_document (multipart)
    intake-form-data/save_intake_form_submission        -> save_intake_section
    intake-form-data/finalize_intake_form_submission    -> finalize_intake_submission
    intake-form-data/get_intake_form_submission         -> get_intake_submission
    Master Data attributes/get_attribute_values         -> get_attribute_values
    Master Data geo/get_all_geo_levels, get_geo_level_values
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

# Discovered register / intake form ids; see HTTPFarmerRegistryClient.register_id
ID_CACHE_PREFIX = "da_services:farmer_registry:ids"
ID_CACHE_TTL = 60 * 60
# The Farmer intake form on dev is "farmer_ingestion_intake"; matched ignoring spaces, _ and -.
DEFAULT_INTAKE_FORM_CONTAINS = "Farmer Ingestion"


def _squash(value: object) -> str:
	"""Lower-case alphanumerics only, the way the registry collection matches form names."""
	return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


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
		# Code lists and places for the intake form; same staff token and envelope.
		self.master_data_url = str(frappe.conf.get("farmer_registry_master_data_url", "") or "").rstrip("/")
		self.api_key = frappe.conf.get("farmer_registry_api_key")
		self.timeout = _positive_timeout(frappe.conf.get("farmer_registry_timeout"))

		self.headers = {"Content-Type": "application/json", "Accept": "application/json"}
		# Static API key. Staff IAM or client-credentials replace this per request.
		if self.api_key:
			self.headers["Authorization"] = f"Bearer {self.api_key}"

		# Register / intake / tab identifiers for the Farmer register. The ids are
		# optional: when absent they are discovered from the registry
		# (get_all_registers / get_all_intake_forms) and cached for an hour, so a
		# redeployed registry does not need a site_config change.
		ingest = frappe.conf.get("farmer_registry_ingest", {}) or {}
		self.ingest_data_model = ingest.get("data_model", "FARMER_ODK_MODEL")
		self._configured_register_id = ingest.get("register_id") or None
		self._configured_intake_form_id = ingest.get("intake_form_id") or None
		self.intake_form_contains = str(
			frappe.conf.get("farmer_registry_intake_form_contains") or DEFAULT_INTAKE_FORM_CONTAINS
		)
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

	def _staff_post(
		self, path: str, request_payload: dict, *, action: str, base_url: str | None = None, **pagination
	) -> StaffResponse:
		"""POST an enveloped request to the Staff API and parse the envelope back.

		Raises FarmerRegistryServerError on network failure, and the 4xx/5xx
		mapping from _raise_for_status. A 200 with response_status ERROR is
		returned as a StaffResponse so callers can treat "not found" specially.
		base_url points the same call at another OpenG2P service (Master Data).
		"""
		url = f"{base_url or self.base_url}{path}"
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

	# ------------------------------------------------------------------ registry ids

	def _cached_id(self, kind: str, discover) -> str:
		"""An id discovered from the registry, cached per staff host for an hour."""
		key = f"{ID_CACHE_PREFIX}:{kind}:{self.base_url}"
		value = frappe.cache.get_value(key)
		if not value:
			value = discover()
			frappe.cache.set_value(key, value, expires_in_sec=ID_CACHE_TTL)
		return str(value)

	@property
	def register_id(self) -> str:
		"""The Farmer register id: configured, else the register named "Farmer"."""
		if self._configured_register_id:
			return self._configured_register_id

		def discover() -> str:
			wanted = self.REGISTER_MNEMONIC.lower()
			for row in self.get_registers():
				if str(row.get("register_mnemonic") or "").lower() == wanted and row.get("register_id"):
					return str(row["register_id"])
			raise FarmerRegistryError(f"Farmer Registry has no register named {self.REGISTER_MNEMONIC}")

		return self._cached_id("register_id", discover)

	@property
	def intake_form_id(self) -> str:
		"""The Farmer intake form id: configured, else the one form whose mnemonic
		contains farmer_registry_intake_form_contains (spaces, _ and - ignored)."""
		if self._configured_intake_form_id:
			return self._configured_intake_form_id

		def discover() -> str:
			wanted = _squash(self.intake_form_contains)
			forms = [
				f
				for f in self.get_intake_forms()
				if str(f.get("register_id") or "") == self.register_id
				and f.get("form_id")
				and (not wanted or wanted in _squash(f.get("form_mnemonic")))
			]
			if len(forms) != 1:
				names = ", ".join(str(f.get("form_mnemonic")) for f in forms) or "none"
				raise FarmerRegistryError(
					"Farmer Registry intake form could not be chosen: "
					f"{len(forms)} form(s) match {self.intake_form_contains!r} ({names}); "
					"set farmer_registry_intake_form_contains or farmer_registry_ingest.intake_form_id"
				)
			return str(forms[0]["form_id"])

		return self._cached_id("intake_form_id", discover)

	@staticmethod
	def clear_id_cache() -> None:
		"""Forget discovered register / form ids (after a registry redeploy)."""
		frappe.cache.delete_keys(ID_CACHE_PREFIX)

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

	def search_farmers(
		self,
		*,
		search_text: str = "",
		filter_by: dict | None = None,
		page: int = 1,
		page_size: int = 50,
	) -> dict:
		"""One page of search_in_a_register with the registry's page metadata.

		filter_by takes DA codes for woreda / kebele and passes any other key
		through as a registry filter. Kebele is narrowed here as in list_farmers.
		"""
		page = max(1, int(page or 1))
		page_size = max(1, min(int(page_size or 50), 500))

		filters: dict = {}
		for key, value in (filter_by or {}).items():
			if value in (None, ""):
				continue
			if key in ("woreda", "kebele"):
				name = self._area_name(value)
				if name:
					filters[f"{key}_name"] = name
			else:
				filters[key] = value

		res = self._staff_post(
			"/register-data/search_in_a_register",
			{"register_id": self.register_id},
			action="search farmers",
			page=page,
			page_size=page_size,
			filter_by=filters or None,
			search_text=search_text or "",
		)
		rows: list = []
		if res.ok:
			rows = [r for r in (res.payload or []) if isinstance(r, dict)]
		elif not res.not_found:
			self._reject(res, "search farmers")

		items = [search_row_to_list_item(row) for row in rows]
		# The dev registry (29 Oct 2026) returns the same rows whatever filter_by
		# says, and kebele_name is not whitelisted anyway. Area filters are the
		# caller's scope, so they are re-applied here and fail closed: a row that
		# names another area, or no area at all, is dropped.
		narrowed = False
		for key, attr in (("woreda_name", "woreda"), ("kebele_name", "kebele")):
			wanted = filters.get(key)
			if not wanted:
				continue
			kept = [i for i in items if getattr(i, attr) == wanted]
			narrowed = narrowed or len(kept) != len(items)
			items = kept

		total = res.pagination.get("number_of_items")
		pages = res.pagination.get("number_of_pages")
		return {
			"items": items,
			"page": page,
			"page_size": page_size,
			"total": int(total) if total is not None else len(items),
			"number_of_pages": int(pages) if pages else (1 if items else 0),
			# True when rows were dropped here; total / number_of_pages are then
			# the registry's unfiltered figures.
			"filtered_client_side": narrowed,
		}

	def get_change_requests(
		self, farmer_id: str, *, tab: str | None = None, status: str | None = None
	) -> list[dict]:
		"""Change requests on a farmer, one get_change_requests call per tab.

		The registry keys change requests by tab, so all configured tabs are
		read unless one tab key is given. A tab with none returns an empty list.
		"""
		if tab:
			tab_id = self.tabs.get(tab)
			if not tab_id:
				raise FarmerRegistryClientError(f"Unknown farmer tab {tab!r}", status_code=400)
			tab_ids = [tab_id]
		else:
			tab_ids = list(dict.fromkeys(self.tabs.values()))

		rows: list[dict] = []
		for tab_id in tab_ids:
			res = self._staff_post(
				"/change-requests/get_change_requests",
				{"subject_register_id": self.register_id, "subject_record_id": farmer_id, "tab_id": tab_id},
				action="get change requests",
				page_size=100,
			)
			if not res.ok:
				if res.not_found:
					continue
				self._reject(res, "get change requests")
			rows.extend(r for r in (res.payload or []) if isinstance(r, dict))

		if status:
			wanted = status.strip().upper()
			rows = [r for r in rows if str(r.get("approval_status") or "").upper() == wanted]
		rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
		return rows

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
		params = {
			"data_model": self.ingest_data_model,
			"register_id": self.register_id,
			"intake_form_id": self.intake_form_id,
		}

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

	# ------------------------------------------------------------------ intake form

	def _master_data_post(self, path: str, request_payload: dict, *, action: str) -> list:
		if not self.master_data_url.startswith(("http://", "https://")):
			raise FarmerRegistryError(
				"Farmer Registry HTTP client misconfigured: farmer_registry_master_data_url must be an http(s) URL"
			)
		res = self._staff_post(
			path, request_payload, action=action, base_url=self.master_data_url, page_size=1000
		)
		if not res.ok:
			self._reject(res, action)
		return res.payload or []

	def render_intake_form(self) -> dict:
		res = self._staff_post(
			"/intake-form-metadata/render_intake_form",
			{"form_id": self.intake_form_id},
			action="render intake form",
		)
		if not res.ok:
			self._reject(res, "render intake form")
		return dict(res.payload or {})

	def get_registers(self) -> list[dict]:
		res = self._staff_post(
			"/register-metadata/get_all_registers", {}, action="list registers", page_size=100
		)
		if not res.ok:
			self._reject(res, "list registers")
		return [r for r in (res.payload or []) if isinstance(r, dict)]

	def get_intake_forms(self) -> list[dict]:
		res = self._staff_post(
			"/intake-form-metadata/get_all_intake_forms",
			{"register_id": self.register_id},
			action="list intake forms",
			page_size=100,
		)
		if not res.ok:
			if res.not_found:
				return []
			self._reject(res, "list intake forms")
		return [f for f in (res.payload or []) if isinstance(f, dict)]

	def get_attribute_values(self, attribute_id: str) -> list[dict]:
		payload = self._master_data_post(
			"/attributes/get_attribute_values",
			{"attribute_id": attribute_id},
			action=f"read code list {attribute_id}",
		)
		if isinstance(payload, dict):
			payload = payload.get("attribute_values") or []
		return list(payload)

	def get_geo_levels(self) -> list[dict]:
		return list(self._master_data_post("/geo/get_all_geo_levels", {}, action="read geo levels"))

	def get_geo_level_values(self, level_id: str, parent_level_value_id: str | None = None) -> list[dict]:
		request = {"level_id": level_id}
		if parent_level_value_id:
			request["parent_level_value_id"] = parent_level_value_id
		return list(
			self._master_data_post("/geo/get_geo_level_values", request, action=f"read places {level_id}")
		)

	def upload_document(self, filename: str, content: bytes, content_type: str) -> str:
		"""POST /documents/upload_documents as multipart (field "documents")."""
		url = f"{self.base_url}/documents/upload_documents"
		headers, cookies = self._auth_and_csrf_headers()
		headers.pop("Content-Type", None)  # requests sets the multipart boundary
		try:
			response = requests.post(
				url,
				headers=headers,
				cookies=cookies,
				files={"documents": (filename, content, content_type)},
				timeout=self.timeout,
			)
		except requests.RequestException as e:
			frappe.log_error(
				title="Farmer Registry HTTP Error", message=f"Could not reach {url} (upload): {e!s}"
			)
			raise FarmerRegistryServerError(f"Farmer Registry unreachable: {e!s}") from e

		self._raise_for_status(response, "upload document")
		try:
			data = response.json()
		except ValueError as e:
			raise FarmerRegistryServerError("Farmer Registry returned non-JSON for upload document") from e
		header = data.get("response_header") or {}
		if header.get("response_status") != "SUCCESS":
			raise FarmerRegistryClientError(
				f"Farmer Registry rejected upload document: {header.get('response_error_message') or header.get('response_error_code') or 'unknown error'}",
				status_code=200,
				body=header.get("response_error_code"),
			)
		payload = (data.get("response_body") or {}).get("response_payload")
		docs = payload if isinstance(payload, list) else (payload or {}).get("documents") or []
		document_id = (docs[0] or {}).get("document_id") if docs else None
		if not document_id:
			raise FarmerRegistryServerError("Farmer Registry upload returned no document_id")
		return str(document_id)

	@staticmethod
	def _intake_result(res: StaffResponse) -> dict:
		payload = res.payload if isinstance(res.payload, dict) else {}
		return {
			"success": res.ok,
			"submission_id": payload.get("submission_id"),
			"draft_status": payload.get("draft_status"),
			"approval_status": payload.get("approval_status"),
			"status": res.status,
			"error_code": res.error_code,
			"error_message": res.error_message,
		}

	def save_intake_section(self, section: dict) -> dict:
		res = self._staff_post(
			"/intake-form-data/save_intake_form_submission", section, action="save intake section"
		)
		return self._intake_result(res)

	def finalize_intake_submission(self, submission_id: str) -> dict:
		res = self._staff_post(
			"/intake-form-data/finalize_intake_form_submission",
			{"submission_id": submission_id},
			action="finalize intake submission",
		)
		return self._intake_result(res)

	def get_intake_submission(self, submission_id: str) -> dict | None:
		res = self._staff_post(
			"/intake-form-data/get_intake_form_submission",
			{"submission_id": submission_id},
			action="read intake submission",
		)
		if not res.ok:
			if res.not_found:
				return None
			self._reject(res, "read intake submission")
		return dict(res.payload or {})
