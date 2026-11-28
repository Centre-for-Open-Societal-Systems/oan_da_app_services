"""HTTP client for the verified Crop Sown Staff read calls.

Staff calls are POST with the OpenG2P envelope. Auth is the caller's SSO
bearer and CSRF (or a test token provider). ``register_id`` comes from
DA Services Settings via the factory; search and field mapping stay here.
"""

from __future__ import annotations

from datetime import UTC, datetime

import frappe
import requests

from da_services.integrations.staff_api.transport import http_failure, staff_envelope

from .client import (
	CropRegistryClient,
	CropRegistryClientError,
	CropRegistryServerError,
)
from .intake import describe_form, form_selector, section_index, submit_with_metadata
from .mapper import submission_to_crop
from .schemas import CropSubmission

_MAX_SEARCH_PAGES = 20
_DEFAULT_SENDER_APP = "OAN_REGISTRY_API_TEST"
_DEFAULT_FORM_NAME_CONTAINS = "Planning"
_DEFAULT_TIMEOUT = 30


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
	except TypeError, ValueError:
		return 1
	return count if count > 0 else 1


def _log(title: str, message: str) -> None:
	frappe.log_error(title=title, message=message)


class HTTPCropRegistryClient(CropRegistryClient):
	"""Live read client. Intake-form metadata is cached on the instance."""

	def __init__(
		self,
		*,
		staff_url: str,
		register_id: str,
		form_name_contains: str = _DEFAULT_FORM_NAME_CONTAINS,
		sender_app: str = _DEFAULT_SENDER_APP,
		timeout: int = _DEFAULT_TIMEOUT,
		tokens=None,
		master_data_url: str | None = None,
	):
		self.staff_url = staff_url.rstrip("/")
		self.form_name_contains = form_name_contains
		self.sender_app = sender_app
		self.timeout = timeout
		self.headers = {"Content-Type": "application/json", "Accept": "application/json"}
		self._tokens = tokens
		self.master_data_url = master_data_url.rstrip("/") if master_data_url else None
		self._register_id = register_id.strip()
		self._form_id: str | None = None
		self._form: dict | None = None
		self._forms: dict[str, dict] = {}
		self._intake_form_rows: list[dict] | None = None
		self._fields: dict[str, list[dict]] = {}

	def _auth_and_csrf_headers(self) -> tuple[dict[str, str], dict[str, str]]:
		"""Headers + cookies for a staff POST.

		CSRF always comes from the FE request ``X-CSRF-Token`` (staff rejects a
		self-generated value). A test token provider may override the bearer;
		otherwise Authorization is also forwarded from the request.
		"""
		headers = dict(self.headers)
		csrf = frappe.get_request_header("X-CSRF-Token")
		if self._tokens is not None:
			auth = f"Bearer {self._tokens.get_token()}"
		else:
			auth = frappe.get_request_header("Authorization")
		if not auth or not csrf:
			raise CropRegistryClientError("Missing Authorization or X-CSRF-Token header", status_code=401)

		headers.setdefault("Content-Type", "application/json")
		headers.setdefault("Accept", "application/json")
		headers["Authorization"] = auth
		headers["X-CSRF-Token"] = csrf
		return headers, {"X-CSRF-Token": csrf}

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
		base: str | None = None,
	):
		_payload, _pagination = self._post_page(
			path,
			payload,
			action=action,
			page=page,
			filter_by=filter_by,
			search_text=search_text,
			base=base,
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
		base: str | None = None,
	) -> tuple[object, dict]:
		url = f"{base or self.staff_url}{path}"
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

	def _intake_forms(self) -> list[dict]:
		"""Every intake form on the CropSown register. One staff call per client."""
		if self._intake_form_rows is not None:
			return self._intake_form_rows
		forms_payload = self._post(
			"/intake-form-metadata/get_all_intake_forms",
			{"register_id": self._register_id},
			action="discover crop intake form",
		)
		rows = []
		for form in forms_payload or []:
			if (
				not isinstance(form, dict)
				or form.get("register_id") != self._register_id
				or not form.get("form_id")
			):
				continue
			rows.append(form)
		self._intake_form_rows = rows
		return rows

	def _rendered_form(self, form_name: str | None = None) -> dict:
		selector = form_selector(form_name, self.form_name_contains)
		cached = self._forms.get(selector)
		if cached is not None:
			self._form = cached
			self._form_id = str(cached.get("form_id") or "") or self._form_id
			return cached
		forms = [
			form for form in self._intake_forms() if selector in str(form.get("form_mnemonic") or "").lower()
		]
		if len(forms) != 1:
			raise CropRegistryClientError("Selected Crop Sown intake form is missing or ambiguous")
		rendered = self._post(
			"/intake-form-metadata/render_intake_form",
			{"form_id": forms[0]["form_id"]},
			action="render crop intake form",
		)
		if not isinstance(rendered, dict) or not rendered.get("tabs"):
			raise CropRegistryClientError("Rendered Crop Sown form has no tabs")
		self._form_id = str(forms[0]["form_id"])
		self._form = rendered
		self._forms[selector] = rendered
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

	def list_for_farmer(self, farmer_id: str, *, reg_id: str | None = None, source: str) -> list[dict]:
		"""Find Crop Sown rows for one farmer.

		``source`` selects one staff search: submissions (``record_name`` =
		Farmer-ID) or register rows (internal farmer id). One path only.
		"""
		if not farmer_id:
			raise CropRegistryClientError("farmer_id is required")
		submission_text = (reg_id or "").strip() or farmer_id
		owned = {farmer_id, submission_text}
		# Known staff search paths only; unknown source fails here.
		if source == "submission":
			path, action, search_text = (
				"/intake-form-data/search_in_intake_form_submissions",
				"search crop submissions",
				submission_text,
			)
		elif source == "register":
			path, action, search_text = (
				"/register-data/search_in_a_register",
				"search crop register",
				farmer_id,
			)
		else:
			raise CropRegistryClientError(f"unsupported Crop Sown list source: {source}")
		found: list[dict] = []
		seen: set[str] = set()
		for row in self._search_pages(
			path, {"register_id": self._register_id}, action=action, search_text=search_text
		):
			if not any(_row_matches_farmer(row, candidate) for candidate in owned):
				continue
			key = str(row.get("submission_id") or row.get("internal_record_id") or "")
			if key and key in seen:
				continue
			if key:
				seen.add(key)
			found.append(dict(row))
		return found

	def get_register_tab(self, internal_record_id: str, tab_id: str) -> dict:
		"""One Crop Sown register tab (single staff round trip)."""
		record_id = (internal_record_id or "").strip()
		wanted_tab = (tab_id or "").strip()
		if not record_id:
			raise CropRegistryClientError("internal_record_id is required")
		if not wanted_tab:
			raise CropRegistryClientError("tab_id is required")
		records = self._post(
			"/register-data/get_tab_records",
			{
				"subject_register_id": self._register_id,
				"subject_record_id": record_id,
				"tab_id": wanted_tab,
			},
			action="read crop register tab",
		)
		if not isinstance(records, list):
			raise CropRegistryClientError("Crop Sown register tab records were not a list")
		return {
			"internal_record_id": record_id,
			"register_id": self._register_id,
			"tab_id": wanted_tab,
			"tabs": [{"tab_id": wanted_tab, "records": records}],
		}

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
		resolved_register = register_id or self._register_id
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

	def describe_intake_form(self, *, form_name: str | None = None) -> dict:
		form = self._rendered_form(form_name)
		sections = section_index(form)
		fields = {
			section["section_register_id"]: self._register_fields(section["section_register_id"])
			for section in sections
		}
		described = describe_form(form, fields, self._catalogs(sections))
		form_id = str(form.get("form_id") or self._form_id or "")
		described["intake_forms"] = [
			{"form_id": str(row["form_id"]), "form_mnemonic": row.get("form_mnemonic")}
			for row in self._intake_forms()
			if str(row.get("form_id") or "") == form_id
		]
		return described

	def submit_sections(
		self,
		requests: list[dict],
		*,
		submission_id: str | None = None,
		subject_record_id: str | None = None,
		form_name: str | None = None,
	) -> dict:
		form = self._rendered_form(form_name)
		sections = section_index(form)
		return submit_with_metadata(
			form=form,
			fields_for=self._register_fields,
			catalogs=self._catalogs(sections),
			requests=requests,
			submission_id=submission_id,
			subject_record_id=subject_record_id,
			save=self.save_section,
		)

	def _register_fields(self, register_id: str) -> list[dict]:
		cached = self._fields.get(register_id)
		if cached is not None:
			return cached
		payload = self._post(
			"/register-metadata/get_register_fields",
			{"register_id": register_id},
			action="read crop field definitions",
		)
		fields = payload.get("fields") if isinstance(payload, dict) else None
		if not isinstance(fields, list):
			raise CropRegistryClientError("Crop Sown field definitions were not a list")
		self._fields[register_id] = fields
		return fields

	def _catalogs(self, sections: list[dict]) -> dict[str, list]:
		if not self.master_data_url:
			return {}
		attribute_ids: set[str] = set()
		for section in sections:
			for widget in section["widgets"].values():
				source = widget.get("widget-data-source") or {}
				if source.get("options"):
					continue
				attribute_id = (source.get("params") or {}).get("attribute_id")
				if attribute_id:
					attribute_ids.add(str(attribute_id))
		return {attribute_id: self._attribute_values(attribute_id) for attribute_id in attribute_ids}

	def _attribute_values(self, attribute_id: str) -> list:
		"""Every page of one attribute. Page size is 100. Commodity and variety need more than one."""
		rows: list = []
		page = 1
		page_count = 1
		while page <= page_count and page <= _MAX_SEARCH_PAGES:
			payload, pagination = self._post_page(
				"/attributes/get_attribute_values",
				{"attribute_id": attribute_id},
				action="read crop code list",
				page=page,
				base=self.master_data_url,
			)
			values = payload.get("attribute_values") if isinstance(payload, dict) else None
			if not isinstance(values, list):
				raise CropRegistryClientError("Crop Sown code list was not a list")
			rows.extend(values)
			page_count = _page_count(pagination)
			page += 1
		return rows
