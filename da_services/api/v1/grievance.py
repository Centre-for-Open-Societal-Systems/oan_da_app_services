"""v1 Grievance API: proxy to OAN Grievance Service, scoped by the caller's RBAC.

Writes that fail against the upstream service are queued in DA Integration Event and
retried automatically. Reads are pass-through; if the upstream is down the caller gets
a 503 error.
"""

from __future__ import annotations

import uuid

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import error_response, handle_api_errors, success_response

from da_services.integrations.grievance.client import (
	GrievanceNotFound,
	GrievanceRejected,
	GrievanceServiceError,
	GrievanceServiceUnavailable,
	get_client,
)
from da_services.integrations.grievance.queue import enqueue_event, retry_failed
from da_services.integrations.grievance.schemas import GrievanceSubmission
from da_services.services import constants as C
from da_services.utils.permissions import require_roles

grievance_route = prefixed("/api/v1/da-services")

_UNAVAILABLE_MSG = _("Grievance Service is currently unavailable. Try again shortly.")
_UPSTREAM_MSG = _("Grievance Service could not complete the request. Try again shortly.")

# Filters for fields the list itself returns: status, category, location,
# and the created_at window. search matches subject, ticket, and complainant name.
_LIST_FILTERS = (
	"status",
	"category",
	"location",
	"from_date",
	"to_date",
	"search",
)


def _upstream_error() -> dict:
	"""Log an upstream fault and return a stable envelope. The client never sees the cause."""
	frappe.log_error(title="Grievance Service request failed", message=frappe.get_traceback())
	frappe.response["http_status_code"] = 502
	return error_response(str(_UPSTREAM_MSG), "BAD_GATEWAY")


@grievance_route("/grievances", methods=("GET",), summary="List grievances scoped to caller")
@frappe.whitelist()
@handle_api_errors
def list_grievances():
	ctx = require_roles(*C.GRIEVANCE_ROLES)

	page = int(frappe.form_dict.get("page", 1) or 1)
	page_size = int(frappe.form_dict.get("page_size", 10) or 10)

	kwargs: dict = {"page": page, "page_size": page_size}
	for name in _LIST_FILTERS:
		value = frappe.form_dict.get(name)
		if value is None or value == "":
			continue
		kwargs[name] = value

	if ctx.has_any_role(C.ROLE_DA):
		da_ids = list(ctx.da_ids)
		if da_ids:
			kwargs["complainant_id"] = da_ids if len(da_ids) > 1 else da_ids[0]
	elif not ctx.unrestricted and not _constrain_area_scope(kwargs, ctx):
		return success_response(
			data=[],
			meta={"page": page, "page_size": page_size, "total": 0},
		)

	try:
		client = get_client()
		items, total = client.list_grievances(**kwargs)
		return success_response(
			data=[g.to_dict() for g in items],
			meta={"page": page, "page_size": page_size, "total": total},
		)
	except GrievanceRejected as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except GrievanceServiceUnavailable:
		frappe.response["http_status_code"] = 503
		return error_response(str(_UNAVAILABLE_MSG), "SERVICE_UNAVAILABLE")
	except GrievanceServiceError:
		return _upstream_error()


