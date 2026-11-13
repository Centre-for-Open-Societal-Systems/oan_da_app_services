"""v1 DA self-service profile (FSD 3.7; docs/api-da-screens.md 2.18).

GET  /api/v1/da-services/profile/me
POST /api/v1/da-services/profile/change-requests            {changes {field: value}, reason, client_op_id}
GET  /api/v1/da-services/profile/change-requests            ?status=&da_id=&limit=&start=
GET  /api/v1/da-services/profile/change-requests/<id>
POST /api/v1/da-services/profile/change-requests/<id>/cancel
POST /api/v1/da-services/profile/change-requests/<id>/decide {decision: Approve | Reject | Request changes, note}

Editable by the DA: mobile, email, language, photo (Contact); education_tier,
specialisation (Qualifications). Name, date of birth, gender and ID are Fayda-verified
and locked. Changes reach the DA Registry only after the Woreda Supervisor approves.
"""

from __future__ import annotations

import frappe
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.api.v1.common import request_fields
from da_services.services import constants as C
from da_services.services import profile as svc
from da_services.utils.permissions import require_roles


@route("/profile/me", methods=("GET",), summary="My profile and open change requests")
@frappe.whitelist()
@handle_api_errors
def my_profile():
	ctx = require_roles(C.ROLE_DA)
	return success_response(data=svc.my_profile(ctx))


@route("/profile/change-requests", methods=("POST",), status=201, summary="Request a change to my profile")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def submit(**kwargs):
	ctx = require_roles(C.ROLE_DA)
	b = request_fields(kwargs, "changes", "reason", "client_op_id", json_keys=("changes",))
	out = svc.submit_change_request(
		changes=b.get("changes") or {},
		reason=b.get("reason"),
		client_op_id=b.get("client_op_id"),
		ctx=ctx,
	)
	return success_response(data=out, message="Change request sent to your Woreda supervisor")


@route("/profile/change-requests", methods=("GET",), summary="Profile change requests in my scope")
@frappe.whitelist()
@handle_api_errors
def list_requests(**kwargs):
	ctx = require_roles(*svc.READ_ROLES)
	q = request_fields(kwargs, "status", "da_id", "limit", "start")
	limit = int(q.get("limit") or 50)
	start = int(q.get("start") or 0)
	rows, total = svc.list_change_requests(
		ctx, status=q.get("status"), da_id=q.get("da_id"), limit=limit, start=start
	)
	return success_response(data=rows, meta={"total": total, "limit": limit, "start": start})


@route("/profile/change-requests/<request_id>", methods=("GET",), summary="One profile change request")
@frappe.whitelist()
@handle_api_errors
def get_request(request_id: str):
	ctx = require_roles(*svc.READ_ROLES)
	return success_response(data=svc.get_change_request(request_id, ctx))


@route("/profile/change-requests/<request_id>/cancel", methods=("POST",), summary="Cancel my pending request")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def cancel(request_id: str):
	ctx = require_roles(C.ROLE_DA)
	return success_response(
		data=svc.cancel_change_request(request_id, ctx), message="Change request cancelled"
	)


@route(
	"/profile/change-requests/<request_id>/decide",
	methods=("POST",),
	summary="Supervisor: approve, reject or request changes",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def decide(request_id: str, **kwargs):
	ctx = require_roles(*svc.DECIDE_ROLES)
	b = request_fields(kwargs, "decision", "note")
	out = svc.decide(request_id, decision=(b.get("decision") or "").strip(), note=b.get("note"), ctx=ctx)
	return success_response(data=out, message=f"Change request {out['status'].lower()}")
