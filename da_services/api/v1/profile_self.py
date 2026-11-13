"""v1 DA profile and profile change requests (FSD 3.7; docs/api-da-screens.md 2.18).

GET  /api/v1/da-services/profile/me
GET  /api/v1/da-services/profile/agents/<da_id>                 Supervisor / Executive / Administrator
POST /api/v1/da-services/profile/change-requests                {changes {field: value}, reason, client_op_id, da_id}
GET  /api/v1/da-services/profile/change-requests                ?status=a,b&woreda=a,b&section=&da_id=&mine=1&no_supervisor_found=1&limit=&start=
GET  /api/v1/da-services/profile/change-requests/<id>
POST /api/v1/da-services/profile/change-requests/<id>/cancel
POST /api/v1/da-services/profile/change-requests/<id>/decide    {decision: Approve | Reject | Request changes, note}
POST /api/v1/da-services/profile/change-requests/bulk-decide    {ids: [...], decision, note}  (at most 20)
POST /api/v1/da-services/profile/change-requests/<id>/sync-local Administrator (failed local update of a transfer)
POST /api/v1/da-services/profile/change-requests/<id>/retry     Administrator
POST /api/v1/da-services/profile/change-requests/<id>/close     Administrator {note}

A DA asks for a change to their own profile; a Supervisor may ask for one on a DA of
their Woreda (``da_id``). Every section can be changed: Contact, Qualifications,
Identity and Location (services/profile_fields.py). A change reaches the DA Registry only
after the Woreda Supervisor (level 1) and then an OAN Administrator (level 2) approve it,
and it is sent in the Administrator's request with the Administrator's token.

Every change request in an answer says what the caller may do with it (``can_decide``,
``allowed_decisions``, ``can_cancel``, ``can_retry``, ``can_close``, ``can_sync_local``).
Refusals a screen treats differently have their own ``code``: ``ALREADY_DECIDED`` (reload
the request), ``OPEN_REQUEST_EXISTS``, ``NOTHING_TO_CHANGE``, and ``VALIDATION_ERROR``
with ``details {field: message}`` when one field's value is not acceptable.
"""

from __future__ import annotations

import functools

import frappe
from oan_auth_service.api.utils import error_response, handle_api_errors, success_response

from da_services.api.router import route
from da_services.api.v1.common import request_fields, truthy
from da_services.services import constants as C
from da_services.services import profile as svc
from da_services.utils.permissions import require_roles


def coded_refusals(endpoint):
	"""Answer a refusal that has its own code with that code.

	The shared handler derives the code from the HTTP status, so every 400 would read
	VALIDATION_ERROR and a screen could not tell "somebody decided this before you" from
	"this value is not valid". Other errors pass through to the shared handler.
	"""

	@functools.wraps(endpoint)
	def wrapper(*args, **kwargs):
		try:
			return endpoint(*args, **kwargs)
		except frappe.ValidationError as exc:
			code = getattr(exc, "api_code", None)
			if not code:
				raise
			frappe.db.rollback()  # as the shared handler does: nothing of a refused request is kept
			frappe.clear_messages()
			frappe.response["http_status_code"] = 400
			field = getattr(exc, "field", None)
			return error_response(str(exc), code, details={field: str(exc)} if field else None)

	return wrapper


@route("/profile/me", methods=("GET",), summary="My profile and open change requests")
@frappe.whitelist()
@handle_api_errors
def my_profile():
	ctx = require_roles(C.ROLE_DA)
	return success_response(data=svc.my_profile(ctx))


@route("/profile/agents/<da_id>", methods=("GET",), summary="A DA's profile and open change requests")
@frappe.whitelist()
@handle_api_errors
def agent_profile(da_id: str):
	ctx = require_roles(*svc.AGENT_PROFILE_ROLES)
	return success_response(data=svc.agent_profile(da_id, ctx))


@route("/profile/change-requests", methods=("POST",), status=201, summary="Request a profile change")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def submit(**kwargs):
	ctx = require_roles(*svc.SUBMIT_ROLES)
	b = request_fields(kwargs, "changes", "reason", "client_op_id", "da_id", json_keys=("changes",))
	out = svc.submit_change_request(
		changes=b.get("changes") or {},
		reason=b.get("reason"),
		client_op_id=b.get("client_op_id"),
		da_id=b.get("da_id"),
		ctx=ctx,
	)
	message = (
		"Change request sent to an OAN Administrator"
		if out["initiated_by_role"] == C.ROLE_SUPERVISOR
		else "Change request sent to your Woreda supervisor"
	)
	return success_response(data=out, message=message)


