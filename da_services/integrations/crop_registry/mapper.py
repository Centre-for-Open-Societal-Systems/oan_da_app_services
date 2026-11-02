"""Map Crop Sown Staff read payloads into the internal submission shape.

Only fields the verified read calls return are read here: submission status,
section_payloads, and tab record lists.
"""

from __future__ import annotations

from .schemas import CropSection, CropSubmission


def submission_to_crop(payload: dict) -> CropSubmission:
	"""Map a get_intake_form_submission response_payload."""
	sections: list[CropSection] = []
	for section in payload.get("section_payloads") or []:
		if not isinstance(section, dict):
			continue
		records = [row for row in (section.get("records") or []) if isinstance(row, dict)]
		sections.append(
			CropSection(
				section_register_id=section.get("section_register_id"),
				records=records,
			)
		)
	return CropSubmission(
		submission_id=str(payload.get("submission_id") or ""),
		draft_status=payload.get("draft_status"),
		approval_status=payload.get("approval_status"),
		sections=sections,
	)
