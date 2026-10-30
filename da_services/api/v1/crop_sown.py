"""v1: Crop Sown read for the farmer profile.

GET /api/v1/da-services/farmers/{farmer_id}/crops

Searches Crop Sown once per endpoint. Pass reg_id (the profile Farmer-ID)
when the path id is the internal farmer id: submissions are searched by
that Farmer-ID, and the register is searched by the internal id. Pass
submission_id to read one intake submission and its tabs instead.

The Crop Sown staff host is called by the registry client, which sends the
staff bearer and the matching CSRF header and cookie. This route checks the
DA caller's role and that the farmer is in scope.
"""

from __future__ import annotations

import frappe
from frappe import _
from oan_auth_service.api.utils import error_response, handle_api_errors, success_response

from da_services.api.router import route
from da_services.integrations.crop_registry import get_crop_registry_client
from da_services.integrations.crop_registry.client import CropRegistryClientError, CropRegistryServerError
from da_services.integrations.crop_registry.intake import _SCREEN_SECTIONS
from da_services.integrations.crop_registry.schemas import CropSubmission
from da_services.integrations.farmer_registry.client import (
	FarmerRegistryClientError,
	FarmerRegistryServerError,
)
from da_services.services import constants as C
from da_services.services import farmers
from da_services.utils.permissions import require_roles

READ_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)
WRITE_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_ADMIN)
_UNAVAILABLE = _("Crop Sown Registry is currently unavailable. Try again shortly.")
_FRAMEWORK_KEYS = {"cmd", "_", "sid"}


def _farmer_is_in_registry(farmer_id: str, ctx) -> bool:
	"""True when this id is a Farmer Registry subject in the caller's scope.

	A Crop Sown Farmer-ID (``FR-…``) is often the submission name, not a Farmer
	Registry subject. ``get_farmer_profile`` records "not found" before it
	raises, so that text is cleared and the Crop Sown call continues.
	"""
	try:
		farmers.get_farmer_profile(farmer_id, ctx)
	except frappe.DoesNotExistError:
		frappe.local.message_log = []
		response = getattr(frappe.local, "response", None)
		if isinstance(response, dict):
			response.pop("http_status_code", None)
		return False
	return True


def _internal_farmer_id(farmer_id: str) -> str:
	"""Accept the registry's internal id or the Farmer-ID shown in the UI (FR-…)."""
	if frappe.db.exists("DA Farmer Reference", farmer_id):
		return farmer_id
	cached = frappe.db.get_value("DA Farmer Reference", {"reg_id": farmer_id}, "name")
	return cached or farmer_id


def _submission_reg_id(path_id: str, resolved_id: str, *, in_registry: bool) -> str | None:
	"""Farmer-ID used as the Crop Sown submission name, when it differs.

	Taken from DA Farmer Reference, or from the path when that id is the
	Farmer-ID. A query ``reg_id`` for a different farmer is rejected.
	"""
	passed = (frappe.form_dict.get("reg_id") or "").strip()
	cached = ""
	if in_registry:
		cached = str(frappe.db.get_value("DA Farmer Reference", resolved_id, "reg_id") or "").strip()
	if passed and cached and passed != cached:
		frappe.throw(_("reg_id does not match this farmer."), frappe.ValidationError)
	if passed and not in_registry and passed not in {path_id, resolved_id}:
		frappe.throw(_("reg_id does not match this farmer."), frappe.ValidationError)
	reg = cached or (path_id if path_id != resolved_id else "")
	if not reg or reg == resolved_id:
		return None
	return reg


def crop_sown_payload(farmer_id: str, submission: CropSubmission, tabs: list[dict]) -> dict:
	"""Shape a staff read for the profile's Crop Sown tab."""
	return {
		"farmer_id": farmer_id,
		"submission_id": submission.submission_id,
		"draft_status": submission.draft_status,
		"approval_status": submission.approval_status,
		"sections": [section.model_dump() for section in submission.sections],
		"tabs": tabs,
	}


