"""In-memory Crop Sown reads for local development and tests."""

from __future__ import annotations

from .client import CropRegistryClient, CropRegistryClientError
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

	def list_for_farmer(self, farmer_id: str) -> list[dict]:
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
