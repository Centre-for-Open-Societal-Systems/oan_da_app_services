"""v1: Registry metadata read through DA Services (DA-272).

GET /api/v1/da-services/registries/{registry}/registers
GET /api/v1/da-services/registries/{registry}/intake-form
GET /api/v1/da-services/registries/{registry}/field-options
GET /api/v1/da-services/registries/{registry}/places?level=&parent=

Only ``farmer`` is wired today. The ids come from the registry itself
(get_all_registers, get_all_intake_forms, render_intake_form) and are cached,
so the frontend can build the registration wizard from the live form instead
of a hard-coded section list.
"""

from __future__ import annotations

import frappe
from frappe import _
from oan_auth_service.api.utils import error_response, handle_api_errors, success_response

from da_services.api.router import route
from da_services.integrations.farmer_registry import get_farmer_registry_client, intake
from da_services.integrations.farmer_registry.client import (
	FarmerRegistryClientError,
	FarmerRegistryError,
	FarmerRegistryServerError,
)
from da_services.services import constants as C
from da_services.utils.permissions import require_roles

READ_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)
REGISTRIES = ("farmer",)
_UNAVAILABLE = _("Farmer Registry is unavailable. Try again shortly.")

# Fields of a register row worth showing; the rest is registry tuning.
_REGISTER_FIELDS = (
	"register_id",
	"register_mnemonic",
	"register_description",
	"register_subject",
	"master_register_mnemonic",
	"has_data",
	"dedup_is_enabled",
	"functional_id_generation_required",
)


def _check_registry(registry: str) -> None:
	if (registry or "").lower() not in REGISTRIES:
		frappe.throw(
			_("Unknown registry {0}. Supported: {1}.").format(registry, ", ".join(REGISTRIES)),
			frappe.DoesNotExistError,
		)


@route("/registries/<registry>/registers", methods=("GET",), summary="List a registry's registers")
@frappe.whitelist()
@handle_api_errors
def list_registers(registry: str):
	"""Registers of a registry with their ids (register-metadata/get_all_registers).

	Example: the Farmer Registry answers Farmer, Household, HouseholdMember,
	FarmerPhone, Land, Crop, Livestock, FarmInputs, MembershipDetails, Score.
	The ids are what every other registry call keys on; cached for an hour.
	"""
	require_roles(*READ_ROLES)
	_check_registry(registry)
	client = get_farmer_registry_client()
	try:
		rows = intake.load_registers(client)
	except FarmerRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except FarmerRegistryServerError:
		frappe.response["http_status_code"] = 503
		return error_response(str(_UNAVAILABLE), "SERVICE_UNAVAILABLE")
	registers = [{k: row.get(k) for k in _REGISTER_FIELDS} for row in rows if isinstance(row, dict)]
	return success_response(
		data={"registry": registry.lower(), "registers": registers, "total": len(registers)}
	)


@route("/registries/<registry>/intake-form", methods=("GET",), summary="Layout of a registry's intake form")
@frappe.whitelist()
@handle_api_errors
def get_intake_form(registry: str):
	"""The intake form POST /farmers writes to, section by section, in save order.

	Returns {form_id, register_id, sections: [{section_id, section_register_id,
	register_mnemonic, is_list, order, has_identity_document}]}. The form id is
	discovered from get_all_intake_forms when it is not configured.
	"""
	require_roles(*READ_ROLES)
	_check_registry(registry)
	client = get_farmer_registry_client()
	try:
		layout = intake.load_layout(client)
	except FarmerRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except FarmerRegistryServerError:
		frappe.response["http_status_code"] = 503
		return error_response(str(_UNAVAILABLE), "SERVICE_UNAVAILABLE")
	except FarmerRegistryError as exc:
		# Misconfiguration (no unique form, missing master data url): say which.
		frappe.throw(str(exc), frappe.ValidationError)
	return success_response(data={"registry": registry.lower(), **layout})


@route(
	"/registries/<registry>/field-options",
	methods=("GET",),
	summary="Option lists of the intake form dropdowns",
)
@frappe.whitelist()
@handle_api_errors
def get_field_options(registry: str):
	"""Every dropdown on the registry's intake form with its allowed values.

	Returns {"registry", "options": {register: {field: [{"value", "label"}]}}}, for
	example options.Farmer.gender or options.Land.soil_fertility. The code list a
	field uses comes from the registry form itself, so the frontend can draw its
	dropdowns without hard-coded values. Send the chosen value back to POST /farmers.
	Cached for an hour.
	"""
	require_roles(*READ_ROLES)
	_check_registry(registry)
	client = get_farmer_registry_client()
	try:
		options = intake.load_field_options(client)
	except FarmerRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except FarmerRegistryServerError:
		frappe.response["http_status_code"] = 503
		return error_response(str(_UNAVAILABLE), "SERVICE_UNAVAILABLE")
	except FarmerRegistryError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	return success_response(data={"registry": registry.lower(), "options": options})


@route(
	"/registries/<registry>/places", methods=("GET",), summary="Master Data places for the location picker"
)
@frappe.whitelist()
@handle_api_errors
def list_places(registry: str, level=None, parent=None):
	"""Region > Zone > Woreda > Kebele, one level at a time (Master Data).

	Query: level=region|zone|woreda|kebele, parent=<id of the place above> (not
	for region). Returns {"level", "parent", "places": [{"id", "name"}]}; the
	kebele id (for example ET04080101) is what POST /farmers takes as kebele.
	Cached for a day.
	"""
	require_roles(*READ_ROLES)
	_check_registry(registry)
	level = (level or frappe.form_dict.get("level") or "region").strip().lower()
	parent = (parent or frappe.form_dict.get("parent") or "").strip() or None
	client = get_farmer_registry_client()
	try:
		places = intake.load_places(client, level, parent)
	except intake.IntakeFormError as exc:
		frappe.throw("; ".join(exc.problems), frappe.ValidationError)
	except FarmerRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except FarmerRegistryServerError:
		frappe.response["http_status_code"] = 503
		return error_response(str(_UNAVAILABLE), "SERVICE_UNAVAILABLE")
	except FarmerRegistryError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	return success_response(data={"level": level, "parent": parent, "places": places})
