"""Mapper: Farmer Registry ↔ DA backend field transformations.

Handles naming and format differences between systems.
"""

import uuid
from typing import Any

from .schemas import Farmer


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
	return {
		"header": {
			"message_id": message_id,
			"sender_id": "da-services",
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