@route("/farmers/<farmer_id>/crops", methods=("GET",), summary="List a farmer's Crop Sown records")
@frappe.whitelist()
@handle_api_errors
def get_farmer_crops(farmer_id: str):
	"""List Crop Sown register rows linked to this farmer.

	``submission_id`` reads that one intake submission and its tabs.

	Without it, the route searches Crop Sown once. Submissions are searched
	with the Farmer-ID (``reg_id`` from the profile, or the same value already
	stored on DA Farmer Reference). The register is searched with the internal
	id. Query ``reg_id`` must match that farmer. The farmer register id is not
	a Crop Sown register id and is not accepted here.

	The id in the path may be the Farmer-ID printed on the Crop Sown form
	(``FR-…``). That number is not always a Farmer Registry subject id, so a
	missing registry profile does not block the Crop Sown read. When the
	farmer is in the registry, the profile lookup still enforces scope.
	"""
	ctx = require_roles(*READ_ROLES)
	if not farmer_id:
		frappe.throw(_("Farmer ID is required."), frappe.ValidationError)

	submission_id = (frappe.form_dict.get("submission_id") or "").strip()
	requested_id = farmer_id
	try:
		resolved_id = _internal_farmer_id(farmer_id)
		in_registry = _farmer_is_in_registry(resolved_id, ctx)
		if in_registry:
			farmer_id = resolved_id
		client = get_crop_registry_client()
		if not submission_id:
			search_id = farmer_id if in_registry else requested_id
			reg_id = _submission_reg_id(requested_id, search_id, in_registry=in_registry)
			crops = client.list_for_farmer(search_id, reg_id=reg_id)
			if not crops and not in_registry:
				frappe.throw(_("Farmer {0} not found.").format(requested_id), frappe.DoesNotExistError)
			return success_response(data={"farmer_id": requested_id, "crops": crops})
		submission = client.get_submission(submission_id)
		tabs = client.get_tabs(submission_id) if submission is not None else []
	except CropRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except FarmerRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except CropRegistryServerError, FarmerRegistryServerError:
		frappe.response["http_status_code"] = 503
		return error_response(str(_UNAVAILABLE), "SERVICE_UNAVAILABLE")

	if submission is None:
		frappe.throw(_("Crop Sown submission {0} not found.").format(submission_id), frappe.DoesNotExistError)

	return success_response(data=crop_sown_payload(farmer_id, submission, tabs))


def _sections_from_values(values: dict) -> list[dict]:
	"""Group ``identity.crop_year`` keys into one record per Submit Crop Report section."""
	grouped: dict[str, dict] = {}
	for key, value in values.items():
		text = str(key)
		if "." not in text:
			frappe.throw(
				_("Crop Report field {0} must be section.field.").format(text),
				frappe.ValidationError,
			)
		section_id, field_name = text.split(".", 1)
		if not section_id or not field_name:
			frappe.throw(
				_("Crop Report field {0} must be section.field.").format(text),
				frappe.ValidationError,
			)
		grouped.setdefault(section_id, {})[field_name] = value
	if not grouped:
		frappe.throw(_("At least one section is required."), frappe.ValidationError)
	order = list(_SCREEN_SECTIONS)
	return [
		{"section_id": section_id, "records": [grouped[section_id]]}
		for section_id in sorted(
			grouped, key=lambda name: (order.index(name) if name in order else len(order), name)
		)
	]


def _section_requests(body: dict) -> tuple[str | None, str | None, list]:
	"""One section object, a ``sections`` list, or the screen's ``values`` map."""
	submission_id = body.get("submission_id") or None
	subject_record_id = body.get("subject_record_id") or None
	values = body.get("values")
	if isinstance(values, dict) and body.get("sections") is None and "records" not in body:
		return submission_id, subject_record_id, _sections_from_values(values)
	sections = body.get("sections")
	if sections is None:
		sections = [body]
	if not isinstance(sections, list) or not sections:
		frappe.throw(_("At least one section is required."), frappe.ValidationError)
	return submission_id, subject_record_id, sections


@route("/crop-sown/intake-form", methods=("GET",), summary="Current Crop Sown intake form")
@frappe.whitelist()
@handle_api_errors
def get_crop_intake_form(form: str | None = None):
	"""Return one live intake form: sections, labels, static options, and catalog rows.

	Query ``form`` selects the staff form: ``planning`` (default), ``cultivation``,
	``sowing``, or ``harvesting``. The Crop Sown tab is ``planning``. That form
	holds farmer identity, season, and location. Catalog rows are loaded only
	when ``crop_registry_master_data_url`` is set. Geography is a separate call.
	"""
	require_roles(*READ_ROLES)
	form_name = (form or frappe.form_dict.get("form") or "").strip() or None
	try:
		described = get_crop_registry_client().describe_intake_form(form_name=form_name)
	except CropRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except CropRegistryServerError:
		frappe.response["http_status_code"] = 503
		return error_response(str(_UNAVAILABLE), "SERVICE_UNAVAILABLE")
	return success_response(data=described)


@route("/crop-sown/geo-level-values", methods=("GET",), summary="One Crop Sown geography level")
@frappe.whitelist()
@handle_api_errors
def get_crop_geo_level_values():
	"""Return one geography level from master data.

	Query ``level_id`` is the staff level id, forwarded unchanged. The planning
	run used ``l1`` (region), then ``l2``, ``l3``, and ``l4`` with
	``parent_level_value_id`` set to the selected parent's ``level_value_id``.
	This route does not load every kebele.
	"""
	require_roles(*READ_ROLES)
	level_id = (frappe.form_dict.get("level_id") or "").strip()
	parent = (frappe.form_dict.get("parent_level_value_id") or "").strip() or None
	if not level_id:
		frappe.throw(_("level_id is required."), frappe.ValidationError)
	try:
		rows = get_crop_registry_client().geo_level_values(level_id, parent)
	except CropRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except CropRegistryServerError:
		frappe.response["http_status_code"] = 503
		return error_response(str(_UNAVAILABLE), "SERVICE_UNAVAILABLE")
	return success_response(data={"level_id": level_id, "parent_level_value_id": parent, "values": rows})


