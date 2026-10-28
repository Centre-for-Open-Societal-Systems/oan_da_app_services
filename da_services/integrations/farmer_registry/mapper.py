"""Mapper: Farmer Registry ↔ DA backend field transformations.

Handles naming and format differences between systems.
"""

import uuid
from datetime import date, datetime
from typing import Any

from .schemas import Farmer, FarmerListItem, Plot

# Staff ingest accepts this partner id and skips partner-key checks for this signature.
INGEST_SENDER_ID = "farmer-partner"
INGEST_STAFF_SIGNATURE = "STAFF_AUTHENTICATED"


def da_form_to_ingest_payload(form: dict[str, Any]) -> dict[str, Any]:
	"""Build the Farmer Registry ingest envelope from a DA registration form.

	Produces the payload accepted by the Staff API ``ingest-data`` endpoint for
	the FARMER_ODK_MODEL data model. Field names here mirror the registry's ODK
	transform template (farmer_transform.j2): the registry reads
	``message.payload`` and maps these keys onto the Farmer intake form.

	Only fields present in ``form`` are included. ``woreda``/``kebele`` must be
	the numeric administrative codes (not names). Location is "lat lon alt acc".

	Args:
	    form: DA-side registration form, e.g.
	        {
	            "first_name": "Abebe", "father_name": "Kebede",
	            "grandfather_name": "Tesfaye", "gender": "male",
	            "date_of_birth": "1985-04-12", "phone": "+251911234567",
	            "woreda": "40801", "kebele": "40801101001",
	            "national_id": "1234567890123456",
	            "da_id": "DA-ID-001",
	            "lands": [{"ownership": "owner", "area_hectare": 1.5}],
	            "crops": [{"name": "teff", "planted_date": "2026-07-01"}],
	            "livestock": [{"animal": "cattle", "count": 3}],
	        }

	Returns:
	    Ingest envelope: {"header": {...}, "message": {"payload": {...}}}.
	"""
	personal: dict[str, Any] = {}
	for src, dst in (
		("first_name", "first_name_english"),
		("father_name", "father_name_english"),
		("grandfather_name", "grandfather_name_english"),
		("gender", "gender"),
		("date_of_birth", "date_of_birth"),
		("phone", "primary_phone_number"),
	):
		if form.get(src) not in (None, ""):
			personal[dst] = form[src]

	payload: dict[str, Any] = {
		"register_mnemonic": "Farmer",
		"basic_info": {
			"personal_info": personal,
			"locale_info": {
				k: v
				for k, v in (
					("region", form.get("region")),
					("woreda", form.get("woreda")),
					("kebele", form.get("kebele")),
				)
				if v not in (None, "")
			},
		},
	}

	if form.get("national_id"):
		payload["national_id_section"] = {"national_fan": form["national_id"]}
	if form.get("location"):
		payload["farmer_location"] = {"location": form["location"]}
	if form.get("lands"):
		payload["land_info"] = {
			"land_info_repeat": [
				{
					"land_id": land.get("land_id") or f"LND-{i + 1}",
					"land_ownership": land.get("ownership", "owner"),
					"total_land_area": land.get("area_hectare"),
				}
				for i, land in enumerate(form["lands"])
			]
		}
	if form.get("crops"):
		payload["crop_information"] = {
			"crop_repeat": [
				{
					"crop_name": crop.get("name"),
					"crop_date": crop.get("planted_date", ""),
					"crop_season": crop.get("season", "meher"),
				}
				for crop in form["crops"]
			]
		}
	if form.get("livestock"):
		payload["livestock_info"] = {
			"livestock_repeat": [
				{
					"animal": ls.get("animal"),
					"num_animals": ls.get("count", 1),
					"livestock_system": ls.get("system", "mixed"),
				}
				for ls in form["livestock"]
			]
		}
	if form.get("da_id"):
		payload["survey_metadata"] = {"enumerator_id": form["da_id"]}
	if form.get("farmer_reference_id"):
		payload["farmer_reference_id"] = form["farmer_reference_id"]

	# A client-supplied idempotency key becomes the message_id, so a retried
	# submission reaches the registry with the same id instead of a new one.
	op_id = form.get("client_op_id")
	message_id = f"da-svc-{op_id}" if op_id else f"da-svc-{uuid.uuid4()}"
	# The Staff API wraps this object as {"body": ...}, so these fields are $.body.header.*.
	return {
		"header": {
			"message_id": message_id,
			"sender_id": INGEST_SENDER_ID,
			"signature": INGEST_STAFF_SIGNATURE,
		},
		"message": {"payload": payload},
	}


