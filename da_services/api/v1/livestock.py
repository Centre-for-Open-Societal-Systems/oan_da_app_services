"""v1: Livestock for the farmer profile and the Livestock form.

GET  /api/v1/da-services/livestock/intake-form
GET  /api/v1/da-services/farmers/{farmer_id}/livestock[?submission_id=]
POST /api/v1/da-services/farmers/{farmer_id}/livestock
POST /api/v1/da-services/farmers/{farmer_id}/livestock/{submission_id}/finalize

The frontend renders the Livestock form from the intake-form response and
saves through these routes. DA Services checks the caller's role and the
farmer's scope; the registry client forwards the caller's SSO headers to the
Livestock staff host.
"""

from __future__ import annotations

import frappe
from frappe import _
from oan_auth_service.api.utils import error_response, handle_api_errors, success_response

from da_services.api.router import route
from da_services.integrations.farmer_registry.client import (
	FarmerRegistryClientError,
	FarmerRegistryServerError,
)
from da_services.integrations.livestock_registry import get_livestock_registry_client
from da_services.integrations.livestock_registry.client import (
	LivestockRegistryClientError,
	LivestockRegistryServerError,
)
from da_services.integrations.livestock_registry.schemas import LivestockSubmission
from da_services.services import constants as C
from da_services.services import farmers

_UNAVAILABLE = _("Livestock Registry is currently unavailable. Try again shortly.")
_FRAMEWORK_KEYS = {"cmd", "_", "sid"}


def _unavailable():
	frappe.response["http_status_code"] = 503
	return error_response(str(_UNAVAILABLE), "SERVICE_UNAVAILABLE")


def _farmer_reg_id(farmer_id: str) -> str:
	"""Farmer-ID (``FR-…``) for the path id, after the caller's scope check.

	Livestock records name the farmer by Farmer-ID. The path may hold it, or
	the registry's internal id cached on DA Farmer Reference. A farmer missing
	from the Farmer Registry does not block the Livestock call, the same as
	Crop Sown.
	"""
	internal_id = farmer_id
	if not frappe.db.exists("DA Farmer Reference", farmer_id):
		internal_id = frappe.db.get_value("DA Farmer Reference", {"reg_id": farmer_id}, "name") or farmer_id
	try:
		farmers.get_farmer_profile(internal_id)
	except frappe.DoesNotExistError:
		frappe.local.message_log = []
		response = getattr(frappe.local, "response", None)
		if isinstance(response, dict):
			response.pop("http_status_code", None)
	if farmer_id.startswith("FR-"):
		return farmer_id
	reg_id = frappe.db.get_value("DA Farmer Reference", internal_id, "reg_id")
	if not reg_id:
		frappe.throw(_("Use the Farmer-ID (FR-…) for Livestock."), frappe.ValidationError)
	return str(reg_id)


def _draft_farmer_ids(submission: LivestockSubmission) -> set[str]:
	"""Farmer-IDs saved in the draft's records (the lookup row's ``farmer_id``)."""
	return {
		str(row["farmer_id"]).strip()
		for section in submission.sections
		for row in section.records
		if isinstance(row, dict) and str(row.get("farmer_id") or "").strip()
	}


def _check_owner(submission: LivestockSubmission | None, submission_id: str, reg_id: str) -> None:
	"""Refuse a draft saved for another farmer.

	The owner is the ``farmer_id`` saved in the draft's lookup row: DA Services
	fills it from the route on every save. ``record_name`` is the registry's
	display name for the record (often the farmer's name), so it is only used
	when the draft has no ``farmer_id`` at all.
	"""
	if submission is None:
		frappe.throw(_("Livestock submission {0} not found.").format(submission_id), frappe.DoesNotExistError)
	farmer_ids = _draft_farmer_ids(submission)
	if farmer_ids:
		owned = farmer_ids == {reg_id}
	else:
		owned = not submission.record_name or submission.record_name == reg_id
	if not owned:
		frappe.throw(
			_("Livestock submission {0} is not for this farmer.").format(submission_id),
			frappe.PermissionError,
		)


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


def _section_requests(body: dict) -> list:
	"""A ``sections`` list, or one section object."""
	sections = body.get("sections")
	if sections is None:
		sections = [body]
	if not isinstance(sections, list) or not sections:
		frappe.throw(_("At least one section is required."), frappe.ValidationError)
	return sections


@route("/livestock/intake-form", methods=("GET",), summary="Livestock intake form for the frontend")
@frappe.whitelist()
@handle_api_errors
def get_livestock_intake_form():
	"""The live Livestock intake form, ready to render.

	Each section lists its fields in screen order: widget kind, label, data
	type, required, read-only, placeholder, pattern, static ``options`` or
	catalog ``choices`` (store ``value_id``), and the form's ``rules``
	(``show`` / ``require`` with a condition on another field of the record).
	A list section (``is_list``) is a table: ``table`` has its label and
	dialog titles, and ``fields`` are its columns.
	"""
	try:
		described = get_livestock_registry_client().describe_intake_form()
	except LivestockRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except LivestockRegistryServerError:
		return _unavailable()
	return success_response(data=described)


