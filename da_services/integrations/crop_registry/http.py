"""HTTP client for the verified Crop Sown Staff read calls.

Staff calls are POST with the OpenG2P envelope. Auth, CSRF, and the token
cache come from the shared staff helper. Register discovery, search, and
field mapping stay in this module.
"""

from __future__ import annotations

from datetime import UTC, datetime

import requests

from da_services.integrations.staff_api.auth import apply_bearer_and_csrf, auth_and_csrf_headers
from da_services.integrations.staff_api.transport import http_failure, staff_envelope

from .client import CropRegistryClient, CropRegistryClientError, CropRegistryError, CropRegistryServerError
from .mapper import submission_to_crop
from .schemas import CropSubmission

_MAX_SEARCH_PAGES = 20
_IAM_KEYS = ("iam_base_url", "kc_realm_url", "username", "password")
_CC_KEYS = ("token_url", "client_id", "client_secret")


def _row_matches_farmer(row: dict, farmer_id: str) -> bool:
	"""True when a search row is the farmer's Crop Sown submission or register record."""
	wanted = str(farmer_id)
	for key in ("record_name", "functional_record_id", "link_internal_record_id"):
		if str(row.get(key) or "") == wanted:
			return True
	for field in row.get("display_fields") or []:
		if isinstance(field, dict) and str(field.get("value") or "") == wanted:
			return True
	return False


def _page_count(pagination: dict) -> int:
	try:
		count = int(pagination.get("number_of_pages") or 1)
	except (TypeError, ValueError):
		return 1
	return count if count > 0 else 1


def _log(title: str, message: str) -> None:
	try:
		import frappe
	except ImportError:
		return
	frappe.log_error(title=title, message=message)


