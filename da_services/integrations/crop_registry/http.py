"""HTTP client for the verified Crop Sown Staff read calls.

Uses shared ``StaffRegistryTransport`` for auth, envelope, and search.
``register_id`` comes from DA Services Settings via the factory.
"""

from __future__ import annotations

from da_services.integrations.staff_registry import (
	StaffRegistryTransport,
	filter_search_hits,
	read_register_tab,
	search_by_source,
)

from .client import (
	CropRegistryClient,
	CropRegistryClientError,
	CropRegistryServerError,
)
from .intake import describe_form, form_selector, section_index, submit_with_metadata
from .mapper import submission_to_crop
from .schemas import CropSubmission

_DEFAULT_FORM_NAME_CONTAINS = "Planning"
_LOG = "Crop Sown Registry"


class HTTPCropRegistryClient(CropRegistryClient):
	"""Live read client. Intake-form metadata is cached on the instance."""

	def __init__(
		self,
		*,
		staff_url: str,
		register_id: str,
		form_name_contains: str = _DEFAULT_FORM_NAME_CONTAINS,
		tokens=None,
		master_data_url: str | None = None,
	):
		self.form_name_contains = form_name_contains
		self._transport = StaffRegistryTransport(
			staff_url=staff_url,
			register_id=register_id,
			master_data_url=master_data_url,
			log_title=_LOG,
			client_error=CropRegistryClientError,
			server_error=CropRegistryServerError,
			tokens=tokens,
		)
		self._register_id = self._transport.register_id
		self._form_id: str | None = None
		self._form: dict | None = None
		self._forms: dict[str, dict] = {}
		self._intake_form_rows: list[dict] | None = None
		self._fields: dict[str, list[dict]] = {}

	@property
	def master_data_url(self) -> str | None:
		return self._transport.master_data_url

	def _post(self, path: str, payload: dict, *, action: str, **kwargs):
		return self._transport.post(path, payload, action=action, **kwargs)

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
		payload = self._transport.get_intake_form_submission(submission_id, action="read crop submission")
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
			records = self._transport.get_tab_records(
				subject_record_id="",
				tab_id=tab_id,
				submission_id=submission_id,
				action="read crop tab",
			)
			if not isinstance(records, list):
				raise CropRegistryClientError("Crop Sown tab records were not a list")
			tabs.append({"tab_id": tab_id, "records": records})
		if not tabs:
			raise CropRegistryClientError("Rendered Crop Sown form has no readable tabs")
		return tabs

	def list_for_farmer(self, farmer_id: str, *, reg_id: str | None = None, source: str) -> list[dict]:
		"""Find Crop Sown rows for one farmer. One staff search only."""
		if not farmer_id:
			raise CropRegistryClientError("farmer_id is required")
		submission_text = (reg_id or "").strip() or farmer_id
		owned = {farmer_id, submission_text}
		search_text = submission_text if source == "submission" else farmer_id
		rows = search_by_source(
			self._transport,
			source=source,
			search_text=search_text,
			label="Crop Sown",
			client_error=CropRegistryClientError,
		)
		return filter_search_hits(rows, owned)

	def get_register_tab(self, internal_record_id: str, tab_id: str) -> dict:
		"""One Crop Sown register tab (single staff round trip)."""
		return read_register_tab(
			self._transport,
			internal_record_id=internal_record_id,
			tab_id=tab_id,
			register_id=self._register_id,
			action="read crop register tab",
			client_error=CropRegistryClientError,
		)

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
		return {
			attribute_id: self._transport.attribute_values(attribute_id, action="read crop code list")
			for attribute_id in attribute_ids
		}
