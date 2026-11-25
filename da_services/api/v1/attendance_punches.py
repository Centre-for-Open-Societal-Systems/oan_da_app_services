"""v1: DA Attendance Punch (punch-in / punch-out).

GET  /api/v1/da-services/attendance-punches/today
GET  /api/v1/da-services/attendance-punches
POST /api/v1/da-services/attendance-punches

Thin: parse query/body, call service, wrap envelope. Access and field rules come from
DA Attendance Punch Role DocPerm + meta (``get_list`` / ``insert``).
No ``require_roles`` / ScopeContext.

Register POST before GET on the shared path so the router keeps both methods
(same order as visits / gatherings).
"""

from __future__ import annotations

import frappe
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.api.v1.common import request_fields
from da_services.services import attendance_punches as svc


@route("/attendance-punches/today", methods=("GET",), summary="Latest attendance punch for today")
@frappe.whitelist()
@handle_api_errors
def today():
	"""Return next_action plus today's latest punch id/date/time (nulls when none)."""
	return success_response(data=svc.get_today_latest())


@route("/attendance-punches", methods=("POST",), status=201, summary="Create attendance punch")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def create(**kwargs):
	"""Body: action, punched_date, punched_time; optional geo Links and lat/lng.

	user and da_id come from the session (``user`` + ``fetch_from`` da_id).
	Response is message-only (no punch payload).
	"""
	body = request_fields(
		kwargs,
		"action",
		"punched_date",
		"punched_time",
		"region",
		"zone",
		"woreda",
		"kebele",
		"lat",
		"lng",
	)
	svc.create_punch(
		action=body.get("action"),
		punched_date=body.get("punched_date"),
		punched_time=body.get("punched_time"),
		region=body.get("region"),
		zone=body.get("zone"),
		woreda=body.get("woreda"),
		kebele=body.get("kebele"),
		lat=body.get("lat"),
		lng=body.get("lng"),
	)
	return success_response(message="Attendance punch recorded")


@route("/attendance-punches", methods=("GET",), summary="Attendance the caller may see")
@frappe.whitelist()
@handle_api_errors
def list_attendance(**kwargs):
	"""Daily attendance for one user (required). Optional date/geo filters via get_list."""
	q = {
		n: (frappe.form_dict or {}).get(n, kwargs.get(n))
		for n in (
			"date",
			"from",
			"to",
			"da_id",
			"user",
			"region",
			"zone",
			"woreda",
			"kebele",
			"limit",
			"start",
		)
	}
	limit = int(q.get("limit") or 50)
	start = int(q.get("start") or 0)
	rows, total = svc.list_attendance(
		user=(q.get("user") or "").strip(),
		date=q.get("date"),
		from_date=q.get("from"),
		to_date=q.get("to"),
		da_id=q.get("da_id"),
		region=q.get("region"),
		zone=q.get("zone"),
		woreda=q.get("woreda"),
		kebele=q.get("kebele"),
		limit=limit,
		start=start,
	)
	return success_response(data=rows, meta={"total": total, "limit": limit, "start": start})
