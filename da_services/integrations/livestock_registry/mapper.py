"""Map Livestock staff read payloads into the internal submission shape."""

from __future__ import annotations

from .schemas import LivestockSection, LivestockSubmission


def submission_to_livestock(payload: dict) -> LivestockSubmission:
	"""Map a get_intake_form_submission response_payload.

	``record_name`` is the Farmer-ID the draft was saved for.
	"""
	sections: list[LivestockSection] = []
	for section in payload.get("section_payloads") or []:
		if not isinstance(section, dict):
			continue
		sections.append(
			LivestockSection(
				section_id=section.get("section_id"),
				section_register_id=section.get("section_register_id"),
				records=[row for row in (section.get("records") or []) if isinstance(row, dict)],
			)
		)
	return LivestockSubmission(
		submission_id=str(payload.get("submission_id") or ""),
		record_name=payload.get("record_name"),
		application_reference=payload.get("application_reference"),
		draft_status=payload.get("draft_status"),
		approval_status=payload.get("approval_status"),
		sections=sections,
	)