@route("/farmers/<farmer_id>/livestock", methods=("GET",), summary="A farmer's Livestock records")
@frappe.whitelist()
@handle_api_errors
def get_farmer_livestock(farmer_id: str):
	"""The farmer's Livestock submissions and the full rows of each section.

	``kind`` is ``submission`` or ``record``. A submission includes ``sections``,
	each with its records. A record is the full section row: every column the
	registry stores for that holding, animal, health event, vaccination, vital
	event, breeding row, or other section of the intake form. The screen pairs
	these values with labels from ``GET /livestock/intake-form``. With
	``submission_id`` the route returns that one submission.
	"""
	submission_id = (frappe.form_dict.get("submission_id") or "").strip()
	try:
		reg_id = _farmer_reg_id(farmer_id)
		client = get_livestock_registry_client()
		if not submission_id:
			return success_response(data={"farmer_id": reg_id, "livestock": client.list_for_farmer(reg_id)})
		submission = client.get_submission(submission_id)
	except (LivestockRegistryClientError, FarmerRegistryClientError) as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except LivestockRegistryServerError, FarmerRegistryServerError:
		return _unavailable()
	_check_owner(submission, submission_id, reg_id)
	return success_response(data={"farmer_id": reg_id, **submission.model_dump()})


@route("/farmers/<farmer_id>/livestock", methods=("POST",), summary="Save Livestock intake sections")
@frappe.whitelist()
@handle_api_errors
def save_farmer_livestock(farmer_id: str):
	"""Save one or more Livestock sections into one draft.

	First call: no ``submission_id``. The farmer lookup section is filled with
	this farmer's Farmer-ID. Send the returned ``submission_id`` (and
	``subject_record_id``) with later sections::

	    {
	        "submission_id": null,
	        "sections": [
	            {"section_id": "livestock_farmer_lookup", "records": [{"fayda_id": "123456789012"}]},
	            {
	                "section_id": "livestock_animal_details_section_01",
	                "records": [{"ear_tag_id": "ET-1", "species": "CATTLE", "gender": "SEX_FEMALE"}],
	            },
	        ],
	    }

	Field names, required flags, rules and allowed values come from the live
	form; the error names the section, row and field that failed. When a
	section fails after earlier ones were saved, the error body carries
	``data: {submission_id, subject_record_id, saved_sections}`` so a retry
	continues that draft and sends only the other sections.
	"""
	body = _request_body()
	submission_id = body.get("submission_id") or None
	subject_record_id = body.get("subject_record_id") or None
	requests = _section_requests(body)
	try:
		reg_id = _farmer_reg_id(farmer_id)
		client = get_livestock_registry_client()
		if submission_id:
			_check_owner(client.get_submission(submission_id), submission_id, reg_id)
		saved = client.submit_sections(
			requests,
			submission_id=submission_id,
			subject_record_id=subject_record_id,
			farmer_id=reg_id,
		)
	except (LivestockRegistryClientError, FarmerRegistryClientError) as exc:
		partial = getattr(exc, "partial", None)
		if not partial:
			frappe.throw(str(exc), frappe.ValidationError)
		frappe.response["http_status_code"] = 400
		return {**error_response(str(exc), "VALIDATION_ERROR"), "data": {"farmer_id": reg_id, **partial}}
	except (LivestockRegistryServerError, FarmerRegistryServerError) as exc:
		partial = getattr(exc, "partial", None)
		return {**_unavailable(), "data": {"farmer_id": reg_id, **partial}} if partial else _unavailable()
	return success_response(data={"farmer_id": reg_id, **saved})


@route(
	"/farmers/<farmer_id>/livestock/<submission_id>/finalize",
	methods=("POST",),
	summary="Finalize a Livestock submission",
)
@frappe.whitelist()
@handle_api_errors
def finalize_farmer_livestock(farmer_id: str, submission_id: str):
	"""Send the draft for approval. A successful finalize is ``FINAL`` and ``PENDING``."""
	try:
		reg_id = _farmer_reg_id(farmer_id)
		client = get_livestock_registry_client()
		_check_owner(client.get_submission(submission_id), submission_id, reg_id)
		finalized = client.finalize_submission(submission_id)
	except (LivestockRegistryClientError, FarmerRegistryClientError) as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except LivestockRegistryServerError, FarmerRegistryServerError:
		return _unavailable()
	return success_response(data={"farmer_id": reg_id, **finalized})
