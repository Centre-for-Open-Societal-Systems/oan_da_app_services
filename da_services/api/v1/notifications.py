"""v1 notifications: the bell list for the signed-in user (data-model 5.10, DA-196).

GET  /api/v1/da-services/notifications?unread=1&limit=20&start=0
POST /api/v1/da-services/notifications/<name>/read
POST /api/v1/da-services/notifications/read-all
POST /api/v1/da-services/notifications/broadcast   (Communications Officer / Admin)

A user only ever sees and changes their own rows; the service enforces it.
"""

from __future__ import annotations

import frappe
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.services import constants as C
from da_services.services import notifications as svc
from da_services.utils.permissions import require_roles


@route("/notifications", methods=("GET",), summary="My notifications")
@frappe.whitelist()
@handle_api_errors
def list_notifications(unread=None, limit=20, start=0):
	ctx = require_roles(*C.DA_SERVICES_ROLES)
	want_unread = str(unread).lower() in ("1", "true", "yes") if unread is not None else False
	rows, meta = svc.list_notifications(ctx, unread=want_unread, limit=limit, start=start)
	return success_response(data=rows, meta=meta)


@route("/notifications/read-all", methods=("POST",), summary="Mark all my notifications read")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def read_all():
	ctx = require_roles(*C.DA_SERVICES_ROLES)
	return success_response(data=svc.mark_all_read(ctx), message="All notifications marked read")


@route("/notifications/<notification_id>/read", methods=("POST",), summary="Mark one notification read")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def read_one(notification_id: str):
	ctx = require_roles(*C.DA_SERVICES_ROLES)
	return success_response(data=svc.mark_read(notification_id, ctx), message="Notification marked read")


@route("/notifications/broadcast", methods=("POST",), summary="Broadcast a message to DAs")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def broadcast():
	"""Send a Broadcast notification to every DA in a scope (Communications Officer, Admin).

	Request body (JSON):
	    {
	        "title": "Fertiliser distribution starts Monday",   // required, <= 140 chars
	        "body": "Collect vouchers at the Woreda office.",   // optional
	        "href": "/notifications",                           // optional deep link
	        "scope": "all" | "region" | "zone" | "woreda",      // default "all"
	        "scope_value": "ET04-W01",                          // required unless scope is "all"
	        "client_op_id": "<uuid>"                            // optional idempotency key
	    }

	Scope rule: Admin may target anything; a Communications Officer with no
	Region/Zone/Woreda on their grant is the national desk and may target
	anything; one with a scoped grant may only target inside that scope.

	Returns:
	    {"success": true, "data": {"broadcast_id", "scope", "scope_value", "recipients", "duplicate"}}
	"""
	ctx = require_roles(C.ROLE_COMMS, C.ROLE_ADMIN)

	data = frappe.request.get_json(silent=True) if frappe.request else None
	if not data and frappe.form_dict:
		framework_keys = {"cmd", "_", "sid"}
		data = {k: v for k, v in frappe.form_dict.items() if k not in framework_keys}
	data = data or {}

	out = svc.broadcast(
		ctx,
		title=data.get("title"),
		body=data.get("body"),
		href=data.get("href"),
		scope=data.get("scope") or "all",
		scope_value=data.get("scope_value"),
		client_op_id=data.get("client_op_id"),
	)
	message = (
		"Broadcast already sent." if out["duplicate"] else f"Broadcast sent to {out['recipients']} DA(s)."
	)
	return success_response(data=out, message=message)