class HTTPCropRegistryClient(CropRegistryClient):
	"""Live read client. Discovery results are cached on the instance."""

	def __init__(
		self,
		*,
		staff_url: str,
		keycloak: dict | None = None,
		api_key: str | None = None,
		register_mnemonic: str = "CropSown",
		form_name_contains: str = "Planning",
		sender_app: str = "OAN_REGISTRY_API_TEST",
		registry_client_id: str = "cropsown-registry-staff-portal",
		timeout: int = 30,
		tokens=None,
	):
		self.staff_url = staff_url.rstrip("/")
		self.keycloak = keycloak or {}
		self.api_key = api_key
		self.register_mnemonic = register_mnemonic
		self.form_name_contains = form_name_contains
		self.sender_app = sender_app
		self.registry_client_id = registry_client_id
		self.timeout = timeout
		self.headers = {"Content-Type": "application/json", "Accept": "application/json"}
		if api_key:
			self.headers["Authorization"] = f"Bearer {api_key}"
		self._tokens = tokens
		self._register_id: str | None = None
		self._form_id: str | None = None
		self._form: dict | None = None
		self._validate_config()

	def _has_staff_login(self) -> bool:
		return all(self.keycloak.get(key) for key in _IAM_KEYS)

	def _validate_config(self) -> None:
		problems = []
		if not self.staff_url.startswith(("http://", "https://")):
			problems.append("crop_registry_staff_url must be an http(s) URL")
		cc_set = [key for key in _CC_KEYS if self.keycloak.get(key)]
		if cc_set and len(cc_set) != len(_CC_KEYS) and not self._has_staff_login():
			problems.append(
				"crop_registry_keycloak needs token_url, client_id and client_secret "
				f"(found only: {', '.join(cc_set)})"
			)
		if not self._has_staff_login() and not cc_set and not self.api_key and self._tokens is None:
			problems.append(
				"set the staff username and password on crop_registry_keycloak "
				"(or farmer_registry_keycloak), or a client-credentials block, or crop_registry_api_key"
			)
		if not self.register_mnemonic:
			problems.append("crop_registry_register_mnemonic is required")
		if problems:
			raise CropRegistryError("Crop Sown HTTP client misconfigured: " + "; ".join(problems))

	def _auth_and_csrf_headers(self) -> tuple[dict, dict]:
		"""Bearer plus a CSRF header that matches the cookie on the staff POST.

		A test token skips the live login. Otherwise this is the shared staff
		login: IAM callback cookie when a username is configured, otherwise
		client-credentials or the static API key, each with a matching CSRF pair.
		"""
		headers = dict(self.headers)
		if self._tokens is not None:
			return apply_bearer_and_csrf(headers, self._tokens.get_token())
		return auth_and_csrf_headers(
			keycloak=self.keycloak,
			timeout=self.timeout,
			headers=headers,
			client_error=CropRegistryClientError,
			server_error=CropRegistryServerError,
			log_title="Crop Sown Registry Auth Error",
			registry_client_id=self.registry_client_id or None,
		)

	def _envelope(
		self, payload: dict, *, page: int = 1, filter_by: dict | None = None, search_text: str = ""
	) -> dict:
		return staff_envelope(
			payload,
			sender_app=self.sender_app,
			timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
			page=page,
			page_size=100,
			filter_by=filter_by,
			search_text=search_text,
			include_instance_id=False,
			include_sort_by=False,
			empty_filter={},
		)

	def _post(
		self,
		path: str,
		payload: dict,
		*,
		action: str,
		page: int = 1,
		filter_by: dict | None = None,
		search_text: str = "",
	):
		_payload, _pagination = self._post_page(
			path, payload, action=action, page=page, filter_by=filter_by, search_text=search_text
		)
		return _payload

	def _post_page(
		self,
		path: str,
		payload: dict,
		*,
		action: str,
		page: int = 1,
		filter_by: dict | None = None,
		search_text: str = "",
	) -> tuple[object, dict]:
		url = f"{self.staff_url}{path}"
		headers, cookies = self._auth_and_csrf_headers()
		try:
			response = requests.post(
				url,
				headers=headers,
				cookies=cookies,
				json=self._envelope(payload, page=page, filter_by=filter_by, search_text=search_text),
				timeout=self.timeout,
			)
		except requests.RequestException as exc:
			_log("Crop Sown Registry HTTP Error", f"Could not reach {url} ({action}): {exc}")
			raise CropRegistryServerError(f"Crop Sown Registry unreachable: {exc}") from exc

		self._raise_for_status(response, action)
		try:
			body = response.json()
		except ValueError as exc:
			raise CropRegistryServerError(f"Crop Sown Registry returned non-JSON for {action}") from exc
		if not isinstance(body, dict):
			raise CropRegistryServerError(f"Crop Sown Registry returned a non-object for {action}")

		header = body.get("response_header") or {}
		status = header.get("response_status")
		if status != "SUCCESS":
			message = (
				header.get("response_error_message") or header.get("response_error_code") or "unknown error"
			)
			raise CropRegistryClientError(f"Crop Sown Registry rejected {action}: {message}", status_code=200)
		response_body = body.get("response_body") or {}
		pagination = response_body.get("pagination_response") or {}
		return response_body.get("response_payload"), pagination if isinstance(pagination, dict) else {}

	@staticmethod
	def _raise_for_status(response: requests.Response, action: str) -> None:
		failure = http_failure(response)
		if failure is None:
			return
		kind, code, snippet = failure
		if kind == "client":
			_log("Crop Sown Registry rejected request", f"{action}: HTTP {code} {response.url}\n{snippet}")
			raise CropRegistryClientError(
				f"Crop Sown Registry rejected {action} (HTTP {code}): {snippet}",
				status_code=code,
			)
		_log("Crop Sown Registry server error", f"{action}: HTTP {code} {response.url}\n{snippet}")
		raise CropRegistryServerError(
			f"Crop Sown Registry failed during {action} (HTTP {code})",
			status_code=code,
		)

	def _discover_register_id(self) -> str:
		if self._register_id:
			return self._register_id

		payload = self._post("/register-metadata/get_all_registers", {}, action="discover crop register")
		matches = [
			row
			for row in (payload or [])
			if isinstance(row, dict) and row.get("register_mnemonic") == self.register_mnemonic
		]
		if len(matches) != 1 or not matches[0].get("register_id"):
			raise CropRegistryClientError("Selected Crop Sown register is missing or ambiguous")
		self._register_id = str(matches[0]["register_id"])
		return self._register_id

	def _rendered_form(self) -> dict:
		if self._form is not None:
			return self._form
		register_id = self._discover_register_id()
		forms_payload = self._post(
			"/intake-form-metadata/get_all_intake_forms",
			{"register_id": register_id},
			action="discover crop intake form",
		)
		selector = (self.form_name_contains or "").lower()
		forms = []
		for form in forms_payload or []:
			if not isinstance(form, dict) or form.get("register_id") != register_id:
				continue
			mnemonic = str(form.get("form_mnemonic") or "").lower()
			if selector and selector not in mnemonic:
				continue
			forms.append(form)
		if len(forms) != 1 or not forms[0].get("form_id"):
			raise CropRegistryClientError("Selected Crop Sown intake form is missing or ambiguous")
		self._form_id = str(forms[0]["form_id"])
		rendered = self._post(
			"/intake-form-metadata/render_intake_form",
			{"form_id": forms[0]["form_id"]},
			action="render crop intake form",
		)
		if not isinstance(rendered, dict) or not rendered.get("tabs"):
			raise CropRegistryClientError("Rendered Crop Sown form has no tabs")
		self._form = rendered
		return rendered

	def get_submission(self, submission_id: str) -> CropSubmission | None:
		if not submission_id:
			raise CropRegistryClientError("submission_id is required")
		payload = self._post(
			"/intake-form-data/get_intake_form_submission",
			{"submission_id": submission_id},
			action="read crop submission",
		)
		if not isinstance(payload, dict) or not payload.get("submission_id"):
			return None
		return submission_to_crop(payload)

	def get_tabs(self, submission_id: str) -> list[dict]:
		if not submission_id:
			raise CropRegistryClientError("submission_id is required")
		tabs = []
		for tab in self._rendered_form().get("tabs") or []:
			if not isinstance(tab, dict) or not tab.get("tab_id"):
				continue
			tab_id = str(tab["tab_id"])
			records = self._post(
				"/intake-form-data/get_tab_records",
				{"submission_id": submission_id, "tab_id": tab_id},
				action="read crop tab",
			)
			if not isinstance(records, list):
				raise CropRegistryClientError("Crop Sown tab records were not a list")
			tabs.append({"tab_id": tab_id, "records": records})
		if not tabs:
			raise CropRegistryClientError("Rendered Crop Sown form has no readable tabs")
		return tabs

	def list_for_farmer(self, farmer_id: str) -> list[dict]:
		"""Find Crop Sown rows for a Farmer-ID.

		The staff form submission screen shows that id as the submission Name
		(``record_name``). Register rows may instead point at the farmer through
		``link_internal_record_id`` or ``functional_record_id``. Both searches
		are in the Crop Sown collection.
		"""
		if not farmer_id:
			raise CropRegistryClientError("farmer_id is required")
		register_id = self._discover_register_id()
		found: list[dict] = []
		seen: set[str] = set()
		for path, action in (
			("/intake-form-data/search_in_intake_form_submissions", "search crop submissions"),
			("/register-data/search_in_a_register", "search crop register"),
		):
			for row in self._search_pages(path, {"register_id": register_id}, action=action, search_text=farmer_id):
				if not _row_matches_farmer(row, farmer_id):
					continue
				key = str(row.get("submission_id") or row.get("internal_record_id") or "")
				if key and key in seen:
					continue
				if key:
					seen.add(key)
				found.append(row)
		return found

	def _search_pages(self, path: str, payload: dict, *, action: str, search_text: str) -> list[dict]:
		rows: list[dict] = []
		page = 1
		page_count = 1
		while page <= page_count and page <= _MAX_SEARCH_PAGES:
			payload_page, pagination = self._post_page(
				path, payload, action=action, page=page, search_text=search_text
			)
			if isinstance(payload_page, list):
				rows.extend(row for row in payload_page if isinstance(row, dict))
			elif not payload_page:
				break
			page_count = _page_count(pagination)
			page += 1
		return rows

	def save_section(
		self,
		*,
		section_id: str,
		section_register_id: str,
		section_payload: list[dict],
		submission_id: str | None = None,
		form_id: str | None = None,
		register_id: str | None = None,
	) -> dict:
		"""POST /intake-form-data/save_intake_form_submission for one section."""
		if not section_id or not section_register_id:
			raise CropRegistryClientError("section_id and section_register_id are required")
		if not isinstance(section_payload, list) or not section_payload:
			raise CropRegistryClientError("section_payload must be a non-empty list of records")
		if any(not isinstance(row, dict) for row in section_payload):
			raise CropRegistryClientError("section_payload must be a list of records")
		resolved_register = register_id or self._discover_register_id()
		resolved_form = form_id or self._form_id
		if not resolved_form:
			self._rendered_form()
			resolved_form = self._form_id
		if not resolved_form:
			raise CropRegistryClientError("Selected Crop Sown intake form is missing or ambiguous")
		saved = self._post(
			"/intake-form-data/save_intake_form_submission",
			{
				"submission_id": submission_id or None,
				"section_id": section_id,
				"section_payload": section_payload,
				"section_register_id": section_register_id,
				"form_id": resolved_form,
				"register_id": resolved_register,
			},
			action="save crop section",
		)
		if not isinstance(saved, dict) or not saved.get("submission_id"):
			raise CropRegistryClientError("Crop Sown save did not return a submission_id")
		return saved

	def finalize_submission(self, submission_id: str) -> dict:
		"""POST /intake-form-data/finalize_intake_form_submission."""
		if not submission_id:
			raise CropRegistryClientError("submission_id is required")
		finalized = self._post(
			"/intake-form-data/finalize_intake_form_submission",
			{"submission_id": submission_id},
			action="finalize crop submission",
		)
		if not isinstance(finalized, dict) or not finalized.get("submission_id"):
			raise CropRegistryClientError("Crop Sown finalize did not return a submission_id")
		return finalized
