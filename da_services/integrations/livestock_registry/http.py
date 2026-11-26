"""HTTP client for Livestock staff calls.

Uses shared ``StaffRegistryTransport``. GET list is one search by ``source``;
register detail is one ``get_tab_records`` (same as Crop Sown).
"""

from __future__ import annotations

from da_services.integrations.staff_registry import (
	StaffRegistryTransport,
	filter_search_hits,
	read_register_tab,
	search_by_source,
)

from .client import (
	LivestockRegistryClient,
	LivestockRegistryClientError,
	LivestockRegistryServerError,
)
from .intake import describe_form, section_index, submit_with_metadata
from .mapper import submission_to_livestock
from .schemas import LivestockSubmission

_DEFAULT_FORM_NAME_CONTAINS = "Livestock Intake"
_LOG = "Livestock Registry"


class HTTPLivestockRegistryClient(LivestockRegistryClient):
	"""Live client. Intake-form metadata is cached on the instance."""

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
			client_error=LivestockRegistryClientError,
			server_error=LivestockRegistryServerError,
			tokens=tokens,
		)
		self._register_id = self._transport.register_id
		self._form: dict | None = None
		self._form_id: str | None = None
		self._fields: dict[str, list[dict]] = {}
		self._catalog_rows: dict[str, list] | None = None

	@property
	def master_data_url(self) -> str | None:
		return self._transport.master_data_url

	def _post(self, path: str, payload: dict, *, action: str, **kwargs):
		return self._transport.post(path, payload, action=action, **kwargs)

	def _rendered_form(self) -> dict:
		if self._form is not None:
			return self._form
		rows = self._post(
			"/intake-form-metadata/get_all_intake_forms",
			{"register_id": self._register_id},
			action="discover livestock intake form",
		)
		wanted = self.form_name_contains.lower()
		forms = [
			row
			for row in rows or []
			if isinstance(row, dict)
			and row.get("register_id") == self._register_id
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
		rendered.setdefault("register_id", self._register_id)
		self._form_id = str(forms[0]["form_id"])
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
		rows = {
			attribute_id: self._transport.attribute_values(attribute_id, action="read livestock code list")
			for attribute_id in sorted(attribute_ids)
		}
		self._catalog_rows = rows
		return rows

	def describe_intake_form(self) -> dict:
		form = self._rendered_form()
		sections = section_index(form)
		fields = {
			section["section_register_id"]: self._register_fields(section["section_register_id"])
			for section in sections
		}
		return describe_form(form, fields, self._catalogs(sections))

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
		submission = self.get_submission(submission_id)
		for section in submission.sections if submission else []:
			if section.section_register_id != self._register_id:
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
				"register_id": register_id or form.get("register_id") or self._register_id,
			},
			action="save livestock section",
		)
		if not isinstance(saved, dict) or not saved.get("submission_id"):
			raise LivestockRegistryClientError("Livestock save did not return a submission_id")
		return saved

	def finalize_submission(self, submission_id: str) -> dict:
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
		payload = self._transport.get_intake_form_submission(
			submission_id, action="read livestock submission"
		)
		if not isinstance(payload, dict) or not payload.get("submission_id"):
			return None
		return submission_to_livestock(payload)

	def list_for_farmer(self, farmer_id: str, *, source: str) -> list[dict]:
		"""One staff search only (Crop-parity)."""
		farmer_id = str(farmer_id or "").strip()
		if not farmer_id:
			raise LivestockRegistryClientError("farmer_id is required")
		rows = search_by_source(
			self._transport,
			source=source,
			search_text=farmer_id,
			label="Livestock",
			client_error=LivestockRegistryClientError,
		)
		return filter_search_hits(rows, farmer_id)

	def get_register_tab(self, internal_record_id: str, tab_id: str) -> dict:
		"""One Livestock register tab (single staff round trip)."""
		return read_register_tab(
			self._transport,
			internal_record_id=internal_record_id,
			tab_id=tab_id,
			register_id=self._register_id,
			action="read livestock register tab",
			client_error=LivestockRegistryClientError,
		)