def farmer_to_reference_dict(farmer: Farmer) -> dict[str, Any]:
	"""Map Farmer Registry schema → DA Farmer Reference fields.

	Args:
	    farmer: Farmer object from registry

	Returns:
	    Dict suitable for DA Farmer Reference doctype
	"""
	return {
		"farmer_id": farmer.farmer_id,
		"reg_id": farmer.reg_id,
		"farmer_name": farmer.record_name or f"{farmer.given_name or ''} {farmer.family_name or ''}".strip(),
		"given_name": farmer.given_name,
		"family_name": farmer.family_name,
		"father_name": farmer.father_first_name,
		"phone": farmer.phone,
		"email": farmer.email,
		"region": farmer.region_name,
		"zone": farmer.zone_name,
		"woreda": farmer.woreda_name,
		"kebele": farmer.kebele_name,
		"gender": farmer.gender,
		"education_level": farmer.education_level,
		"household_size": farmer.household_size,
		"total_land_hectares": farmer.total_land_area,
		"land_ownership_type": farmer.land_ownership,
		"registration_state": farmer.state,
		"registered_at": farmer.registration_date,
		"source_of_income": farmer.source_of_income,
		"is_psnp_beneficiary": farmer.is_psnp_user,
	}


def reference_dict_to_profile(ref_dict: dict[str, Any], visit_data: dict[str, Any]) -> dict[str, Any]:
	"""Merge farmer reference + visit data → profile response (DA-40).

	Args:
	    ref_dict: DA Farmer Reference fields
	    visit_data: Latest visit info from DA Visit service

	Returns:
	    Combined profile for frontend
	"""
	return {
		# Identity
		"farmer_id": ref_dict.get("farmer_id"),
		"reg_id": ref_dict.get("reg_id"),
		"name": ref_dict.get("farmer_name"),
		"kebele": ref_dict.get("kebele"),
		# Verification (Fayda ignored per user request)
		"verification_state": None,  # TODO: Add when Fayda integration is ready
		# Work status
		"pending_actions": visit_data.get("pending_task_count", 0),
		# Contact
		"phone": ref_dict.get("phone"),
		"email": ref_dict.get("email"),
		# Household
		"household_size": ref_dict.get("household_size"),
		# Land
		"total_land_area": ref_dict.get("total_land_hectares"),
		"land_ownership": ref_dict.get("land_ownership_type"),
		# Agriculture
		"primary_crop": None,  # TODO: Query from Crop table when needed
		# Registration
		"registration_date": ref_dict.get("registered_at"),
		# Field activity
		"last_visit_date": visit_data.get("last_visit_date"),
		"last_visit_outcome": visit_data.get("last_visit_outcome"),
		# Additional
		"region": ref_dict.get("region"),
		"woreda": ref_dict.get("woreda"),
		"gender": ref_dict.get("gender"),
		"education_level": ref_dict.get("education_level"),
		"source_of_income": ref_dict.get("source_of_income"),
	}


# ---------------------------------------------------------------------------
# Staff API read path: registry rows -> our schemas
# ---------------------------------------------------------------------------

# DA-41 editable fields the registry can take through a change request, grouped
# by the register section they live in. Everything else is reported back as
# unsupported so the sync queue records a clear reason.
CHANGE_REQUEST_SECTIONS: dict[str, dict[str, Any]] = {
	"socio": {
		"section_id": "farmer_farmer_socio_economic_and_health_section_04",
		"tab": "farmer",
		"fields": {
			"income_source": "source_of_income",
			"source_of_income": "source_of_income",
			"education_level": "education_level",
			"marital_status": "marital_status",
			"is_psnp_user": "is_psnp_user",
			"disabled": "disabled",
		},
	},
}

_SKIP_RECORD_KEYS = ("documents", "actual_score", "ideal_score", "completion_score_required")


def _truthy(value: Any) -> bool:
	if isinstance(value, bool):
		return value
	if value is None:
		return False
	return str(value).strip().lower() in ("1", "true", "yes", "y")


def _to_float(value: Any) -> float | None:
	try:
		return float(value) if value not in (None, "") else None
	except TypeError, ValueError:
		return None


def _to_int(value: Any) -> int | None:
	try:
		return int(float(value)) if value not in (None, "") else None
	except TypeError, ValueError:
		return None


def _to_date(value: Any) -> date | None:
	if not value:
		return None
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	try:
		return date.fromisoformat(str(value)[:10])
	except ValueError:
		return None


def _to_datetime(value: Any) -> datetime | None:
	if not value:
		return None
	if isinstance(value, datetime):
		return value
	text = str(value).replace("Z", "+00:00")
	try:
		return datetime.fromisoformat(text)
	except ValueError:
		d = _to_date(text)
		return datetime(d.year, d.month, d.day) if d else None


def merge_tab_records(record: dict[str, Any], tab_sections: list) -> dict[str, Any]:
	"""Fold get_tab_records output into a flat farmer record.

	The registry returns [{section_register_id, is_list, records: [...]}].
	Single-row sections (birth, socio-economic, location, household lookup)
	contribute their fields; list sections are kept under "_lists" and phone
	rows under "_phones" for the mapper. Existing keys win, so the identity
	row from get_subject_record is never overwritten by a tab.
	"""
	for section in tab_sections or []:
		if not isinstance(section, dict):
			continue
		rows = [r for r in (section.get("records") or []) if isinstance(r, dict)]
		if not rows:
			continue
		if "phone_number" in rows[0]:
			record.setdefault("_phones", []).extend(rows)
			continue
		if section.get("is_list") and len(rows) > 1:
			record.setdefault("_lists", {})[section.get("section_register_id") or ""] = rows
			continue
		for k, v in rows[0].items():
			if k in _SKIP_RECORD_KEYS:
				continue
			record.setdefault(k, v)
	return record


