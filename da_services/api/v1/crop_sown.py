"""v1: Crop Sown read for the farmer profile.

GET /api/v1/da-services/farmers/{farmer_id}/crops

Searches the Crop Sown register. A row belongs to the farmer when its
link_internal_record_id matches. Pass submission_id to read one intake
submission and its tabs instead.

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
from da_services.integrations.crop_registry.schemas import CropSubmission
from da_services.integrations.farmer_registry import get_farmer_registry_client
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


def _internal_farmer_id(farmer_id: str) -> str:
	"""Accept the registry's internal id or the Farmer-ID shown in the UI (FR-…)."""
	if frappe.db.exists("DA Farmer Reference", farmer_id):
		return farmer_id
	cached = frappe.db.get_value("DA Farmer Reference", {"reg_id": farmer_id}, "name")
	if cached:
		return cached
	finder = getattr(get_farmer_registry_client(), "find_internal_id", None)
	found = finder(farmer_id) if finder else None
	return found or farmer_id


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

	``submission_id`` is optional. Without it, the route searches the Crop Sown
	register and keeps rows whose ``link_internal_record_id`` is this farmer.
	With it, the route reads that one intake submission and its tabs.

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
		in_registry = False
		try:
			farmers.get_farmer_profile(resolved_id, ctx)
			in_registry = True
		except frappe.DoesNotExistError:
			# Crop Sown names the form with the Farmer-ID even when that number is
			# not a Farmer Registry subject. Continue and read the submission.
			pass
		else:
			farmer_id = resolved_id
		client = get_crop_registry_client()
		if not submission_id:
			crops = []
			seen = set()
			for candidate in dict.fromkeys((requested_id, farmer_id)):
				for row in client.list_for_farmer(candidate):
					key = str(row.get("submission_id") or row.get("internal_record_id") or id(row))
					if key in seen:
						continue
					seen.add(key)
					crops.append(row)
			if not crops and not in_registry:
				frappe.throw(_("Farmer {0} not found.").format(requested_id), frappe.DoesNotExistError)
			return success_response(data={"farmer_id": requested_id, "crops": crops})
		submission = client.get_submission(submission_id)
		tabs = client.get_tabs(submission_id) if submission is not None else []
	except CropRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except FarmerRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except (CropRegistryServerError, FarmerRegistryServerError):
		frappe.response["http_status_code"] = 503
		return error_response(str(_UNAVAILABLE), "SERVICE_UNAVAILABLE")

	if submission is None:
		frappe.throw(_("Crop Sown submission {0} not found.").format(submission_id), frappe.DoesNotExistError)

	return success_response(data=crop_sown_payload(farmer_id, submission, tabs))


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
	requested_id = farmer_id
	resolved_id = _internal_farmer_id(farmer_id)
	try:
		farmers.get_farmer_profile(resolved_id, ctx)
	except frappe.DoesNotExistError:
		pass
	return requested_id, get_crop_registry_client()


@route("/farmers/<farmer_id>/crops", methods=("POST",), summary="Save a Crop Sown intake section")
@frappe.whitelist()
@handle_api_errors
def save_farmer_crop(farmer_id: str):
	"""Save one Crop Sown intake section.

	Crop Sown has no ingest endpoint. The staff collection creates a draft by
	posting one section at a time to ``save_intake_form_submission``. Omit
	``submission_id`` on the first section. Send it back on the following
	sections so they stay on the same draft.

	Request body:
	    {
	        "section_id": "...",
	        "section_register_id": "...",
	        "section_payload": [{ "...": "..." }],
	        "submission_id": null,
	        "form_id": "...",
	        "register_id": "..."
	    }

	``form_id`` and ``register_id`` are optional. When they are omitted the
	client uses the discovered CropSown Planning form.
	"""
	ctx = require_roles(*WRITE_ROLES)
	if not farmer_id:
		frappe.throw(_("Farmer ID is required."), frappe.ValidationError)
	body = _request_body()
	try:
		requested_id, client = _open_crop_client(farmer_id, ctx)
		saved = client.save_section(
			section_id=str(body.get("section_id") or ""),
			section_register_id=str(body.get("section_register_id") or ""),
			section_payload=body.get("section_payload"),
			submission_id=(body.get("submission_id") or None),
			form_id=(body.get("form_id") or None),
			register_id=(body.get("register_id") or None),
		)
	except CropRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except FarmerRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except (CropRegistryServerError, FarmerRegistryServerError):
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
	except (CropRegistryServerError, FarmerRegistryServerError):
		frappe.response["http_status_code"] = 503
		return error_response(str(_UNAVAILABLE), "SERVICE_UNAVAILABLE")

	return success_response(data={"farmer_id": requested_id, **finalized})
