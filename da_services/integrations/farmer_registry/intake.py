"""Register a farmer through the registry's intake form, section by section.

This is the path the staff portal uses, and the one the registry team's
collection uses since ingest-data started requiring a partner signature:

    upload_documents (identity document)  -> document_id      (separate API call, done first)
    save_intake_form_submission x N       -> one draft submission_id
    finalize_intake_form_submission       -> FINAL, approval PENDING

The form layout (sections, which register each section writes to), the code
lists and the place hierarchy are read from the registry and Master Data and
cached, so a changed form on the registry does not need a code change here.

Each register row gets an internal_record_id we generate, and rows point at
each other through link_internal_record_id:
    Farmer          -> Household
    Household       -> Farmer
    HouseholdMember -> Household
    FarmerPhone     -> Farmer,  Land -> Farmer
    Crop, Livestock -> Land (the first land)
Household is saved first, as the registry expects.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import date
from typing import Any

import frappe

from .client import FarmerRegistryClient

CACHE_PREFIX = "da_services:farmer_registry:intake"
LAYOUT_TTL = 60 * 60
CODE_LIST_TTL = 6 * 60 * 60
PLACE_TTL = 24 * 60 * 60

GEO_LEVELS = ("region", "zone", "woreda", "kebele")
# Master Data place ids grow by two digits a level: ET04 > ET0408 > ET040801 > ET04080101.
KEBELE_ID_RE = re.compile(r"^ET\d{8}$")
GEO_ID_LENGTHS = {"region": 4, "zone": 6, "woreda": 8, "kebele": 10}

PHONE_TYPE_PRIMARY = "PHONE_PRIMARY"
LAND_UNIT_HECTARE = "HECTARE"
COUNTRY_CODE = "ET"

# DA app / ODK words that do not match a Master Data code by name.
SYNONYMS: dict[str, dict[str, str]] = {
	"EDUCATION_LEVEL": {"readwrite": "CAN_READ_AND_WRITE", "canreadwrite": "CAN_READ_AND_WRITE"},
	"SOURCE_OF_INCOME": {
		"crop": "SOI_CROP_PRODUCTION",
		"livestock": "SOI_LIVESTOCK_PRODUCTION",
		"govngo": "SOI_GOVERNMENT_NGO_SUPPORT",
		"other": "SOI_OTHERS",
	},
	"LAND_OWNERSHIP_TYPE": {"share": "CROP_SHARE"},
	"CROP_SEASON": {"irrigated": "SEASON_IRRIGATION"},
}

# Code lists the mapper reads.
CODE_LISTS = (
	"GENDER",
	"MARITAL_STATUS",
	"EDUCATION_LEVEL",
	"SOURCE_OF_INCOME",
	"RELATIONSHIP_TO_HEAD",
	"IDENTITY_DOCUMENT_TYPE",
	"LAND_OWNERSHIP_TYPE",
	"CROP_COMMODITY",
	"CROP_SEASON",
	"LIVESTOCK_TYPE",
	"LIVESTOCK_SYSTEM",
)


class IntakeFormError(ValueError):
	"""The DA form cannot be turned into a valid intake submission. Lists every problem."""

	def __init__(self, problems: list[str]):
		super().__init__("; ".join(problems))
		self.problems = problems


# ---------------------------------------------------------------------------
# Registry metadata (cached)
# ---------------------------------------------------------------------------


def _cache_key(client: FarmerRegistryClient, *parts: str) -> str:
	return ":".join((CACHE_PREFIX, getattr(client, "base_url", "") or type(client).__name__, *parts))


def _cached(key: str, ttl: int, load: Callable[[], Any]) -> Any:
	value = frappe.cache.get_value(key)
	if value is None:
		value = load()
		frappe.cache.set_value(key, value, expires_in_sec=ttl)
	return value


def clear_cache() -> None:
	"""Drop cached layout, code lists and places (after the registry form changes)."""
	frappe.cache.delete_keys(CACHE_PREFIX)


def _walk(value: Any):
	if isinstance(value, dict):
		yield value
		for v in value.values():
			yield from _walk(v)
	elif isinstance(value, list):
		for v in value:
			yield from _walk(v)


def _has_identity_document_widget(ui_schema: Any) -> bool:
	for node in _walk(ui_schema):
		path = node.get("widget-data-path") or node.get("column-key")
		if isinstance(path, str) and path.endswith(".identity_document_1"):
			return True
	return False


def load_registers(client: FarmerRegistryClient) -> list[dict]:
	"""Every register of the registry (get_all_registers), cached with the layout."""
	return _cached(_cache_key(client, "registers"), LAYOUT_TTL, client.get_registers)


def load_layout(client: FarmerRegistryClient) -> dict:
	"""The intake form as {form_id, register_id, sections: [...]}, sections in save order.

	Each section: {section_id, section_register_id, register_mnemonic, is_list,
	order, has_identity_document}.
	"""

	def load() -> dict:
		form = client.render_intake_form()
		mnemonics = {r.get("register_id"): r.get("register_mnemonic") for r in load_registers(client)}
		sections = []
		for tab in form.get("tabs") or []:
			for s in tab.get("sections") or []:
				sections.append(
					{
						"section_id": s.get("section_id"),
						"section_register_id": s.get("section_register_id"),
						"register_mnemonic": mnemonics.get(s.get("section_register_id")),
						"is_list": bool(s.get("is_list")),
						"order": s.get("section_order") or 0,
						"has_identity_document": _has_identity_document_widget(s.get("section_ui_schema")),
					}
				)
		# The registry wants the household before the farmer that links to it.
		sections.sort(key=lambda s: (s["register_mnemonic"] != "Household", s["order"]))
		return {"form_id": form.get("form_id"), "register_id": form.get("register_id"), "sections": sections}

	return _cached(_cache_key(client, "layout"), LAYOUT_TTL, load)


def load_code_lists(client: FarmerRegistryClient, attribute_ids=CODE_LISTS) -> dict[str, list[dict]]:
	return {
		aid: _cached(
			_cache_key(client, "codes", aid), CODE_LIST_TTL, lambda aid=aid: client.get_attribute_values(aid)
		)
		for aid in attribute_ids
	}


def resolve_geo_path(client: FarmerRegistryClient, kebele_id: str) -> list[dict]:
	"""Region -> kebele path for a Master Data kebele id, checked against Master Data.

	Returns [{level, level_value_id, level_value_mnemonic}] for region, zone,
	woreda, kebele. Raises IntakeFormError if any level is not found.
	"""
	if not KEBELE_ID_RE.match(kebele_id or ""):
		raise IntakeFormError(
			[f"kebele: '{kebele_id}' is not a Master Data kebele id (for example ET04080101)"]
		)

	def load() -> list[dict]:
		level_ids = {
			str(lv.get("level_mnemonic") or "").lower(): lv.get("level_id") for lv in client.get_geo_levels()
		}
		path, parent = [], None
		for level in GEO_LEVELS:
			wanted = kebele_id[: GEO_ID_LENGTHS[level]]
			if not level_ids.get(level):
				raise IntakeFormError([f"kebele: Master Data has no '{level}' level"])
			places = client.get_geo_level_values(level_ids[level], parent)
			node = next((p for p in places if p.get("level_value_id") == wanted), None)
			if node is None:
				raise IntakeFormError([f"kebele: {level} '{wanted}' was not found in Master Data"])
			path.append(
				{
					"level": level,
					"level_value_id": wanted,
					"level_value_mnemonic": node.get("level_value_mnemonic"),
				}
			)
			parent = wanted
		return path

	return _cached(_cache_key(client, "place", kebele_id), PLACE_TTL, load)


# ---------------------------------------------------------------------------
# DA form -> intake sections (pure)
# ---------------------------------------------------------------------------


def _norm(value: Any) -> str:
	return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def code_for(code_lists: dict[str, list[dict]], attribute_id: str, value: Any, label: str) -> str:
	"""Match a DA value to a Master Data code: by code, by display name, by synonym,
	then by the code without its prefix (CROP_TEFF matches "teff")."""
	values = code_lists.get(attribute_id) or []
	wanted = _norm(value)
	ids = [str(v.get("value_id") or v.get("value_code") or "") for v in values]
	for v, vid in zip(values, ids, strict=True):
		if wanted in (_norm(vid), _norm(v.get("value_code"))):
			return vid
	for v, vid in zip(values, ids, strict=True):
		if wanted == _norm(v.get("value_display")):
			return vid
	synonym = SYNONYMS.get(attribute_id, {}).get(wanted)
	if synonym and synonym in ids:
		return synonym
	for vid in ids:
		if "_" in vid and wanted == _norm(vid.split("_", 1)[1]):
			return vid
	allowed = ", ".join(ids[:15]) + (" ..." if len(ids) > 15 else "")
	raise IntakeFormError([f"{label}: '{value}' is not a known value (allowed: {allowed})"])


def normalize_phone(phone: str) -> str:
	"""Ethiopian mobile as the registry accepts it: 0912345678 or +251912345678."""
	digits = re.sub(r"\D", "", phone or "")
	if len(digits) == 9 and digits.startswith("9"):
		return "0" + digits
	if len(digits) == 12 and digits.startswith("2519"):
		return "+" + digits
	return phone


def _location(form: dict) -> dict:
	parts = (form.get("location") or "").split()
	out = {}
	for key, value in zip(("latitude", "longitude", "altitude"), parts, strict=False):
		out[key] = float(value)
	return out


def _full_name(person: dict) -> str:
	return " ".join(
		p for p in (person.get("first_name"), person.get("father_name"), person.get("grandfather_name")) if p
	)


def _check_form(form: dict) -> list[str]:
	problems = []
	required = {
		"grandfather_name": "grandfather_name (registry last name) is required",
		"gender": "gender is required",
		"date_of_birth": "date_of_birth is required",
		"marital_status": "marital_status is required",
		"education_level": "education_level is required",
		"kebele": "kebele (Master Data id, for example ET04080101) is required",
		"identity_document_id": "identity_document_id is required; upload the ID with POST /farmers/registrations/documents first",
	}
	for key, message in required.items():
		if form.get(key) in (None, ""):
			problems.append(message)

	household = form.get("household") or {}
	members = household.get("members") or []
	size = household.get("size")
	if size is not None and size != 1 + len(members):
		problems.append(
			f"household: size is {size} but {1 + len(members)} people were given "
			"(the farmer plus each member); add every member or correct the size"
		)
	if (form.get("crops") or form.get("livestock")) and not form.get("lands"):
		problems.append("lands: crops and livestock must belong to a land; add at least one land")
	return problems


def build_intake_sections(
	form: dict,
	layout: dict,
	code_lists: dict[str, list[dict]],
	geo_path: list[dict],
	*,
	today: date | None = None,
) -> list[dict]:
	"""Turn a validated DA registration form into save_intake_form_submission bodies.

	Returns one dict per section to save, in order, without submission_id
	(the caller chains it). Raises IntakeFormError listing every problem.
	"""
	problems = _check_form(form)
	if problems:
		raise IntakeFormError(problems)

	today_iso = (today or date.today()).isoformat()
	problems = []

	def code(attribute_id: str, value: Any, label: str) -> str | None:
		try:
			return code_for(code_lists, attribute_id, value, label)
		except IntakeFormError as e:
			problems.extend(e.problems)
			return None

	farmer_id = str(uuid.uuid4())
	household_id = str(uuid.uuid4())
	hierarchy = {
		"hierarchy": [
			{
				"level_mnemonic": p["level"],
				"level_value_id": p["level_value_id"],
				"level_value_mnemonic": p.get("level_value_mnemonic"),
			}
			for p in geo_path
		]
	}

	farmer: dict[str, Any] = {
		"internal_record_id": farmer_id,
		"link_internal_record_id": household_id,
		"first_name": form["first_name"],
		"middle_name": form.get("father_name") or None,
		"last_name": form["grandfather_name"],
		"gender": code("GENDER", form["gender"], "gender"),
		"birth_date": str(form["date_of_birth"]),
		"marital_status": code("MARITAL_STATUS", form["marital_status"], "marital_status"),
		"education_level": code("EDUCATION_LEVEL", form["education_level"], "education_level"),
		"income_sources": [
			code("SOURCE_OF_INCOME", s, "income_sources") for s in form.get("income_sources") or []
		],
		"disabled": bool(form.get("disabled")),
		"is_household_head": True,
		"is_farmer": True,
		"has_national_id": bool(form.get("national_id")),
		"country_code": COUNTRY_CODE,
		"geo_lowest_level_value_id": form["kebele"],
		"geo_code_hierarchy_json": hierarchy,
		"identity_document_type": code(
			"IDENTITY_DOCUMENT_TYPE", form.get("identity_document_type") or "fayda", "identity_document_type"
		),
		"registration_date": today_iso,
		"data_collection_date": today_iso,
		**_location(form),
	}
	if form.get("national_id"):
		farmer["foundational_id"] = form["national_id"]
	if form.get("fayda_rid"):
		farmer["rid"] = form["fayda_rid"]
	if form.get("is_psnp_user") is not None:
		farmer["is_psnp_user"] = bool(form["is_psnp_user"])
	if form.get("da_id"):
		farmer["enumerator_user_id"] = form["da_id"]
	if form.get("enumerator_name"):
		farmer["enumerator_name"] = form["enumerator_name"]
	farmer = {k: v for k, v in farmer.items() if v is not None}

	household_in = form.get("household") or {}
	members_in = household_in.get("members") or []
	people = [
		{
			"first_name": form["first_name"],
			"father_name": form.get("father_name"),
			"grandfather_name": form["grandfather_name"],
			"gender": form["gender"],
			"date_of_birth": form["date_of_birth"],
			"relationship": "self",
			"is_head": True,
		},
		*({**m, "is_head": False} for m in members_in),
	]
	member_rows = []
	for i, person in enumerate(people):
		label = "household.head" if i == 0 else f"household.members[{i - 1}]"
		row = {
			"internal_record_id": str(uuid.uuid4()),
			"link_internal_record_id": household_id,
			"first_name": person["first_name"],
			"middle_name": person.get("father_name") or None,
			"last_name": person["grandfather_name"],
			"birth_date": str(person["date_of_birth"]),
			"gender": code("GENDER", person["gender"], f"{label}.gender"),
			"is_head": person["is_head"],
			"relationship_to_the_head": code(
				"RELATIONSHIP_TO_HEAD", person["relationship"], f"{label}.relationship"
			),
		}
		member_rows.append({k: v for k, v in row.items() if v is not None})

	genders = [r.get("gender") for r in member_rows]
	household = {
		"internal_record_id": household_id,
		"link_internal_record_id": farmer_id,
		"household_head": _full_name(form),
		"size_of_group": len(people),
		"number_of_male_members": genders.count("MALE"),
		"number_of_female_members": genders.count("FEMALE"),
		"other_land_owner": bool(household_in.get("other_land_owner")),
	}
	if household_in.get("number_of_children") is not None:
		household["number_of_children"] = household_in["number_of_children"]

	phones = []
	if form.get("phone"):
		phones.append(
			{
				"internal_record_id": str(uuid.uuid4()),
				"link_internal_record_id": farmer_id,
				"phone_number": normalize_phone(form["phone"]),
				"phone_type": PHONE_TYPE_PRIMARY,
				"is_primary": True,
				"country_code": COUNTRY_CODE,
			}
		)

	lands = []
	for i, land in enumerate(form.get("lands") or []):
		row = {
			"internal_record_id": str(uuid.uuid4()),
			"link_internal_record_id": farmer_id,
			"land_ownership_type": code(
				"LAND_OWNERSHIP_TYPE", land.get("ownership") or "owner", f"lands[{i}].ownership"
			),
			"land_size": land.get("area_hectare"),
			"area_in_hectare": land.get("area_hectare"),
			"unit": LAND_UNIT_HECTARE,
			"land_id": land.get("land_id"),
		}
		lands.append({k: v for k, v in row.items() if v is not None})
	land_id = lands[0]["internal_record_id"] if lands else None

	crops = []
	for i, crop in enumerate(form.get("crops") or []):
		row = {
			"internal_record_id": str(uuid.uuid4()),
			"link_internal_record_id": land_id,
			"commodity": code("CROP_COMMODITY", crop.get("name"), f"crops[{i}].name"),
			"season": code("CROP_SEASON", crop.get("season") or "meher", f"crops[{i}].season"),
			"planted_date": str(crop["planted_date"]) if crop.get("planted_date") else None,
		}
		crops.append({k: v for k, v in row.items() if v is not None})

	livestock = []
	for i, animal in enumerate(form.get("livestock") or []):
		row = {
			"internal_record_id": str(uuid.uuid4()),
			"link_internal_record_id": land_id,
			"livestock_type": code("LIVESTOCK_TYPE", animal.get("animal"), f"livestock[{i}].animal"),
			"head_count": animal.get("count") or 1,
			"livestock_system": code(
				"LIVESTOCK_SYSTEM", animal.get("system") or "mixed", f"livestock[{i}].system"
			),
		}
		livestock.append({k: v for k, v in row.items() if v is not None})

	if problems:
		raise IntakeFormError(problems)

	rows_by_register = {
		"Farmer": [farmer],
		"Household": [household],
		"HouseholdMember": member_rows,
		"FarmerPhone": phones,
		"Land": lands,
		"Crop": crops,
		"Livestock": livestock,
	}

	sections = []
	for s in layout.get("sections") or []:
		rows = rows_by_register.get(s.get("register_mnemonic")) or []
		if not rows:
			continue  # e.g. farm inputs and membership are not on the DA form
		body = {
			"form_id": layout.get("form_id"),
			"register_id": layout.get("register_id"),
			"section_id": s["section_id"],
			"section_register_id": s["section_register_id"],
			"section_payload": rows,
		}
		if s.get("has_identity_document"):
			body["documents"] = [
				{"document_id": form["identity_document_id"], "label": "identity_document_1"}
			]
		sections.append(body)

	if not any(s.get("documents") for s in sections):
		raise IntakeFormError(
			["the registry intake form has no identity document section; check the form layout"]
		)
	return sections


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def build_plan(client: FarmerRegistryClient, form: dict) -> list[dict]:
	"""Read layout, code lists and places, then map the form. Raises IntakeFormError."""
	problems = _check_form(form)
	if problems:
		raise IntakeFormError(problems)
	layout = load_layout(client)
	code_lists = load_code_lists(client)
	geo_path = resolve_geo_path(client, form["kebele"])
	return build_intake_sections(form, layout, code_lists, geo_path)


def submit_plan(
	client: FarmerRegistryClient,
	sections: list[dict],
	*,
	on_draft: Callable[[str], None] | None = None,
) -> dict:
	"""Save every section into one draft, then finalize it.

	on_draft(submission_id) runs once the registry has created the draft, so
	the caller can record it before later sections can fail.

	Returns {"success", "submission_id", "status", "approval_status",
	"error_code", "error_message", "failed_section"}. Registry exceptions
	(FarmerRegistryClientError / ServerError) propagate.
	"""
	submission_id = None
	for body in sections:
		result = client.save_intake_section({**body, "submission_id": submission_id})
		if not result.get("success"):
			return {
				"success": False,
				"submission_id": submission_id,
				"status": result.get("status") or "ERROR",
				"approval_status": None,
				"error_code": result.get("error_code"),
				"error_message": result.get("error_message") or f"Saving {body['section_id']} failed",
				"failed_section": body["section_id"],
			}
		if not submission_id:
			submission_id = result.get("submission_id")
			if not submission_id:
				return {
					"success": False,
					"submission_id": None,
					"status": "ERROR",
					"approval_status": None,
					"error_code": None,
					"error_message": "The registry did not return a submission_id",
					"failed_section": body["section_id"],
				}
			if on_draft:
				on_draft(submission_id)

	final = client.finalize_intake_submission(submission_id)
	if not final.get("success"):
		return {
			"success": False,
			"submission_id": submission_id,
			"status": final.get("status") or "ERROR",
			"approval_status": None,
			"error_code": final.get("error_code"),
			"error_message": final.get("error_message") or "Finalizing the submission failed",
			"failed_section": "finalize",
		}
	return {
		"success": True,
		"submission_id": submission_id,
		"status": "PENDING_APPROVAL"
		if final.get("approval_status") == "PENDING"
		else final.get("approval_status") or "FINAL",
		"approval_status": final.get("approval_status"),
		"error_code": None,
		"error_message": None,
		"failed_section": None,
	}
