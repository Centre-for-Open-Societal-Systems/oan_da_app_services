"""Translate registry payloads into our schemas.

``to_da_reference`` reads the mock's fixture shape (FSD Part 1 data model names).
``registry_record_to_da_reference`` and ``profile_changes_to_change_request`` speak the
DA Registry staff API (DA register, ``da_details_tab``). Registry field names live here
and nowhere else.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from da_services.services import constants as C

from .schemas import DAReference

# Staff API: the DA register's one profile tab and its editable sections. Identity
# (da_identity_section_01: name, Fayda ID, gender, birth) is never sent from here; the
# registry would accept it from the service account, so the block lives in DA Services.
DA_TAB = "da_details_tab"
SECTION_CONTACT = "da_contact_section_04"
SECTION_SERVICE = "da_service_section_02"

# ProfileUpdate.changes key (services/profile.py EDITABLE) -> (section, registry field)
PROFILE_FIELDS: dict[str, tuple[str, str]] = {
	"phone": (SECTION_CONTACT, "mobile_number"),
	"email": (SECTION_CONTACT, "email"),
	"education_tier": (SECTION_SERVICE, "education_level"),
	"specialisation": (SECTION_SERVICE, "specialisation"),
}
# Master Data coded fields: DA Services keeps labels ("Crop production"), the registry codes
_CODED_FIELDS = ("education_level", "specialisation")

# The registry has ACTIVE / INACTIVE / ARCHIVED only; leave and the rest of the DA
# lifecycle stay in DA Services. A record that is not ACTIVE is out of service.
_RECORD_STATUS = {"ACTIVE": C.ACTIVE_STATUS_ACTIVE}


def _to_code(label: str) -> str:
	"""'Crop production' -> 'CROP_PRODUCTION' (Master Data value_id)."""
	return re.sub(r"[\s\-.]+", "_", str(label).strip()).upper()


def _to_label(code: str | None) -> str | None:
	"""'CROP_PRODUCTION' -> 'Crop production'."""
	if not code:
		return None
	return str(code).replace("_", " ").strip().capitalize()


def _geo_level(record: dict, level: str) -> str | None:
	"""Code of one level from geo_code_hierarchy_json, for records without *_code columns."""
	geo = record.get("geo_code_hierarchy_json")
	if not isinstance(geo, dict):
		return None
	for row in geo.get("hierarchy") or []:
		if row.get("level") == level:
			return row.get("level_value_id")
	return None


def registry_record_to_da_reference(record: dict) -> DAReference:
	"""A DA record from get_tab_records (da_details_tab) -> DAReference."""
	name_parts = (record.get("first_name"), record.get("middle_name"), record.get("last_name"))
	return DAReference(
		da_id=str(record["functional_record_id"]),
		full_name=record.get("record_name") or " ".join(p for p in name_parts if p),
		active_status=_RECORD_STATUS.get(record.get("record_status") or "", C.ACTIVE_STATUS_SEPARATED),
		# Only approved records reach the register; submissions are not DAs yet.
		approval_status="Published",
		region=record.get("region_code") or _geo_level(record, "region"),
		zone=record.get("zone_code") or _geo_level(record, "zone"),
		woreda=record.get("woreda_code") or _geo_level(record, "woreda"),
		kebele=record.get("geo_lowest_level_value_id") or _geo_level(record, "kebele"),
		education_tier=_to_label(record.get("education_level")),
		# The registry checks Fayda ID uniqueness only; it holds no verification status.
		fayda_status=None,
		phone=record.get("mobile_number"),
		specialisation=_to_label(record.get("specialisation")),
		joined_at=record.get("joined_date"),
		email=record.get("email"),
		source_version=record.get("last_approved_at"),
		fetched_at=datetime.now(UTC),
	)


def profile_changes_to_change_request(changes: dict) -> tuple[str, dict]:
	"""ProfileUpdate.changes -> (section_id, registry fields) for one change request.

	Raises ValueError for a field the registry does not hold or the DA may not change,
	and for changes spanning two sections (the registry takes one section per request).
	"""
	unknown = sorted(set(changes) - set(PROFILE_FIELDS))
	if unknown:
		raise ValueError(f"Not editable in the DA Registry: {', '.join(unknown)}")
	sections = {PROFILE_FIELDS[f][0] for f in changes}
	if len(sections) != 1:
		raise ValueError("Profile changes must stay within one registry section")
	fields = {}
	for key, value in changes.items():
		field = PROFILE_FIELDS[key][1]
		fields[field] = _to_code(value) if field in _CODED_FIELDS and value else value
	return sections.pop(), fields


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
