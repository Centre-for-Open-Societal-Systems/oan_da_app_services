"""Translate registry payloads into our schemas.

``to_da_reference`` reads the mock's fixture shape (FSD Part 1 data model names).
``registry_record_to_da_reference`` and ``profile_changes_to_change_request`` speak the
DA Registry staff API (DA register, ``da_details_tab``). Registry field names live here
and nowhere else.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from da_services.services import constants as C

from .schemas import DAReference

# Staff API: the DA register's one profile tab and its four sections. DA Services
# approves every change at two levels (Supervisor, then Administrator) before it is sent;
# the registry checks formats and duplicates, never who asked, so the allow-list below
# and the checks in services/profile_fields.py are the guard.
DA_TAB = "da_details_tab"
SECTION_IDENTITY = "da_identity_section_01"
SECTION_SERVICE = "da_service_section_02"
SECTION_LOCATION = "da_location_section_03"
SECTION_CONTACT = "da_contact_section_04"
# List sections of the same tab (DA Registry #66). Each row is a record of its own
# register, linked to the DA by link_internal_record_id.
SECTION_QUALIFICATION = "da_qualification_section_05"
SECTION_DEPENDENT = "da_dependent_section_06"
QUALIFICATION_REGISTER = "6f1c2d8e-4a7b-4c3e-9d2f-8b5a1e7c3fa0"
DEPENDENT_REGISTER = "6f1c2d8e-4a7b-4c3e-9d2f-8b5a1e7c3fb0"

# ProfileUpdate.changes key (services/profile_fields.py) -> (section, registry field)
PROFILE_FIELDS: dict[str, tuple[str, str]] = {
	"phone": (SECTION_CONTACT, "mobile_number"),
	"telegram_number": (SECTION_CONTACT, "telegram_number"),
	"alternate_number": (SECTION_CONTACT, "alternate_number"),
	"email": (SECTION_CONTACT, "email"),
	"home_address": (SECTION_CONTACT, "address_line_1"),
	# Position and reporting. The DA record's education_level and passout_year are filled
	# by the registry from the main education record: changed only through that list.
	"specialisation": (SECTION_SERVICE, "specialisation"),
	"position": (SECTION_SERVICE, "position"),
	"joined_date": (SECTION_SERVICE, "joined_date"),
	"reports_to": (SECTION_SERVICE, "reports_to"),
	"prefix": (SECTION_IDENTITY, "prefix"),
	"first_name": (SECTION_IDENTITY, "first_name"),
	"father_name": (SECTION_IDENTITY, "middle_name"),
	"grandfather_name": (SECTION_IDENTITY, "last_name"),
	"fayda_id": (SECTION_IDENTITY, "foundational_id"),
	"gender": (SECTION_IDENTITY, "gender"),
	"marital_status": (SECTION_IDENTITY, "marital_status"),
	"birth_month_ec": (SECTION_IDENTITY, "birth_month_ec"),
	"birth_year_ec": (SECTION_IDENTITY, "birth_year_ec"),
	# The registry derives region / zone / woreda / kebele names and codes itself.
	"kebele": (SECTION_LOCATION, "geo_lowest_level_value_id"),
	"geo_code_hierarchy_json": (SECTION_LOCATION, "geo_code_hierarchy_json"),
}
_INT_FIELDS = ("birth_month_ec", "birth_year_ec")
# Coded fields (education level, position, gender ...) arrive as Master Data codes:
# services/profile_fields.py checks each against its list. They are sent unchanged.

# The registry has ACTIVE / INACTIVE / ARCHIVED only; leave and the rest of the DA
# lifecycle stay in DA Services. A record that is not ACTIVE is out of service.
_RECORD_STATUS = {"ACTIVE": C.ACTIVE_STATUS_ACTIVE}


def _to_label(code: str | None) -> str | None:
	"""'CROP_PRODUCTION' -> 'Crop production'."""
	if not code:
		return None
	return str(code).replace("_", " ").strip().capitalize()


def _geo_row(record: dict, level: str) -> dict:
	"""One level (region / zone / woreda / kebele) of geo_code_hierarchy_json."""
	geo = record.get("geo_code_hierarchy_json")
	if not isinstance(geo, dict):
		return {}
	for row in geo.get("hierarchy") or []:
		if row.get("level") == level:
			return row
	return {}


def _geo_level(record: dict, level: str) -> str | None:
	"""Code of one level: from the chain first, then the flat *_code column.

	The chain is what a change request writes. Until the registry re-derives the flat
	columns on a location change, they keep the old values, so they are the fallback.
	"""
	return _geo_row(record, level).get("level_value_id") or record.get(f"{level}_code")


def _geo_name(record: dict, level: str) -> str | None:
	"""Name of one level, chain first (the *_name columns are often empty or stale)."""
	return _geo_row(record, level).get("level_value_mnemonic") or record.get(f"{level}_name")


# Master Data attribute of each coded DA field
LABEL_ATTRIBUTES = {
	"prefix": "PREFIX",
	"gender": "GENDER",
	"marital_status": "MARITAL_STATUS",
	"education_level": "DA_EDUCATION_LEVEL",
	"specialisation": "DA_SPECIALISATION",
	"position": "DA_POSITION",
	"relationship": "DA_RELATIONSHIP",
}

# Ethiopian calendar months, birth_month_ec 1-13
EC_MONTHS = (
	"Meskerem",
	"Tikimt",
	"Hidar",
	"Tahsas",
	"Tir",
	"Yekatit",
	"Megabit",
	"Miazia",
	"Ginbot",
	"Sene",
	"Hamle",
	"Nehase",
	"Pagume",
)


def _int(value) -> int | None:
	try:
		return int(value) if value not in (None, "") else None
	except TypeError, ValueError:
		return None


def _coded(field: str, code, labels: dict[str, dict]) -> str | None:
	"""The Master Data label of a coded value; the code as words when the list is not known."""
	if not code:
		return None
	label = (labels.get(LABEL_ATTRIBUTES[field]) or {}).get(code)
	if label:
		return label
	# DA_RELATIONSHIP codes carry a prefix ("REL_SON"): the words are what follows it.
	return _to_label(code.removeprefix("REL_") if field == "relationship" else code)


def _files(value) -> list[dict]:
	"""A list cell of stored files, [{"document_id", "name"}] (sent as JSON text or as a list)."""
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except ValueError:
			return []
	return [
		{"document_id": str(f["document_id"]), "name": f.get("name") or None}
		for f in value or []
		if isinstance(f, dict) and f.get("document_id")
	]


def qualification_rows(rows, labels: dict[str, dict] | None = None) -> list[dict]:
	"""Education & Professional records: the main record first, then newest passout year.

	A record holds the education level, the passout year and its certificates. The main
	record is the one the registry copies into the DA record's education_level and
	passout_year: the latest passout year (registry qualification_sync.py).
	"""
	labels = labels or {}
	out = [
		{
			"id": r.get("internal_record_id"),
			"education_level": r.get("education_level"),
			"education_level_label": _coded("education_level", r.get("education_level"), labels),
			"passout_year": _int(r.get("passout_year")),
			"certificates": _files(r.get("certificates")),
			"is_main": False,
		}
		for r in rows or []
	]
	if out:
		max(out, key=lambda q: q["passout_year"] or 0)["is_main"] = True
		out.sort(key=lambda q: (not q["is_main"], -(q["passout_year"] or 0)))
	return out


def dependent_rows(rows, labels: dict[str, dict] | None = None) -> list[dict]:
	"""Family members / dependents of the DA, as the registry holds them (no status there)."""
	labels = labels or {}
	return [
		{
			"id": r.get("internal_record_id"),
			"name": r.get("member_name"),
			"relationship": r.get("relationship"),
			"relationship_label": _coded("relationship", r.get("relationship"), labels),
			"date_of_birth": r.get("date_of_birth") or None,
		}
		for r in rows or []
	]


def registry_record_to_da_reference(record: dict, labels: dict[str, dict] | None = None) -> DAReference:
	"""A DA record from get_tab_records (da_details_tab) -> DAReference.

	``labels`` maps a Master Data attribute (DA_POSITION...) to {value_id: value_display};
	without it a code is shown as words ("CROP_PRODUCTION" -> "Crop production").
	"""
	labels = labels or {}

	def label(field: str) -> str | None:
		code = record.get(field)
		if not code:
			return None
		return (labels.get(LABEL_ATTRIBUTES[field]) or {}).get(code) or _to_label(code)

	name_parts = (record.get("first_name"), record.get("middle_name"), record.get("last_name"))
	month = _int(record.get("birth_month_ec"))
	return DAReference(
		da_id=str(record["functional_record_id"]),
		full_name=record.get("record_name") or " ".join(p for p in name_parts if p),
		active_status=_RECORD_STATUS.get(record.get("record_status") or "", C.ACTIVE_STATUS_SEPARATED),
		# Only approved records reach the register; submissions are not DAs yet.
		approval_status="Published",
		region=_geo_level(record, "region"),
		zone=_geo_level(record, "zone"),
		woreda=_geo_level(record, "woreda"),
		kebele=record.get("geo_lowest_level_value_id") or _geo_level(record, "kebele"),
		education_tier=label("education_level"),
		# The registry checks Fayda ID uniqueness only; it holds no verification status.
		fayda_status=None,
		phone=record.get("mobile_number"),
		specialisation=label("specialisation"),
		joined_at=record.get("joined_date"),
		email=record.get("email"),
		salutation=label("prefix"),
		first_name=record.get("first_name"),
		# The registry keeps the father's name in middle_name, the grandfather's in last_name.
		father_name=record.get("middle_name"),
		grandfather_name=record.get("last_name"),
		fayda_id=record.get("foundational_id"),
		gender=label("gender"),
		marital_status=label("marital_status"),
		birth_month_ec=month,
		birth_month_name=EC_MONTHS[month - 1] if month and 1 <= month <= 13 else None,
		birth_year_ec=_int(record.get("birth_year_ec")),
		telegram_number=record.get("telegram_number"),
		alternate_number=record.get("alternate_number"),
		home_address=record.get("address_line_1") or None,
		position=label("position"),
		position_code=record.get("position") or None,
		reports_to=record.get("reports_to") or None,
		passout_year=_int(record.get("passout_year")),
		# Every DA is posted in Ethiopia; the register has no country field.
		country="Ethiopia",
		region_name=_geo_name(record, "region"),
		zone_name=_geo_name(record, "zone"),
		woreda_name=_geo_name(record, "woreda"),
		kebele_name=_geo_name(record, "kebele"),
		# The photo's link from a read lives about an hour; the document id stays.
		photo_url=record.get("record_image_url") or None,
		photo_document_id=record.get("record_image_document_id") or None,
		qualifications=qualification_rows(record.get("_qualifications"), labels),
		dependents=dependent_rows(record.get("_dependents"), labels),
		source_version=record.get("last_approved_at"),
		fetched_at=datetime.now(UTC),
	)


def profile_changes_to_change_request(changes: dict) -> tuple[str, dict]:
	"""ProfileUpdate.changes -> (section_id, registry fields) for one change request.

	Raises ValueError for a field outside PROFILE_FIELDS and for changes spanning two
	sections (the registry takes one section per request).
	"""
	unknown = sorted(set(changes) - set(PROFILE_FIELDS))
	if unknown:
		raise ValueError(f"Not editable in the DA Registry: {', '.join(unknown)}")
	sections = {PROFILE_FIELDS[f][0] for f in changes}
	if len(sections) != 1:
		raise ValueError("Profile changes must stay within one registry section")
	section = sections.pop()
	if section == SECTION_LOCATION and set(changes) != {"kebele", "geo_code_hierarchy_json"}:
		raise ValueError("A location change needs the Kebele and its full geo chain")
	fields = {}
	for key, value in changes.items():
		field = PROFILE_FIELDS[key][1]
		if field in _INT_FIELDS and value not in (None, ""):
			value = int(value)
		fields[field] = value
	return section, fields


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
		telegram_number=payload.get("telegram_number"),
		alternate_number=payload.get("alternate_number"),
		home_address=payload.get("home_address"),
		language=payload.get("language"),
		photo_url=payload.get("photo_url"),
		photo_document_id=payload.get("photo_document_id"),
		qualifications=[dict(q) for q in payload.get("qualifications") or []],
		dependents=[dict(d) for d in payload.get("dependents") or []],
		salutation=payload.get("salutation"),
		first_name=payload.get("first_name"),
		father_name=payload.get("father_name"),
		grandfather_name=payload.get("grandfather_name"),
		fayda_id=payload.get("fayda_id"),
		gender=payload.get("gender"),
		marital_status=payload.get("marital_status"),
		birth_month_ec=_int(payload.get("birth_month_ec")),
		birth_year_ec=_int(payload.get("birth_year_ec")),
		position=payload.get("position"),
		position_code=payload.get("position_code"),
		reports_to=payload.get("reports_to"),
		passout_year=_int(payload.get("passout_year")),
		source_version=str(payload["version"]) if payload.get("version") is not None else None,
		fetched_at=datetime.now(UTC),
	)