@route("/profile/change-requests", methods=("GET",), summary="Profile change requests in my scope")
@frappe.whitelist()
@handle_api_errors
def list_requests(**kwargs):
	ctx = require_roles(*svc.READ_ROLES)
	q = request_fields(
		kwargs, "status", "da_id", "woreda", "section", "mine", "no_supervisor_found", "limit", "start"
	)
	limit = int(q.get("limit") or 50)
	start = int(q.get("start") or 0)
	rows, total = svc.list_change_requests(
		ctx,
		status=q.get("status"),
		da_id=q.get("da_id"),
		woreda=q.get("woreda"),
		section=q.get("section"),
		mine=truthy(q.get("mine")),
		no_supervisor_found=truthy(q.get("no_supervisor_found")),
		limit=limit,
		start=start,
	)
	return success_response(data=rows, meta={"total": total, "limit": limit, "start": start})


@route("/profile/change-requests/<request_id>", methods=("GET",), summary="One profile change request")
@frappe.whitelist()
@handle_api_errors
def get_request(request_id: str):
	ctx = require_roles(*svc.READ_ROLES)
	return success_response(data=svc.get_change_request(request_id, ctx))


@route("/profile/change-requests/<request_id>/cancel", methods=("POST",), summary="Cancel a waiting request")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def cancel(request_id: str):
	ctx = require_roles(*svc.SUBMIT_ROLES)
	return success_response(
		data=svc.cancel_change_request(request_id, ctx), message="Change request cancelled"
	)


@route(
	"/profile/change-requests/<request_id>/decide",
	methods=("POST",),
	summary="Level 1 (Woreda Supervisor) or level 2 (OAN Administrator) decision",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def decide(request_id: str, **kwargs):
	ctx = require_roles(*svc.DECIDE_ROLES)
	b = request_fields(kwargs, "decision", "note")
	out = svc.decide(request_id, decision=(b.get("decision") or "").strip(), note=b.get("note"), ctx=ctx)
	return success_response(data=out, message=svc.decision_message(out))


@route(
	"/profile/change-requests/bulk-decide",
	methods=("POST",),
	summary="The same decision on several change requests (Approvals screen)",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def bulk_decide(**kwargs):
	ctx = require_roles(*svc.DECIDE_ROLES)
	b = request_fields(kwargs, "ids", "decision", "note", json_keys=("ids",))
	out = svc.decide_many(
		b.get("ids") or [], decision=(b.get("decision") or "").strip(), note=b.get("note"), ctx=ctx
	)
	return success_response(data=out, message=f"{out['decided']} decided, {out['failed']} not decided")


@route(
	"/profile/change-requests/<request_id>/retry",
	methods=("POST",),
	summary="Administrator: send a request the DA Registry did not apply again",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def retry(request_id: str):
	ctx = require_roles(C.ROLE_ADMIN)
	out = svc.retry_registry_push(request_id, ctx)
	return success_response(data=out, message=svc.decision_message(out))


@route(
	"/profile/change-requests/<request_id>/sync-local",
	methods=("POST",),
	summary="Administrator: repeat the DA Services update after an applied location change",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def sync_local(request_id: str):
	ctx = require_roles(C.ROLE_ADMIN)
	out = svc.sync_local_after_transfer(request_id, ctx)
	done = out.get("local_sync_state") == "Done"
	return success_response(
		data=out, message="DA Services scope updated" if done else "The local update failed again"
	)


@route(
	"/profile/change-requests/<request_id>/close",
	methods=("POST",),
	summary="Administrator: close a request the DA Registry did not apply",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def close(request_id: str, **kwargs):
	ctx = require_roles(C.ROLE_ADMIN)
	b = request_fields(kwargs, "note")
	out = svc.close_change_request(request_id, note=b.get("note"), ctx=ctx)
	return success_response(data=out, message=svc.decision_message(out))
