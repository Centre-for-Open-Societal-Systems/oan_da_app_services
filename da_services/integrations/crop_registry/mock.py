"""In-memory Crop Sown reads for local development and tests."""

from __future__ import annotations

from .client import CropRegistryClient, CropRegistryClientError
from .intake import describe_form, form_selector, submit_with_metadata
from .mapper import submission_to_crop
from .schemas import CropSubmission

FIXTURE_SUBMISSION_ID = "crop-sub-001"

_FIXTURE = {
	"submission_id": FIXTURE_SUBMISSION_ID,
	"draft_status": "FINAL",
	"approval_status": "PENDING",
	"section_payloads": [
		{
			"section_register_id": "crop-section-1",
			"records": [
				{
					"internal_record_id": "crop-rec-001",
					"local_name": "Synthetic maize",
				}
			],
		}
	],
}

_TABS = [
	{
		"tab_id": "crop-tab-1",
		"records": [
			{
				"section_register_id": "crop-section-1",
				"records": [{"internal_record_id": "crop-rec-001", "local_name": "Synthetic maize"}],
			}
		],
	}
]


_MOCK_FORM = {
	"form_id": "form-1",
	"register_id": "reg-crop",
	"form_mnemonic": "Crop Planning",
	"tabs": [
		{
			"tab_id": "crop-tab-1",
			"sections": [
				{
					"section_id": "section-1",
					"section_register_id": "reg-crop",
					"section_mnemonic": "planning",
					"is_list": False,
					"section_ui_schema": {
						"local_name": {"column-key": "local_name", "widget-type": "input"},
					},
				}
			],
		}
	],
}

_MOCK_FIELDS = {
	"reg-crop": [
		{"field_name": "local_name", "data_type": "string", "required": True},
	]
}


class MockCropRegistryClient(CropRegistryClient):
	"""Fixture submission used when crop_registry_backend is mock."""

	def get_submission(self, submission_id: str) -> CropSubmission | None:
		if submission_id != FIXTURE_SUBMISSION_ID:
			return None
		return submission_to_crop(_FIXTURE)

	def get_tabs(self, submission_id: str) -> list[dict]:
		if submission_id != FIXTURE_SUBMISSION_ID:
			return []
		return [dict(tab) for tab in _TABS]

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
		if not section_id or not section_register_id or not section_payload:
			raise CropRegistryClientError("section_id, section_register_id and section_payload are required")
		saved = {
			"submission_id": submission_id or FIXTURE_SUBMISSION_ID,
			"draft_status": "DRAFT",
			"section_id": section_id,
			"section_register_id": section_register_id,
		}
		if form_id:
			saved["form_id"] = form_id
		if register_id:
			saved["register_id"] = register_id
		return saved

	def finalize_submission(self, submission_id: str) -> dict:
		if not submission_id:
			raise CropRegistryClientError("submission_id is required")
		return {
			"submission_id": submission_id,
			"draft_status": "FINAL",
			"approval_status": "PENDING",
		}

	def describe_intake_form(self, *, form_name: str | None = None) -> dict:
		selected = form_selector(form_name, "Planning")
		if selected != "planning":
			raise CropRegistryClientError(
				"The Crop Sown mock client only has the planning fixture. "
				"Set crop_registry_backend to http to load " + selected
			)
		described = describe_form(_MOCK_FORM, _MOCK_FIELDS)
		described["intake_forms"] = [{"form_id": "form-1", "form_mnemonic": "Crop Planning"}]
		return described

	def geo_level_values(self, level_id: str, parent_level_value_id: str | None = None) -> list[dict]:
		# The fixture is one synthetic place. The parent is accepted and not applied.
		del parent_level_value_id
		if not str(level_id or "").strip():
			raise CropRegistryClientError("level_id is required")
		return [
			{
				"level_value_id": "geo-1",
				"level_value_mnemonic": "SYNTHETIC",
				"display_name": "Synthetic",
			}
		]

	def submit_sections(
		self,
		requests: list[dict],
		*,
		submission_id: str | None = None,
		subject_record_id: str | None = None,
		form_name: str | None = None,
	) -> dict:
		return submit_with_metadata(
			form=_MOCK_FORM,
			fields_for=lambda register_id: list(_MOCK_FIELDS.get(register_id) or []),
			catalogs={},
			requests=requests,
			submission_id=submission_id,
			subject_record_id=subject_record_id,
			save=self.save_section,
		)

	def list_for_farmer(self, farmer_id: str, *, reg_id: str | None = None) -> list[dict]:
		if not farmer_id:
			return []
		return [
			{
				"internal_record_id": "crop-rec-001",
				"link_internal_record_id": farmer_id,
				"record_name": "Synthetic maize",
				"display_fields": [{"field_name": "local_name", "order": 1, "value": "Synthetic maize"}],
			}
		]