@grievance_route("/grievances", methods=("POST",), summary="Submit a new grievance")
@frappe.whitelist()
@handle_api_errors
def submit_grievance():
	ctx = require_roles(*C.GRIEVANCE_ROLES)

	body = frappe.request.get_json(silent=True) or {}
	subject = body.get("subject", "").strip()
	description = body.get("description", "").strip()
	category = body.get("category", "").strip()
	subcategory = body.get("subcategory", "").strip()
	raised_for = body.get("raised_for", "Self").strip()
	farmer_id = (body.get("farmer_id") or "").strip() or None
	client_op_id = (body.get("client_op_id") or "").strip() or None
	submitter_name = (body.get("submitter_name") or "").strip() or None
	contact_mobile = (body.get("contact_mobile") or "").strip() or None
	administrative_area = (body.get("administrative_area") or "").strip() or None
	administrative_unit = (body.get("administrative_unit") or "").strip() or None
	service_category = (body.get("service_category") or "").strip() or None
	grievance_type = (body.get("grievance_type") or "").strip() or None
	desired_outcome = (body.get("desired_outcome") or "").strip() or None
	submitter_type = (body.get("submitter_type") or "").strip() or "Individual Farmer"
	consent_given = body.get("consent_given", 1) not in (0, "0", False, "false")
	is_anonymous = body.get("is_anonymous") in (1, "1", True, "true")

	if not subject:
		frappe.throw(_("Subject is required."), frappe.ValidationError)

	if raised_for == "Farmer" and not farmer_id:
		frappe.throw(_("farmer_id is required when raised_for is Farmer."), frappe.ValidationError)

	complainant_id, complainant_name = _resolve_complainant_identity(ctx)

	submission = GrievanceSubmission(
		complainant_id=complainant_id,
		complainant_name=complainant_name,
		complainant_role=_resolve_complainant_role(ctx),
		region=sorted(ctx.region_scopes)[0] if ctx.region_scopes else None,
		woreda=sorted(ctx.woreda_scopes)[0] if ctx.woreda_scopes else None,
		category=category,
		subcategory=subcategory,
		subject=subject,
		description=description,
		raised_for=raised_for,
		farmer_id=farmer_id,
		attachments=body.get("attachments") or [],
		client_submission_uuid=client_op_id or str(uuid.uuid4()),
		submitter_type=submitter_type,
		submitter_name=submitter_name,
		contact_mobile=contact_mobile,
		administrative_area=administrative_area,
		administrative_unit=administrative_unit,
		service_category=service_category,
		grievance_type=grievance_type,
		desired_outcome=desired_outcome,
		is_anonymous=is_anonymous,
		consent_given=consent_given,
	)

	reference_id = client_op_id or f"{complainant_id}:{subject[:50]}"

	try:
		client = get_client()
		result = client.submit_grievance(submission)
		frappe.response["http_status_code"] = 201
		return success_response(
			data={
				"ticket_id": result.ticket_id,
				"ticket_number": result.ticket_number or result.ticket_id,
				"sync_state": "synced",
				"message": result.message,
			},
		)
	except GrievanceRejected as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except GrievanceServiceUnavailable:
		event_name = enqueue_event(
			event_type="Grievance Submit",
			payload=submission.to_dict(),
			reference_id=reference_id,
		)
		frappe.response["http_status_code"] = 201
		return success_response(
			data={
				"ticket_id": None,
				"sync_state": "pending",
				"queue_ref": event_name,
				"message": "Grievance queued for submission — the service is temporarily unavailable.",
			},
		)
	except GrievanceServiceError:
		return _upstream_error()


@grievance_route("/grievances/summary", methods=("GET",), summary="Grievance status summary")
@frappe.whitelist()
@handle_api_errors
def get_grievance_summary():
	"""KPI cards from the Grievance Service, passed through on success."""
	require_roles(*C.GRIEVANCE_ROLES)

	try:
		client = get_client()
		return success_response(data=client.get_summary())
	except GrievanceRejected as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except GrievanceServiceUnavailable:
		frappe.response["http_status_code"] = 503
		return error_response(str(_UNAVAILABLE_MSG), "SERVICE_UNAVAILABLE")
	except GrievanceServiceError:
		return _upstream_error()


@grievance_route("/grievances/<ticket_id>", methods=("GET",), summary="Get grievance detail")
@frappe.whitelist()
@handle_api_errors
def get_grievance(ticket_id: str):
	require_roles(*C.GRIEVANCE_ROLES)

	try:
		client = get_client()
		detail = client.get_grievance(ticket_id)
		return success_response(data=detail.to_dict())
	except GrievanceNotFound:
		frappe.throw(_("Grievance {0} not found.").format(ticket_id), frappe.DoesNotExistError)
	except GrievanceRejected as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except GrievanceServiceUnavailable:
		frappe.response["http_status_code"] = 503
		return error_response(str(_UNAVAILABLE_MSG), "SERVICE_UNAVAILABLE")
	except GrievanceServiceError:
		return _upstream_error()


