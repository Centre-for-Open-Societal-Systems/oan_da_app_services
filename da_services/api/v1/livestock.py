"""v1: Livestock for the farmer profile and the Livestock form.

GET  /api/v1/da-services/livestock/intake-form
GET  /api/v1/da-services/farmers/{farmer_id}/livestock

List requires ``source=submission`` or ``source=register`` (one staff search).
Pass ``submission_id`` for one intake submission, or ``internal_record_id``
with required ``tab_id`` for one register tab (``/register-data/get_tab_records``).

POST /api/v1/da-services/farmers/{farmer_id}/livestock
POST /api/v1/da-services/farmers/{farmer_id}/livestock/{submission_id}/finalize
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
from da_services.integrations.staff_registry import parse_registry_get_query
from da_services.services import farmers
from da_services.services.utils import StaffAPIError

_UNAVAILABLE = _("Livestock Registry is currently unavailable. Try again shortly.")
_FRAMEWORK_KEYS = {"cmd", "_", "sid"}


def _unavailable():
	frappe.response["http_status_code"] = 503
	return error_response(str(_UNAVAILABLE), "SERVICE_UNAVAILABLE")


def _farmer_reg_id(farmer_id: str) -> str:
	"""Farmer-ID (``FR-…``) for the path id, after the caller's scope check."""
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


def _check_owner(submission: LivestockSubmission | None, submission_id: str, reg_id: str) -> None:
	if submission is None:
		frappe.throw(_("Livestock submission {0} not found.").format(submission_id), frappe.DoesNotExistError)
	if submission.record_name and submission.record_name != reg_id:
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
	"""The live Livestock intake form, ready to render."""
	try:
		described = get_livestock_registry_client().describe_intake_form()
	except (LivestockRegistryClientError, StaffAPIError) as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except LivestockRegistryServerError:
		return _unavailable()
	return success_response(data=described)


@route("/farmers/<farmer_id>/livestock", methods=("GET",), summary="A farmer's Livestock records")
@frappe.whitelist()
@handle_api_errors
def get_farmer_livestock(farmer_id: str):
	"""List or detail Livestock data for one farmer.

	List requires ``source``: ``submission`` or ``register`` (one staff search).
	``submission_id`` reads one intake submission.
	``internal_record_id`` with required ``tab_id`` reads one register tab.
	"""
	if not farmer_id:
		frappe.throw(_("Farmer ID is required."), frappe.ValidationError)

	submission_id, internal_record_id, tab_id, source = parse_registry_get_query(frappe.form_dict)
	try:
		reg_id = _farmer_reg_id(farmer_id)
		client = get_livestock_registry_client()
		if internal_record_id:
			detail = client.get_register_tab(internal_record_id, tab_id)
			return success_response(data={"farmer_id": reg_id, **detail})
		if not submission_id:
			livestock = client.list_for_farmer(reg_id, source=source)
			return success_response(data={"farmer_id": reg_id, "livestock": livestock, "source": source})
		submission = client.get_submission(submission_id)
	except (LivestockRegistryClientError, StaffAPIError, FarmerRegistryClientError) as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except LivestockRegistryServerError, FarmerRegistryServerError:
		return _unavailable()
	_check_owner(submission, submission_id, reg_id)
	return success_response(data={"farmer_id": reg_id, **submission.model_dump()})


@route("/farmers/<farmer_id>/livestock", methods=("POST",), summary="Save Livestock intake sections")
@frappe.whitelist()
@handle_api_errors
def save_farmer_livestock(farmer_id: str):
	"""Save one or more Livestock sections into one draft."""
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
	except (LivestockRegistryClientError, StaffAPIError, FarmerRegistryClientError) as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except LivestockRegistryServerError, FarmerRegistryServerError:
		return _unavailable()
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
	except (LivestockRegistryClientError, StaffAPIError, FarmerRegistryClientError) as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except LivestockRegistryServerError, FarmerRegistryServerError:
		return _unavailable()
	return success_response(data={"farmer_id": reg_id, **finalized})
