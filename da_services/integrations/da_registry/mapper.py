"""Translate registry payloads into our schemas.

Field names on the right-hand side are the FSD Part 1 data model names (FSD section 5,
"Development Agent" entity). The real contract may differ; change it here and nowhere
else.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .schemas import DAReference


def to_da_reference(payload: dict) -> DAReference:
	return DAReference(
		da_id=str(payload["da_id"]),
		full_name=payload.get("full_name") or "",
		active_status=payload.get("active_status") or "",
		approval_status=payload.get("approval_status") or "",
		region=payload.get("region"),
		zone=payload.get("zone"),
		woreda=payload.get("woreda"),
		kebele=payload.get("assigned_kebele") or payload.get("kebele"),
		education_tier=payload.get("education_tier"),
		fayda_status=payload.get("fayda_status"),
		phone=payload.get("phone"),
		specialisation=payload.get("specialisation"),
		joined_at=payload.get("joined_at") or payload.get("joined_on"),
		farmer_count=int(payload["farmer_count"]) if payload.get("farmer_count") is not None else None,
		email=payload.get("email"),
		language=payload.get("language"),
		photo_url=payload.get("photo_url"),
		source_version=str(payload["version"]) if payload.get("version") is not None else None,
		fetched_at=datetime.now(UTC),
	)