@grievance_route("/grievances/<ticket_id>/timeline", methods=("GET",), summary="Get grievance timeline")
@frappe.whitelist()
@handle_api_errors
def get_timeline(ticket_id: str):
	require_roles(*C.GRIEVANCE_ROLES)

	try:
		client = get_client()
		entries = client.get_timeline(ticket_id)
		return success_response(data=entries)
	except GrievanceNotFound:
		frappe.throw(_("Grievance {0} not found.").format(ticket_id), frappe.DoesNotExistError)
	except GrievanceRejected as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except GrievanceServiceUnavailable:
		frappe.response["http_status_code"] = 503
		return error_response(str(_UNAVAILABLE_MSG), "SERVICE_UNAVAILABLE")
	except GrievanceServiceError:
		return _upstream_error()


@grievance_route(
	"/grievances/options", methods=("GET",), allow_guest=False, summary="Get grievance form options"
)
@frappe.whitelist()
@handle_api_errors
def get_options():
	require_roles(*C.GRIEVANCE_ROLES)

	try:
		client = get_client()
		options = client.get_options()
		return success_response(data=options)
	except GrievanceRejected as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except GrievanceServiceUnavailable:
		frappe.response["http_status_code"] = 503
		return error_response(str(_UNAVAILABLE_MSG), "SERVICE_UNAVAILABLE")
	except GrievanceServiceError:
		return _upstream_error()


@grievance_route("/grievances/sync/retry", methods=("POST",), summary="Retry failed integration events")
@frappe.whitelist()
@handle_api_errors
def retry_sync():
	require_roles(C.ROLE_ADMIN)

	body = frappe.request.get_json(silent=True) or {}
	event_name = body.get("event_name")

	retried = retry_failed(event_name)
	return success_response(
		data={"retried": retried, "count": len(retried)},
	)


def _split_values(value) -> list[str]:
	if value is None or value == "":
		return []
	if isinstance(value, (list, tuple)):
		return [str(item).strip() for item in value if str(item).strip()]
	return [part.strip() for part in str(value).split(",") if part.strip()]


def _intersect_scope(requested, allowed: frozenset[str]) -> list[str]:
	"""Values the caller asked for that their grant also covers.

	No request means the whole grant. A request outside the grant is an empty list.
	"""
	requested_values = _split_values(requested)
	if not requested_values:
		return sorted(allowed)
	return [value for value in requested_values if value in allowed]


def _constrain_area_scope(kwargs: dict, ctx) -> bool:
	"""Narrow region, zone, and woreda to the caller's grant.

	Returns False when a requested area is entirely outside that grant.
	"""
	if ctx.woreda_scopes:
		allowed = _intersect_scope(kwargs.get("woreda"), ctx.woreda_scopes)
		if not allowed:
			return False
		kwargs["woreda"] = allowed if len(allowed) > 1 else allowed[0]
	elif ctx.region_scopes:
		allowed = _intersect_scope(kwargs.get("region"), ctx.region_scopes)
		if not allowed:
			return False
		kwargs["region"] = allowed if len(allowed) > 1 else allowed[0]

	if ctx.zone_scopes and kwargs.get("zone") is not None:
		allowed = _intersect_scope(kwargs.get("zone"), ctx.zone_scopes)
		if not allowed:
			return False
		kwargs["zone"] = allowed if len(allowed) > 1 else allowed[0]
	return True


def _resolve_complainant_identity(ctx) -> tuple[str, str]:
	"""Derive complainant identity from the RBAC context — never from the request body."""
	if ctx.has_any_role(C.ROLE_DA) and ctx.da_ids:
		da_id = sorted(ctx.da_ids)[0]
		full_name = frappe.get_value("User", ctx.user, "full_name") or ctx.user
		return da_id, full_name
	full_name = frappe.get_value("User", ctx.user, "full_name") or ctx.user
	return ctx.user, full_name


def _resolve_complainant_role(ctx) -> str:
	for role in (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN):
		if ctx.has_any_role(role):
			return role
	return "Unknown"
