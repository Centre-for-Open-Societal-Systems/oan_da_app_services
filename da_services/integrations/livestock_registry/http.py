"""HTTP client for the Livestock staff API.

Staff calls are POST with the OpenG2P envelope. Like Crop Sown, the client
forwards the caller's SSO ``Authorization`` and ``X-CSRF-Token`` headers; a
token provider replaces them in tests. Discovery and metadata are cached on
the instance.
"""

from __future__ import annotations

from datetime import UTC, datetime

import frappe
import requests

from da_services.integrations.staff_api.auth import apply_bearer_and_csrf
from da_services.integrations.staff_api.transport import http_failure, staff_envelope

from .client import (
	LivestockRegistryClient,
	LivestockRegistryClientError,
	LivestockRegistryError,
	LivestockRegistryServerError,
)
from .intake import describe_form, section_index, submit_with_metadata
from .mapper import submission_to_livestock
from .schemas import LivestockSubmission

_MAX_PAGES = 20


def _row_matches_farmer(row: dict, farmer_id: str) -> bool:
	"""True when a search row is this farmer's Livestock submission or register row.

	Submissions are named with the Farmer-ID; register rows show it in the
	``farmer_id`` display field.
	"""
	if str(row.get("record_name") or "") == farmer_id:
		return True
	for field in row.get("display_fields") or []:
		if isinstance(field, dict) and field.get("field_name") == "farmer_id":
			if str(field.get("value") or "") == farmer_id:
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