def registry_record_to_farmer(record: dict[str, Any], farmer_id: str) -> Farmer:
	"""Map a flattened Staff API farmer record (identity row + merged tabs) -> Farmer."""
	phones = record.get("_phones") or []
	primary = next((p for p in phones if _truthy(p.get("is_primary"))), phones[0] if phones else {})
	phone = primary.get("phone_number") or record.get("phone_number") or record.get("phone")

	first = record.get("first_name") or ""
	middle = record.get("middle_name") or record.get("father_first_name") or ""
	last = record.get("last_name") or ""
	record_name = record.get("record_name") or " ".join(p for p in (first, middle, last) if p) or None

	gender = record.get("gender")
	return Farmer(
		farmer_id=record.get("internal_record_id") or farmer_id,
		reg_id=record.get("functional_record_id"),
		given_name=first or None,
		family_name=last or None,
		father_first_name=middle or None,
		record_name=record_name,
		phone=phone,
		email=record.get("email"),
		has_personal_phone=bool(phone),
		region_name=record.get("region_name"),
		zone_name=record.get("zone_name"),
		woreda_name=record.get("woreda_name"),
		kebele_name=record.get("kebele_name"),
		birth_date=_to_date(record.get("birth_date")),
		gender=str(gender).lower() if gender else None,
		education_level=record.get("education_level"),
		is_household_head=_truthy(record.get("is_household_head")),
		household_size=_to_int(record.get("size_of_group") or record.get("household_size")),
		total_land_area=_to_float(record.get("total_land_area")),
		land_ownership=record.get("land_ownership"),
		state=record.get("state") or record.get("record_status") or "UNKNOWN",
		registration_date=_to_datetime(record.get("last_approved_at") or record.get("created_at")),
		source_of_income=record.get("source_of_income"),
		is_psnp_user=_truthy(record.get("is_psnp_user")),
	)


def search_row_to_list_item(row: dict[str, Any]) -> FarmerListItem:
	"""Map one search_in_a_register row (SearchResultData) -> FarmerListItem.

	Column values ride in display_fields: [{field_name, value, order}]; which
	columns appear is registry list-view configuration, so every lookup is
	optional.
	"""
	display: dict[str, Any] = {}
	for f in row.get("display_fields") or []:
		if isinstance(f, dict) and f.get("field_name"):
			display[f["field_name"]] = f.get("value")

	farmer_id = row.get("internal_record_id") or ""
	name = row.get("record_name") or " ".join(
		p for p in (display.get("first_name"), display.get("last_name")) if p
	)
	return FarmerListItem(
		farmer_id=farmer_id,
		reg_id=row.get("functional_record_id") or display.get("functional_record_id"),
		name=name or farmer_id,
		phone=display.get("phone_number") or display.get("phone"),
		kebele=display.get("kebele_name"),
		woreda=display.get("woreda_name"),
		region=display.get("region_name"),
		total_land_area=_to_float(display.get("total_land_area")),
		state=display.get("state") or row.get("state") or "UNKNOWN",
		registration_date=_to_datetime(row.get("last_approved_at") or row.get("created_at")),
	)


def land_record_to_plot(rec: dict[str, Any]) -> Plot:
	"""Map one Land Details row (Lands tab) -> Plot."""
	return Plot(
		plot_id=str(rec.get("land_id") or rec.get("internal_record_id") or ""),
		land_kebele=rec.get("land_kebele"),
		area_in_hectare=_to_float(rec.get("area_in_hectare")),
		land_ownership_type=rec.get("land_ownership_type"),
		soil_fertility=rec.get("soil_fertility"),
		current_land_use=rec.get("current_land_use"),
		certificate_provided=_truthy(rec.get("certificate_provided")),
	)


def updates_to_change_requests(updates: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
	"""Split DA-41 updates into per-section change_payloads.

	Returns (groups, unsupported) where each group is
	{"section_id", "tab", "payload"} and unsupported lists the field names
	the registry cannot accept through a change request.
	"""
	groups: dict[str, dict[str, Any]] = {}
	unsupported: list[str] = []
	for field_name, value in (updates or {}).items():
		if field_name == "client_op_id":
			continue
		placed = False
		for key, section in CHANGE_REQUEST_SECTIONS.items():
			registry_field = section["fields"].get(field_name)
			if registry_field:
				g = groups.setdefault(
					key, {"section_id": section["section_id"], "tab": section["tab"], "payload": {}}
				)
				g["payload"][registry_field] = value
				placed = True
				break
		if not placed:
			unsupported.append(field_name)
	return list(groups.values()), sorted(unsupported)
