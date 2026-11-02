"""HTTP client for the DA Registry staff API (OpenG2P).

DA Services calls the registry server to server with its own Keycloak service account
(client credentials, realm ``staff``, client ``da-services-backend``). A signed-in
user's DA Services token never reaches the registry. Every call is a POST with the
OpenG2P envelope and a double-submit CSRF pair (same random value in the
``X-CSRF-Token`` header and cookie).

The registry trusts the service account with every DA, so callers decide whose record
is read or changed: the DA-ID must come from DA Services' own user -> DA-ID mapping,
never from the request (services/profile.py ``_own_da_id``).

Staff API endpoints used:
    register-data/search_in_a_register     -> find a DA by DA-ID (exact match on top of a
                                              partial search), list DAs by Woreda / Region
    register-data/get_tab_records          -> the DA record (da_details_tab)
    change-requests/create_change_request  -> push_profile_update (two-stage approval in
                                              the registry)

Copy keys from ``config.example.json`` into site_config.json.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import secrets
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import frappe
import requests

from .client import (
	DANotFound,
	DARegistryAuthError,
	DARegistryClient,
	DARegistryContractPending,
	DARegistryError,
	DARegistryUnavailable,
)
from .mapper import (
	DA_TAB,
	LABEL_ATTRIBUTES,
	PROFILE_FIELDS,
	profile_changes_to_change_request,
	registry_record_to_da_reference,
)
from .schemas import DAReference, IntegrationResult, LifecycleOutcome, ProfileUpdate

# Fixed on every environment (registry seed data)
DEFAULT_REGISTER_ID = "6f1c2d8e-4a7b-4c3e-9d2f-8b5a1e7c3f90"
DEFAULT_INTAKE_FORM_ID = "6f1c2d8e-4a7b-4c3e-9d2f-8b5a1e7c3f91"
DEFAULT_IMPORT_DATA_MODEL_ID = "6f1c2d8e-4a7b-4c3e-9d2f-8b5a1e7c3fb2"
IMPORT_BUCKET = "import-files"
PAGE_SIZE = 100

# Lists (My Teams, assignments, Region views) read each DA's record once more; those
# records are kept briefly so a list is not re-read on every page load. A DA's own
# profile and a single DA view are always read live.
RECORD_CACHE_SECONDS = 300
LIST_WORKERS = 8
# Master Data labels change rarely.
LABEL_CACHE_SECONDS = 24 * 3600

# A new client is built per call, so tokens live on the worker, not the instance.
_TOKEN_CACHE: dict[tuple[str, str], dict] = {}
_TOKEN_LOCK = threading.Lock()
_TOKEN_SKEW_SECONDS = 30

# Registry messages that mean "no such record" rather than a failure
_NOT_FOUND_MARKERS = ("not found", "does not exist", "no record")

_LOGIN_FORM_RE = re.compile(r'<form\b[^>]*\bid=["\']kc-form-login["\'][^>]*>', re.IGNORECASE)
_FORM_ACTION_RE = re.compile(r'\baction=["\']([^"\']+)["\']', re.IGNORECASE)


def _log_error(title: str, message: str) -> None:
	"""frappe.log_error, skipped on the list worker threads (they have no site context)."""
	if getattr(frappe.local, "site", None):
		frappe.log_error(title=title, message=message)


def _request_credentials() -> tuple[str, str]:
	"""The signed-in user's staff IAM access token and CSRF value, from the current request.

	The frontend sends the token as ``Authorization: Bearer`` and the IAM's CSRF value as
	``X-CSRF-Token`` (or the browser sends it as the cookie of that name). DA Services has
	already validated the token before the endpoint runs. A token DA Services issued
	itself (password sign-in) means nothing to the registry, so it is refused here.
	"""
	request = getattr(frappe.local, "request", None)
	headers = request.headers if request is not None else {}
	auth = headers.get("Authorization") or ""
	token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
	if not token:
		raise DARegistryAuthError("Sign in through the staff IAM to use the DA Registry")
	issuer = (_jwt_claims(token) or {}).get("iss")
	if issuer and issuer == frappe.conf.get("jwt_issuer"):
		raise DARegistryAuthError(
			"The DA Registry needs the staff IAM (Keycloak) sign-in, not a DA Services password sign-in"
		)
	csrf = headers.get("X-CSRF-Token") or (
		request.cookies.get("X-CSRF-Token") if request is not None else None
	)
	# The registry only checks that the header and the cookie carry the same value.
	return token, csrf or secrets.token_urlsafe(24)


class HttpDARegistryClient(DARegistryClient):
	# The staff API holds no language or photo: the portal must not offer them.
	editable_profile_fields = frozenset(PROFILE_FIELDS)

	def __init__(
		self,
		base_url: str,
		*,
		keycloak: dict,
		register_id: str = DEFAULT_REGISTER_ID,
		intake_form_id: str = DEFAULT_INTAKE_FORM_ID,
		import_data_model_id: str = DEFAULT_IMPORT_DATA_MODEL_ID,
		timeout: float = 10,
		sender_app: str = "da-services",
		master_data_url: str | None = None,
		auth_mode: str = "service",
	):
		self.auth_mode = auth_mode
		self._user_credentials: tuple[str, str] | None = None
		self.base_url = base_url.rstrip("/")
		self.master_data_url = (master_data_url or "").rstrip("/") or None
		self.keycloak = keycloak
		self.register_id = register_id
		self.intake_form_id = intake_form_id
		self.import_data_model_id = import_data_model_id
		self.timeout = timeout
		self.sender_app = sender_app

	@classmethod
	def from_conf(cls, auth_mode: str | None = None) -> HttpDARegistryClient:
		"""The client for this site. ``auth_mode`` overrides ``da_registry_auth``: a
		scheduled job or a bench command has no signed-in user and needs the service identity."""
		conf = frappe.conf
		base_url = str(conf.get("da_registry_base_url") or "")
		if not base_url.startswith(("http://", "https://")):
			raise DARegistryError("da_registry_base_url must be an http(s) URL in site_config.json")
		auth_mode = str(auth_mode or conf.get("da_registry_auth") or "service").lower()
		if auth_mode not in ("user", "service"):
			raise DARegistryError(f"Unknown da_registry_auth: {auth_mode!r}")
		keycloak = conf.get("da_registry_keycloak") or {}
		if auth_mode == "service":
			# The service account; a staff user's portal sign-in is accepted for dev testing.
			needed = ("token_url", "client_id", "client_secret")
			if not keycloak.get("client_secret") and keycloak.get("username"):
				needed = ("login_url", "username", "password")
			missing = [k for k in needed if not keycloak.get(k)]
			if missing:
				raise DARegistryError(
					f"da_registry_keycloak is missing {', '.join(missing)} in site_config.json"
				)
		return cls(
			base_url,
			keycloak=keycloak,
			register_id=conf.get("da_registry_register_id") or DEFAULT_REGISTER_ID,
			intake_form_id=conf.get("da_registry_intake_form_id") or DEFAULT_INTAKE_FORM_ID,
			import_data_model_id=conf.get("da_registry_import_data_model_id") or DEFAULT_IMPORT_DATA_MODEL_ID,
			timeout=float(conf.get("da_registry_timeout_seconds") or 10),
			sender_app=conf.get("da_registry_sender_app") or "da-services",
			master_data_url=conf.get("da_registry_master_data_url"),
			auth_mode=auth_mode,
		)

	# ------------------------------------------------------------------ reads

	def get_da(self, da_id: str) -> DAReference:
		"""Always read live: a DA's own profile and a single DA view never show a stale copy."""
		internal_id = self._find_internal_id(da_id)
		if not internal_id:
			raise DANotFound(da_id)
		record = self._da_record(internal_id, da_id)
		self._cache_record(internal_id, record)
		return registry_record_to_da_reference(record, self._labels())

	def list_das(self, *, woreda: str | None = None, region: str | None = None) -> list[DAReference]:
		"""Active DAs of a Woreda (or Region), by Master Data P-code.

		The search answers a few display fields only, so each DA's record is read once
		more for its posting and profile: from the short record cache when it is there,
		otherwise several at a time.
		"""
		if woreda:
			filter_by = {"woreda_code": {"eq": woreda}}
		elif region:
			filter_by = {"region_code": {"eq": region}}
		else:
			filter_by = None
		rows = [
			row
			for row in self._search("", filter_by)
			if str(row.get("functional_record_id") or "").startswith("DA-")
		]
		records = {row["internal_record_id"]: self._cached_record(row["internal_record_id"]) for row in rows}
		missing = [row for row in rows if records[row["internal_record_id"]] is None]
		if missing:
			self._credentials()  # resolve the token here: the workers have no request context

			def fetch(row):
				try:
					return self._da_record(row["internal_record_id"], row.get("functional_record_id"))
				except DANotFound:
					# Gone between the search and the read: leave it out, keep the rest of the list.
					return None

			with ThreadPoolExecutor(max_workers=min(LIST_WORKERS, len(missing))) as pool:
				for row, record in zip(missing, pool.map(fetch, missing), strict=True):
					records[row["internal_record_id"]] = record
					if record is not None:
						self._cache_record(row["internal_record_id"], record)
		labels = self._labels()
		return [
			registry_record_to_da_reference(records[row["internal_record_id"]], labels)
			for row in rows
			if records[row["internal_record_id"]] is not None
		]

	# ------------------------------------------------------------------ caches

	def _record_key(self, internal_id: str) -> str:
		return f"da_registry:record:{self.register_id}:{internal_id}"

	def _cached_record(self, internal_id: str) -> dict | None:
		return frappe.cache.get_value(self._record_key(internal_id))

	def _cache_record(self, internal_id: str, record: dict) -> None:
		frappe.cache.set_value(self._record_key(internal_id), record, expires_in_sec=RECORD_CACHE_SECONDS)

	# ------------------------------------------------------------------ Master Data geography

	def geo_levels(self) -> list[dict]:
		rows = self._master_data_pages(
			"/geo/get_all_geo_levels", {}, action="read Master Data geography levels"
		)
		return [
			{
				"level_id": row["level_id"],
				"mnemonic": str(row.get("level_mnemonic") or "").strip().lower(),
				"parent_level_id": row.get("parent_level_id"),
			}
			for row in rows
			if isinstance(row, dict) and row.get("level_id")
		]

	def geo_level_values(self, level_id: str, parent: str | None = None) -> list[dict]:
		rows = self._master_data_pages(
			"/geo/get_geo_level_values",
			{"level_id": level_id, "parent_level_value_id": parent or ""},
			action=f"read Master Data {level_id} values",
		)
		return [
			{
				"code": row["level_value_id"],
				"name": row.get("level_value_mnemonic") or row["level_value_id"],
				"parent": row.get("parent_level_value_id") or None,
			}
			for row in rows
			if isinstance(row, dict) and row.get("level_value_id")
		]

	def geo_level_values_many(self, level_id: str, parents: list[str | None]) -> dict[str | None, list[dict]]:
		# A full tree is one call per Region, Zone and Woreda: fan out like list_das.
		self._credentials()  # resolve the token here: the workers have no request context
		with ThreadPoolExecutor(max_workers=min(LIST_WORKERS, max(1, len(parents)))) as pool:
			areas = pool.map(lambda parent: self.geo_level_values(level_id, parent), parents)
			return dict(zip(parents, areas, strict=True))

	def _master_data_pages(self, path: str, payload: dict, *, action: str) -> list:
		"""Every row of a Master Data list call (the same token as the registry)."""
		if not self.master_data_url:
			raise DARegistryError("da_registry_master_data_url is not set in site_config.json")
		rows: list = []
		page = 1
		while True:
			body = self._post(
				path,
				payload,
				action=action,
				pagination={
					"current_page": page,
					"page_size": PAGE_SIZE,
					"search_text": "",
					"sort_by": "",
					"filter_by": "",
				},
				base=self.master_data_url,
			)
			rows.extend(body.get("response_payload") or [])
			pages = (body.get("pagination_response") or {}).get("number_of_pages") or 1
			if page >= pages:
				return rows
			page += 1

	def _labels(self) -> dict[str, dict]:
		"""Master Data labels of the DA's coded fields, {attribute_id: {value_id: label}}.

		Without da_registry_master_data_url, or while Master Data is down, codes are shown
		as words instead ("SENIOR_DA" -> "Senior da").
		"""
		if not self.master_data_url:
			return {}
		key = f"da_registry:labels:{self.master_data_url}"
		labels = frappe.cache.get_value(key)
		if labels is not None:
			return labels
		labels = {}
		try:
			for attribute_id in LABEL_ATTRIBUTES.values():
				body = self._post(
					"/attributes/get_attribute_values",
					{"attribute_id": attribute_id},
					action=f"read Master Data {attribute_id}",
					pagination={
						"current_page": 1,
						"page_size": 500,
						"search_text": "",
						"sort_by": "",
						"filter_by": "",
					},
					base=self.master_data_url,
				)
				values = (body.get("response_payload") or {}).get("attribute_values") or []
				labels[attribute_id] = {
					v["value_id"]: v.get("value_display") or v["value_id"] for v in values
				}
		except DARegistryError:
			return {}  # not cached: try again on the next read
		frappe.cache.set_value(key, labels, expires_in_sec=LABEL_CACHE_SECONDS)
		return labels

	def _find_internal_id(self, da_id: str) -> str | None:
		# search_text is a partial match on DA-ID, names, Fayda ID, phone and email, and
		# the default lists ACTIVE records only: widen the status, then match exactly.
		statuses = {"record_status": {"in": ["ACTIVE", "INACTIVE", "ARCHIVED"]}}
		for row in self._search(da_id, statuses):
			if row.get("functional_record_id") == da_id:
				return row.get("internal_record_id")
		return None

	def _search(self, search_text: str, filter_by: dict | None) -> Iterator[dict]:
		page = 1
		while True:
			body = self._post(
				"/register-data/search_in_a_register",
				{"register_id": self.register_id},
				pagination={
					"current_page": page,
					"page_size": PAGE_SIZE,
					"search_text": search_text,
					"sort_by": "",
					"filter_by": json.dumps(filter_by) if filter_by else "",
				},
				action="search DAs",
			)
			yield from body.get("response_payload") or []
			pages = (body.get("pagination_response") or {}).get("number_of_pages") or 1
			if page >= pages:
				return
			page += 1

	def _da_record(self, internal_id: str, da_id: str | None) -> dict:
		body = self._post(
			"/register-data/get_tab_records",
			{"subject_register_id": self.register_id, "subject_record_id": internal_id, "tab_id": DA_TAB},
			action="read DA record",
			not_found=da_id,
		)
		for section in body.get("response_payload") or []:
			if section.get("section_register_id") == self.register_id and section.get("records"):
				return section["records"][0]
		raise DANotFound(da_id or internal_id)

	# ------------------------------------------------------------------ commands

	def push_profile_update(self, update: ProfileUpdate) -> IntegrationResult:
		"""Open a registry change request; it applies after the registry's two-stage approval.

		The registry allows one pending change per DA and section, which also stops a
		replay of the same update from opening a second request.
		"""
		try:
			section_id, fields = profile_changes_to_change_request(update.changes)
		except ValueError as exc:
			return IntegrationResult(accepted=False, message=str(exc), retryable=False)

		internal_id = self._find_internal_id(update.da_id)
		if not internal_id:
			return IntegrationResult(accepted=False, message=f"DA {update.da_id} is not in the DA Registry")

		try:
			body = self._post(
				"/change-requests/create_change_request",
				{
					"register_id": self.register_id,
					"tab_id": DA_TAB,
					"section_id": section_id,
					"section_register_id": self.register_id,
					"internal_record_id": internal_id,
					"change_payload": [
						{"internal_record_id": internal_id, "edit_action": "UPDATE", **fields}
					],
				},
				action="create change request",
			)
		except DARegistryUnavailable:
			raise
		except DARegistryError as exc:
			return IntegrationResult(accepted=False, message=str(exc), retryable=False)

		payload = body.get("response_payload") or {}
		if not payload.get("change_request_id"):
			# Without the id the decision can never be read back: better a visible failure.
			return IntegrationResult(
				accepted=False, message="DA Registry gave no change_request_id", retryable=False, raw=payload
			)
		return IntegrationResult(
			accepted=True,
			external_reference=payload["change_request_id"],
			message=f"Registry approval {payload.get('approval_status') or 'PENDING'}",
			raw=payload,
		)

	def push_lifecycle_outcome(self, outcome: LifecycleOutcome) -> IntegrationResult:
		# The registry has no record-status API and no leave / retirement / separation
		# model (ACTIVE / INACTIVE / ARCHIVED only). A transfer is a location change
		# request, which needs the full Master Data geo chain of the new Kebele.
		raise DARegistryContractPending(
			f"The DA Registry has no API for a {outcome.transition_type} outcome yet; "
			"DA Services keeps the lifecycle state."
		)

	def import_agents_file(self, filename: str, content: bytes) -> IntegrationResult:
		"""Upload the file to the registry's document store, then queue it for import.

		Needs the Intake Officer role on the registry (intakeSubmission:edit). The registry
		answers once the file is queued; rows are processed in the background.
		"""
		body = self._post(
			"/documents/upload_documents",
			{},
			action="upload the import file",
			files={"documents": (filename, content)},
			form={"bucket": IMPORT_BUCKET},
		)
		documents = (body.get("response_payload") or {}).get("documents") or []
		document_id = documents[0].get("document_id") if documents else None
		if not document_id:
			raise DARegistryUnavailable("DA Registry stored the import file but returned no document id")

		body = self._post(
			"/input-mechanism-data/enqueue_import_file",
			{
				"document_id": document_id,
				"data_model_id": self.import_data_model_id,
				"register_id": self.register_id,
				"intake_form_id": self.intake_form_id,
			},
			action="queue the import file",
		)
		payload = body.get("response_payload") or {}
		return IntegrationResult(
			accepted=True,
			external_reference=payload.get("import_file_id"),
			message="Queued for import",
			raw={"document_id": document_id, **payload},
		)

	def list_import_exceptions(self, *, page: int = 1, page_size: int = 50) -> dict:
		"""Needs the Integration Manager role on the registry (incomingMessage:view)."""
		body = self._post(
			"/da/search_exceptions",
			{"source": "file_import", "exception_type": None, "state": None},
			action="list import exceptions",
			pagination={"current_page": page, "page_size": page_size},
		)
		return {
			"items": body.get("response_payload") or [],
			"total": (body.get("pagination_response") or {}).get("number_of_items") or 0,
		}

	def change_request_status(self, reference: str) -> str:
		body = self._post(
			"/change-requests/get_change_request",
			{"change_request_id": reference},
			action="read change request",
			not_found=reference,
		)
		status = str((body.get("response_payload") or {}).get("approval_status") or "PENDING").upper()
		return {"APPROVED": "Approved", "REJECTED": "Rejected"}.get(status, "Pending")

	# ------------------------------------------------------------------ transport

	def _post(
		self,
		path: str,
		payload: dict,
		*,
		action: str,
		pagination: dict | None = None,
		not_found: str | None = None,
		files: dict | None = None,
		form: dict | None = None,
		base: str | None = None,
	) -> dict:
		"""POST one staff API call; return response_body. Retries once on an expired token.

		With ``files`` the call is multipart (``form`` fields plus the files) instead of
		the JSON envelope, as the document upload expects. ``base`` sends the call to
		another host on the same token, such as Master Data.
		"""
		envelope = {
			"request_header": {
				"request_id": str(uuid.uuid4()),
				"request_timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
				"sender_app_mnemonic": self.sender_app,
				"sender_app_url": f"https://{self.sender_app}",
			},
			"request_body": {"request_payload": payload},
		}
		if pagination is not None:
			envelope["request_body"]["pagination_request"] = pagination

		url = (base or self.base_url) + path
		for attempt in (1, 2):
			token, csrf = self._credentials()
			# Double submit: a server-to-server call has no browser cookie, so the CSRF value
			# goes in the header and, with the same value, in the cookie.
			headers = {"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf}
			body = {"files": files, "data": form} if files else {"json": envelope}
			if not files:
				headers["Content-Type"] = "application/json"
			try:
				response = requests.post(
					url,
					headers=headers,
					cookies={"X-CSRF-Token": csrf},
					timeout=self.timeout,
					**body,
				)
			except requests.RequestException as exc:
				_log_error(title="DA Registry unreachable", message=f"{action}: {url}: {exc!s}")
				raise DARegistryUnavailable(f"DA Registry unreachable ({action})") from exc
			if response.status_code == 401 and attempt == 1 and self.auth_mode == "service":
				self._drop_token()
				continue
			break

		if self.auth_mode == "user" and response.status_code in (401, 403):
			# The user's own token: expired (sign in again) or without the registry role.
			try:
				message = (response.json().get("response_header") or {}).get("response_error_message") or ""
			except ValueError:
				message = ""
			raise DARegistryAuthError(
				f"DA Registry refused the signed-in user's token ({action}): {message or response.status_code}",
				http_status_code=response.status_code,
			)

		if response.status_code >= 500:
			_log_error(title="DA Registry error", message=f"{action}: HTTP {response.status_code} {url}")
			raise DARegistryUnavailable(f"DA Registry failed to {action} (HTTP {response.status_code})")
		try:
			data = response.json()
		except ValueError:
			data = None
		if response.status_code >= 400:
			# 401/403: token, role or CSRF problem; 413 or an HTML 404 from the gateway.
			# Retrying the same call will not help.
			header = (data or {}).get("response_header") or {}
			message = header.get("response_error_message") or getattr(response, "reason", "") or ""
			_log_error(
				title="DA Registry rejected request",
				message=f"{action}: HTTP {response.status_code} {url}\n{message[:300]}",
			)
			raise DARegistryError(f"DA Registry refused to {action} (HTTP {response.status_code}): {message}")
		if data is None:
			raise DARegistryUnavailable(f"DA Registry answered {action} with non-JSON")

		header = data.get("response_header") or {}
		message = header.get("response_error_message") or ""
		if header.get("response_status") != "SUCCESS":
			if not_found and any(m in message.lower() for m in _NOT_FOUND_MARKERS):
				raise DANotFound(not_found)
			raise DARegistryError(f"DA Registry refused to {action}: {message or 'no reason given'}")
		return data.get("response_body") or {}

	# ------------------------------------------------------------------ auth

	def _credentials(self) -> tuple[str, str]:
		"""(bearer token, CSRF value) for the next registry call.

		The one place that decides whose identity reaches the registry: the signed-in
		user's own staff IAM token ("user", read once per request) or the service account
		("service", with a fresh random CSRF pair per call).
		"""
		if self.auth_mode == "user":
			if self._user_credentials is None:
				self._user_credentials = _request_credentials()
			return self._user_credentials
		return self._token(), secrets.token_urlsafe(24)

	@property
	def _staff_login(self) -> bool:
		"""A staff user (dev testing) instead of the service account."""
		return not self.keycloak.get("client_secret") and bool(self.keycloak.get("username"))

	def _cache_key(self) -> tuple[str, str]:
		if self._staff_login:
			return (str(self.keycloak["login_url"]), str(self.keycloak["username"]))
		return (str(self.keycloak["token_url"]), str(self.keycloak["client_id"]))

	def _drop_token(self) -> None:
		_TOKEN_CACHE.pop(self._cache_key(), None)

	def _token(self) -> str:
		"""Bearer token for the staff API, cached on the worker until shortly before expiry."""
		key = self._cache_key()
		secret = self.keycloak.get("password") if self._staff_login else self.keycloak.get("client_secret")
		secret_mark = hashlib.sha256(str(secret).encode()).hexdigest()

		def cached() -> str | None:
			entry = _TOKEN_CACHE.get(key) or {}
			if entry.get("secret_mark") == secret_mark and entry.get("expires_at", 0) > time.time():
				return entry["token"]
			return None

		if token := cached():
			return token
		with _TOKEN_LOCK:
			if token := cached():
				return token
			fetch = self._fetch_staff_token if self._staff_login else self._fetch_client_credentials_token
			token, expires_at = fetch()
			_TOKEN_CACHE[key] = {
				"token": token,
				"secret_mark": secret_mark,
				"expires_at": expires_at - _TOKEN_SKEW_SECONDS,
			}
			return token

	def _fetch_client_credentials_token(self) -> tuple[str, float]:
		token_url = self.keycloak["token_url"]
		try:
			resp = requests.post(
				token_url,
				data={
					"grant_type": "client_credentials",
					"client_id": self.keycloak["client_id"],
					"client_secret": self.keycloak["client_secret"],
				},
				timeout=self.timeout,
			)
		except requests.RequestException as exc:
			_log_error(
				title="DA Registry auth error", message=f"Keycloak unreachable at {token_url}: {exc!s}"
			)
			raise DARegistryUnavailable("Could not reach Keycloak for a DA Registry token") from exc
		if resp.status_code >= 400:
			# Do not log the body: a token endpoint can echo the client or the token.
			_log_error(
				title="DA Registry auth error",
				message=f"Keycloak token request failed: HTTP {resp.status_code} {token_url}",
			)
			exc_type = DARegistryUnavailable if resp.status_code >= 500 else DARegistryError
			raise exc_type(f"Keycloak refused the DA Registry token request (HTTP {resp.status_code})")
		try:
			data = resp.json()
			token = data["access_token"]
			expires_in = int(data.get("expires_in") or 300)
		except (ValueError, KeyError, TypeError) as exc:
			raise DARegistryUnavailable("Keycloak token response had no access_token") from exc
		return token, time.time() + max(expires_in, 0)

	def _fetch_staff_token(self) -> tuple[str, float]:
		"""Staff portal sign-in: login page -> Keycloak form -> IAM callback cookie.

		For dev testing until the service account exists. The password is posted only to
		the Keycloak login action the portal redirected to; the token is the callback's
		X-Access-Token cookie. A Keycloak required action (profile, password, OTP) cannot
		be completed here and is reported as a configuration error.
		"""
		login_url = self.keycloak["login_url"]
		session = requests.Session()
		try:
			page = session.get(login_url, timeout=self.timeout)
			form = _LOGIN_FORM_RE.search(page.text or "")
			action = _FORM_ACTION_RE.search(form.group(0)) if form else None
			action_url = html.unescape(action.group(1)) if action else ""
			if "/login-actions/authenticate" not in action_url:
				raise DARegistryUnavailable("DA Registry staff login page had no Keycloak login form")
			signed_in = session.post(
				action_url,
				data={
					"username": self.keycloak["username"],
					"password": self.keycloak["password"],
					"credentialId": "",
				},
				allow_redirects=False,
				timeout=self.timeout,
			)
			callback = signed_in.headers.get("Location") or ""
			if signed_in.status_code != 302 or not callback:
				raise DARegistryError(
					"Keycloak refused the DA Registry staff login (check username and password)"
				)
			if "/login-actions/required-action" in callback:
				raise DARegistryError(
					"Keycloak asks the DA Registry staff user to complete a required action "
					"(profile, password or OTP); finish it in Keycloak first"
				)
			session.get(callback, allow_redirects=False, timeout=self.timeout)
		except requests.RequestException as exc:
			_log_error(title="DA Registry auth error", message=f"Staff login failed at {login_url}: {exc!s}")
			raise DARegistryUnavailable("Could not reach the DA Registry staff login") from exc

		token = session.cookies.get("X-Access-Token")
		if not token:
			raise DARegistryUnavailable("DA Registry staff login returned no access token")
		return token, _jwt_expires_at(token) or time.time() + 300


def _jwt_claims(token: str) -> dict | None:
	"""The claims of a JWT, without verifying it (the registry verifies)."""
	try:
		payload = token.split(".")[1]
		claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
	except IndexError, ValueError, TypeError:
		return None
	return claims if isinstance(claims, dict) else None


def _jwt_expires_at(token: str) -> float | None:
	try:
		return float((_jwt_claims(token) or {})["exp"])
	except KeyError, TypeError, ValueError:
		return None