class HTTPLivestockRegistryClient(LivestockRegistryClient):
	"""Live client. Register, form, field and catalog reads are cached on the instance."""

	def __init__(
		self,
		*,
		staff_url: str,
		keycloak: dict | None = None,
		api_key: str | None = None,
		register_mnemonic: str = "Livestock",
		register_id: str | None = None,
		form_name_contains: str = "Livestock Intake",
		sender_app: str = "OAN_REGISTRY_API_TEST",
		timeout: int = 30,
		tokens=None,
		master_data_url: str | None = None,
	):
		self.staff_url = staff_url.rstrip("/")
		self.keycloak = keycloak or {}
		self.register_mnemonic = register_mnemonic
		self.form_name_contains = form_name_contains
		self.sender_app = sender_app
		self.timeout = timeout
		self.headers = {"Content-Type": "application/json", "Accept": "application/json"}
		if api_key:
			self.headers["Authorization"] = f"Bearer {api_key}"
		self._tokens = tokens
		self.master_data_url = master_data_url.rstrip("/") if master_data_url else None
		self._register_id: str | None = (register_id or "").strip() or None
		self._registers: list[dict] | None = None
		self._form: dict | None = None
		self._fields: dict[str, list[dict]] = {}
		self._catalog_rows: dict[str, list] | None = None
		self._validate_config()

	def _validate_config(self) -> None:
		problems = []
		if not self.staff_url.startswith(("http://", "https://")):
			problems.append("livestock_registry_staff_url must be an http(s) URL")
		if not self.register_mnemonic:
			problems.append("livestock_registry_register_mnemonic is required")
		if not self.form_name_contains:
			problems.append("livestock_registry_form_name_contains is required")
		if problems:
			raise LivestockRegistryError("Livestock HTTP client misconfigured: " + "; ".join(problems))

	def _auth_and_csrf_headers(self) -> tuple[dict[str, str], dict[str, str]]:
		"""Headers + cookies for a staff POST.

		A token provider (tests) wins. Otherwise the Authorization and
		X-CSRF-Token headers of the current request are forwarded; a missing
		one is a 401 client error.
		"""
		headers = dict(self.headers)
		if self._tokens is not None:
			return apply_bearer_and_csrf(headers, self._tokens.get_token())

		token = frappe.get_request_header("Authorization")
		csrf = frappe.get_request_header("X-CSRF-Token")
		if not token or not csrf:
			raise LivestockRegistryClientError(
				"Missing Authorization or X-CSRF-Token header", status_code=401
			)
		headers["Authorization"] = token
		headers["X-CSRF-Token"] = csrf
		return headers, {"X-CSRF-Token": csrf}

	def _post(self, path: str, payload: dict, *, action: str, base: str | None = None):
		body, _pagination = self._post_page(path, payload, action=action, base=base)
		return body

	def _post_page(
		self,
		path: str,
		payload: dict,
		*,
		action: str,
		page: int = 1,
		search_text: str = "",
		base: str | None = None,
	) -> tuple[object, dict]:
		url = f"{base or self.staff_url}{path}"
		headers, cookies = self._auth_and_csrf_headers()
		envelope = staff_envelope(
			payload,
			sender_app=self.sender_app,
			timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
			page=page,
			page_size=100,
			search_text=search_text,
			include_instance_id=False,
			include_sort_by=False,
			empty_filter={},
		)
		try:
			response = requests.post(
				url, headers=headers, cookies=cookies, json=envelope, timeout=self.timeout
			)
		except requests.RequestException as exc:
			_log("Livestock Registry HTTP Error", f"Could not reach {url} ({action}): {exc}")
			raise LivestockRegistryServerError(f"Livestock Registry unreachable: {exc}") from exc

		failure = http_failure(response)
		if failure is not None:
			kind, code, snippet = failure
			_log("Livestock Registry error", f"{action}: HTTP {code} {response.url}\n{snippet}")
			if kind == "client":
				raise LivestockRegistryClientError(
					f"Livestock Registry rejected {action} (HTTP {code}): {snippet}", status_code=code
				)
			raise LivestockRegistryServerError(
				f"Livestock Registry failed during {action} (HTTP {code})", status_code=code
			)
		try:
			body = response.json()
		except ValueError as exc:
			raise LivestockRegistryServerError(f"Livestock Registry returned non-JSON for {action}") from exc
		if not isinstance(body, dict):
			raise LivestockRegistryServerError(f"Livestock Registry returned a non-object for {action}")
		header = body.get("response_header") or {}
		if header.get("response_status") != "SUCCESS":
			message = (
				header.get("response_error_message") or header.get("response_error_code") or "unknown error"
			)
			raise LivestockRegistryClientError(
				f"Livestock Registry rejected {action}: {message}", status_code=200
			)
		response_body = body.get("response_body") or {}
		pagination = response_body.get("pagination_response") or {}
		return response_body.get("response_payload"), pagination if isinstance(pagination, dict) else {}

	def _pages(
		self, path: str, payload: dict, *, action: str, search_text: str = "", base: str | None = None
	):
		"""Yield (payload, pagination) for every page, up to _MAX_PAGES."""
		page = 1
		page_count = 1
		while page <= page_count and page <= _MAX_PAGES:
			body, pagination = self._post_page(
				path, payload, action=action, page=page, search_text=search_text, base=base
			)
			yield body
			page_count = _page_count(pagination)
			page += 1

	# ------------------------------------------------------------------ discovery

	def _all_registers(self) -> list[dict]:
		"""Every register on the livestock staff host. One call per client."""
		if self._registers is not None:
			return self._registers
		payload = self._post("/register-metadata/get_all_registers", {}, action="discover livestock register")
		if not isinstance(payload, list):
			raise LivestockRegistryClientError("Livestock registers were not a list")
		self._registers = [row for row in payload if isinstance(row, dict) and row.get("register_id")]
		return self._registers

	def _discover_register_id(self) -> str:
		if self._register_id:
			return self._register_id
		matches = [
			row for row in self._all_registers() if row.get("register_mnemonic") == self.register_mnemonic
		]
		if len(matches) != 1 or not matches[0].get("register_id"):
			raise LivestockRegistryClientError("Livestock register is missing or ambiguous")
		self._register_id = str(matches[0]["register_id"])
		return self._register_id

	def _rendered_form(self) -> dict:
		if self._form is not None:
			return self._form
		register_id = self._discover_register_id()
		rows = self._post(
			"/intake-form-metadata/get_all_intake_forms",
			{"register_id": register_id},
			action="discover livestock intake form",
		)
		wanted = self.form_name_contains.lower()
		forms = [
			row
			for row in rows or []
			if isinstance(row, dict)
			and row.get("register_id") == register_id
			and row.get("form_id")
			and wanted in str(row.get("form_mnemonic") or "").lower()
		]
		if len(forms) != 1:
			raise LivestockRegistryClientError("Livestock intake form is missing or ambiguous")
		rendered = self._post(
			"/intake-form-metadata/render_intake_form",
			{"form_id": forms[0]["form_id"]},
			action="render livestock intake form",
		)
		if not isinstance(rendered, dict) or not rendered.get("tabs"):
			raise LivestockRegistryClientError("Rendered Livestock form has no tabs")
		rendered.setdefault("form_id", forms[0]["form_id"])
		rendered.setdefault("register_id", register_id)
		self._form = rendered
		return rendered

	def _register_fields(self, register_id: str) -> list[dict]:
		cached = self._fields.get(register_id)
		if cached is not None:
			return cached
		payload = self._post(
			"/register-metadata/get_register_fields",
			{"register_id": register_id},
			action="read livestock field definitions",
		)
		fields = payload.get("fields") if isinstance(payload, dict) else None
		if not isinstance(fields, list):
			raise LivestockRegistryClientError("Livestock field definitions were not a list")
		self._fields[register_id] = fields
		return fields

	def _catalogs(self, sections: list[dict]) -> dict[str, list]:
		"""Every catalog the form names, read once per client. Empty without a master-data URL."""
		if self._catalog_rows is not None:
			return self._catalog_rows
		if not self.master_data_url:
			return {}
		attribute_ids: set[str] = set()
		for section in sections:
			for widget in section["ordered"]:
				source = widget.get("widget-data-source") or {}
				attribute_id = (source.get("params") or {}).get("attribute_id")
				if attribute_id and not source.get("options"):
					attribute_ids.add(str(attribute_id))
		rows: dict[str, list] = {}
		for attribute_id in sorted(attribute_ids):
			values: list = []
			for body in self._pages(
				"/attributes/get_attribute_values",
				{"attribute_id": attribute_id},
				action="read livestock code list",
				base=self.master_data_url,
			):
				page_rows = body.get("attribute_values") if isinstance(body, dict) else None
				if not isinstance(page_rows, list):
					raise LivestockRegistryClientError("Livestock code list was not a list")
				values.extend(page_rows)
			rows[attribute_id] = values
		self._catalog_rows = rows
		return rows

	# ------------------------------------------------------------------ operations

	def describe_intake_form(self) -> dict:
		form = self._rendered_form()
		sections = section_index(form)
		fields = {
			section["section_register_id"]: self._register_fields(section["section_register_id"])
			for section in sections
		}
		return describe_form(form, fields, self._catalogs(sections))

	def geo_level_values(self, level_id: str, parent_level_value_id: str | None = None) -> list[dict]:
		"""One geography level. A child level sends the selected parent's ``level_value_id``."""
		if not self.master_data_url:
			raise LivestockRegistryClientError("livestock_registry_master_data_url is not configured")
		level = str(level_id or "").strip()
		if not level:
			raise LivestockRegistryClientError("level_id is required")
		payload: dict = {"level_id": level}
		parent = str(parent_level_value_id or "").strip()
		if parent:
			payload["parent_level_value_id"] = parent
		rows: list[dict] = []
		for body in self._pages(
			"/geo/get_geo_level_values", payload, action="read livestock geography", base=self.master_data_url
		):
			if not isinstance(body, list):
				raise LivestockRegistryClientError("Livestock geography was not a list")
			for row in body:
				if not isinstance(row, dict) or not row.get("level_value_id"):
					continue
				item = {
					"level_value_id": row.get("level_value_id"),
					"level_value_mnemonic": row.get("level_value_mnemonic"),
				}
				if row.get("display_name") is not None:
					item["display_name"] = row.get("display_name")
				rows.append(item)
		return rows

	def submit_sections(
		self,
		requests: list[dict],
		*,
		submission_id: str | None = None,
		subject_record_id: str | None = None,
		farmer_id: str | None = None,
	) -> dict:
		form = self._rendered_form()
		return submit_with_metadata(
			form=form,
			fields_for=self._register_fields,
			catalogs=self._catalogs(section_index(form)),
			requests=requests,
			submission_id=submission_id,
			subject_record_id=subject_record_id,
			save=self.save_section,
			find_subject=self._subject_of,
			farmer_id=farmer_id,
		)

	def _subject_of(self, submission_id: str) -> str | None:
		"""Internal id of the draft's Livestock register row, for linking list sections."""
		submission = self.get_submission(submission_id)
		register_id = self._discover_register_id()
		for section in submission.sections if submission else []:
			if section.section_register_id != register_id:
				continue
			for row in section.records:
				if row.get("internal_record_id"):
					return str(row["internal_record_id"])
		return None

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
			raise LivestockRegistryClientError("section_id and section_register_id are required")
		if not isinstance(section_payload, list) or not section_payload:
			raise LivestockRegistryClientError("section_payload must be a non-empty list of records")
		form = self._rendered_form() if not (form_id and register_id) else {}
		saved = self._post(
			"/intake-form-data/save_intake_form_submission",
			{
				"submission_id": submission_id or None,
				"section_id": section_id,
				"section_payload": section_payload,
				"section_register_id": section_register_id,
				"form_id": form_id or form.get("form_id"),
				"register_id": register_id or form.get("register_id"),
			},
			action="save livestock section",
		)
		if not isinstance(saved, dict) or not saved.get("submission_id"):
			raise LivestockRegistryClientError("Livestock save did not return a submission_id")
		return saved

	def finalize_submission(self, submission_id: str) -> dict:
		"""POST /intake-form-data/finalize_intake_form_submission."""
		if not submission_id:
			raise LivestockRegistryClientError("submission_id is required")
		finalized = self._post(
			"/intake-form-data/finalize_intake_form_submission",
			{"submission_id": submission_id},
			action="finalize livestock submission",
		)
		if not isinstance(finalized, dict) or not finalized.get("submission_id"):
			raise LivestockRegistryClientError("Livestock finalize did not return a submission_id")
		return finalized

	def get_submission(self, submission_id: str) -> LivestockSubmission | None:
		if not submission_id:
			raise LivestockRegistryClientError("submission_id is required")
		payload = self._post(
			"/intake-form-data/get_intake_form_submission",
			{"submission_id": submission_id},
			action="read livestock submission",
		)
		if not isinstance(payload, dict) or not payload.get("submission_id"):
			return None
		return submission_to_livestock(payload)

	def list_for_farmer(self, farmer_id: str) -> list[dict]:
		"""Drafts, then every full section record of this farmer's Livestock rows.

		Search finds the Livestock holdings and the drafts. Each draft is read
		back with its sections. ``get_section_records`` reads every section of
		the rendered form for a holding, including the holding itself. A search
		hit only carries ``display_fields``; the section read is the record the
		registry screen shows.
		"""
		farmer_id = str(farmer_id or "").strip()
		if not farmer_id:
			raise LivestockRegistryClientError("farmer_id is required")
		main_id = self._discover_register_id()
		found: list[dict] = []
		seen: set[str] = set()
		subject_ids: set[str] = set()

		def take(row: dict, *, kind: str, register_id: str) -> None:
			key = str(row.get("submission_id") or row.get("internal_record_id") or "")
			if key and key in seen:
				return
			if key:
				seen.add(key)
			found.append({"kind": kind, "register_id": register_id, **row})

		for body in self._pages(
			"/intake-form-data/search_in_intake_form_submissions",
			{"register_id": main_id},
			action="search livestock submissions",
			search_text=farmer_id,
		):
			for row in body if isinstance(body, list) else []:
				if not isinstance(row, dict) or not _row_matches_farmer(row, farmer_id):
					continue
				item = dict(row)
				submission_id = str(row.get("submission_id") or "")
				if submission_id:
					submission = self.get_submission(submission_id)
					if submission is not None:
						item["sections"] = [section.model_dump() for section in submission.sections]
				take(item, kind="submission", register_id=main_id)

		for body in self._pages(
			"/register-data/search_in_a_register",
			{"register_id": main_id},
			action="search livestock register",
			search_text=farmer_id,
		):
			for row in body if isinstance(body, list) else []:
				if (
					isinstance(row, dict)
					and row.get("internal_record_id")
					and _row_matches_farmer(row, farmer_id)
				):
					subject_ids.add(str(row["internal_record_id"]))

		section_registers: list[str] = []
		seen_registers: set[str] = set()
		for section in section_index(self._rendered_form()):
			register_id = str(section.get("section_register_id") or "")
			if register_id and register_id not in seen_registers:
				seen_registers.add(register_id)
				section_registers.append(register_id)
		if main_id not in seen_registers:
			section_registers.insert(0, main_id)

		for subject_id in sorted(subject_ids):
			for register_id in section_registers:
				payload = self._post(
					"/register-data/get_section_records",
					{
						"subject_register_id": main_id,
						"subject_record_id": subject_id,
						"section_register_id": register_id,
					},
					action=f"read livestock section records {register_id}",
				)
				rows = payload if isinstance(payload, list) else []
				for row in rows:
					if isinstance(row, dict):
						take(row, kind="record", register_id=register_id)
		return found
