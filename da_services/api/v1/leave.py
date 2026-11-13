"""v1: DA leave requests (lifecycle slice 1 — "Supervisor can manage DA").

POST /api/v1/da-services/leave-requests                 DA asks for leave
GET  /api/v1/da-services/leave-requests                 DA: own · Supervisor: Woreda · Executive: Region
GET  /api/v1/da-services/leave-requests/<id>
POST /api/v1/da-services/leave-requests/<id>/cancel     DA withdraws (Pending / Approved-not-started)
POST /api/v1/da-services/leave-requests/<id>/decide     Supervisor: Approve | Reject

Thin by design: authorise, call the service, wrap in the envelope. Scope, date
rules and the DA Registry push live in services/leave.py.
"""

from __future__ import annotations

import frappe
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.api.v1.common import request_fields
from da_services.services import constants as C
from da_services.services import leave as svc
from da_services.utils.permissions import require_roles


@route("/leave-requests", methods=("POST",), status=201, summary="DA: request leave")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def submit(**kwargs):
	"""Body: {"leave_type": "Annual|Sick|Maternity|Study|Other", "from_date", "to_date",
	"reason", "client_op_id"?}. Goes to the DA's Woreda Supervisor."""
	ctx = require_roles(C.ROLE_DA)
	b = request_fields(kwargs, "leave_type", "from_date", "to_date", "reason", "client_op_id")
	out = svc.submit_leave_request(
		leave_type=b.get("leave_type"),
		from_date=b.get("from_date"),
		to_date=b.get("to_date"),
		reason=b.get("reason"),
		client_op_id=b.get("client_op_id"),
		ctx=ctx,
	)
	return success_response(data=out, message="Leave request sent to your Woreda supervisor")


@route("/leave-requests", methods=("GET",), summary="List leave requests in scope")
@frappe.whitelist()
@handle_api_errors
def list_requests(**kwargs):
	ctx = require_roles(*svc.READ_ROLES)
	q = request_fields(kwargs, "status", "da_id", "limit", "start")
	limit = int(q.get("limit") or 50)
	start = int(q.get("start") or 0)
	rows, total = svc.list_leave_requests(
		ctx, status=q.get("status"), da_id=q.get("da_id"), limit=limit, start=start
	)
	return success_response(data=rows, meta={"total": total, "limit": limit, "start": start})


@route("/leave-requests/<request_id>", methods=("GET",), summary="One leave request")
@frappe.whitelist()
@handle_api_errors
def get_request(request_id: str):
	ctx = require_roles(*svc.READ_ROLES)
	return success_response(data=svc.get_leave_request(request_id, ctx))


@route("/leave-requests/<request_id>/cancel", methods=("POST",), summary="DA: cancel own leave request")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def cancel(request_id: str):
	ctx = require_roles(C.ROLE_DA)
	return success_response(data=svc.cancel_leave_request(request_id, ctx), message="Leave request cancelled")


@route(
	"/leave-requests/<request_id>/decide",
	methods=("POST",),
	summary="Supervisor: approve or reject a leave request",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def decide(request_id: str, **kwargs):
	"""Body: {"decision": "Approve" | "Reject", "note"?}. Reject needs a note."""
	ctx = require_roles(*svc.DECIDE_ROLES)
	b = request_fields(kwargs, "decision", "note")
	out = svc.decide(request_id, decision=(b.get("decision") or "").strip(), note=b.get("note"), ctx=ctx)
	return success_response(data=out, message=f"Leave request {out['status'].lower()}")