def _request_body() -> dict:
	request_data = None
	try:
		request_data = frappe.request.get_json(silent=True) if frappe.request else None
	except RuntimeError:
		request_data = None
	if not request_data and frappe.form_dict:
		request_data = {key: value for key, value in frappe.form_dict.items() if key not in _FRAMEWORK_KEYS}
	if not isinstance(request_data, dict) or not request_data:
		frappe.throw(_("Request body is required and must be valid JSON."), frappe.ValidationError)
	return request_data


def _open_crop_client(farmer_id: str, ctx):
	"""Resolve the farmer and return (requested id, crop client). Scope applies when the farmer exists."""
	_farmer_is_in_registry(_internal_farmer_id(farmer_id), ctx)
	return farmer_id, get_crop_registry_client()


@route("/farmers/<farmer_id>/crops", methods=("POST",), summary="Save a Crop Sown intake section")
@frappe.whitelist()
@handle_api_errors
def save_farmer_crop(farmer_id: str):
	"""Save one Crop Sown intake section.

	Crop Sown has no ingest endpoint. The staff collection creates a draft by
	posting one section at a time to ``save_intake_form_submission``. Omit
	``submission_id`` on the first section. Send it back on the following
	sections so they stay on the same draft.

	Records use the registry's own field names. The route loads the current
	intake form and field definitions, checks types, required fields, and
	allowed values, then posts ``save_intake_form_submission``. A field added
	or removed in that metadata is accepted or rejected with no code change.

	One section::

	    {"section_id": "planning", "records": [{"season": "Meher", "planned_area": "1.2"}]}

	``section_id`` may be the staff section id or a Submit Crop Report id:
	``identity``, ``address``, ``planning``, ``planning-cluster``,
	``cultivation``, ``cultivation-cluster``, ``sowing``, ``infestation``,
	``harvest``. Field names are the register names. Strings are converted to
	the register type, and a choice label is stored as the staff code.

	The screen's value map is accepted as well::

	    {"values": {"identity.crop_year": "2026", "planning.season": "Meher"}}

	Several sections, chained onto one draft::

	    {"submission_id": null, "sections": [{"section_id": "...", "records": [{}]}]}

	``section_mnemonic`` or ``section_register_id`` may be sent instead of ``section_id``. ``section_payload``
	is accepted as another name for ``records``.
	"""
	ctx = require_roles(*WRITE_ROLES)
	if not farmer_id:
		frappe.throw(_("Farmer ID is required."), frappe.ValidationError)
	body = _request_body()
	form_name = body.get("form") or None
	if form_name is not None and not isinstance(form_name, str):
		frappe.throw(_("form must be a string."), frappe.ValidationError)
	submission_id, subject_record_id, requests = _section_requests(body)
	try:
		requested_id, client = _open_crop_client(farmer_id, ctx)
		saved = client.submit_sections(
			requests,
			submission_id=submission_id,
			subject_record_id=subject_record_id,
			form_name=form_name,
		)
	except CropRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except FarmerRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except CropRegistryServerError, FarmerRegistryServerError:
		frappe.response["http_status_code"] = 503
		return error_response(str(_UNAVAILABLE), "SERVICE_UNAVAILABLE")

	return success_response(data={"farmer_id": requested_id, **saved})


@route(
	"/farmers/<farmer_id>/crops/<submission_id>/finalize",
	methods=("POST",),
	summary="Finalize a Crop Sown submission",
)
@frappe.whitelist()
@handle_api_errors
def finalize_farmer_crop(farmer_id: str, submission_id: str):
	"""Finalize a Crop Sown draft through the staff approval workflow.

	Staff call: ``POST /intake-form-data/finalize_intake_form_submission``
	with ``{submission_id}``. A successful finalize is ``FINAL`` and ``PENDING``.
	"""
	ctx = require_roles(*WRITE_ROLES)
	if not farmer_id or not submission_id:
		frappe.throw(_("Farmer ID and submission ID are required."), frappe.ValidationError)
	try:
		requested_id, client = _open_crop_client(farmer_id, ctx)
		finalized = client.finalize_submission(submission_id)
	except CropRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except FarmerRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except CropRegistryServerError, FarmerRegistryServerError:
		frappe.response["http_status_code"] = 503
		return error_response(str(_UNAVAILABLE), "SERVICE_UNAVAILABLE")

	return success_response(data={"farmer_id": requested_id, **finalized})
