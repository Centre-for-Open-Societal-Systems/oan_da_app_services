"""v1 agent assignments (docs/api-da-screens.md 1.1, 2.2; DA-37, DA-38).

GET  /api/v1/da-services/assignments?woreda=&status=&assignment_status=&q=&limit=&start=
GET  /api/v1/da-services/assignments/coverage?woreda=
POST /api/v1/da-services/assignments  {da_id, kebele, effective_date, reason, mode, da_location {lat, lng}, client_op_id}

A Supervisor lists and assigns within their Woreda; an Executive reads their Region.
"""

from __future__ import annotations

import frappe
from frappe import _
from oan_auth_service.api.utils import error_response, handle_api_errors, success_response

from da_services.api.router import route
from da_services.api.v1.common import request_fields
from da_services.integrations.da_registry.client import DARegistryContractPending, DARegistryUnavailable
from da_services.services import assignments as svc
from da_services.services import constants as C
from da_services.utils.permissions import require_permission

_REGISTRY_DOWN = (DARegistryUnavailable, DARegistryContractPending)


def _registry_unavailable(exc: Exception):
	"""503, not 400/500: the caller did nothing wrong and may retry."""
	frappe.response["http_status_code"] = 503
	return error_response(
		_("The DA Registry is unavailable ({0}). Try again shortly.").format(exc.__class__.__name__),
		"SERVICE_UNAVAILABLE",
	)


@route("/assignments", methods=("GET",), summary="Agent assignment list for a Woreda")
@frappe.whitelist()
@handle_api_errors
def list_assignments(**kwargs):
	require_permission("DA Kebele Assignment", "read")
	q = request_fields(kwargs, "woreda", "status", "assignment_status", "q", "limit", "start")
	limit = int(q.get("limit") or 50)
	start = int(q.get("start") or 0)
	try:
		rows, total, summary = svc.list_assignments(
			woreda=q.get("woreda"),
			status=q.get("status"),
			assignment_status=q.get("assignment_status"),
			q=q.get("q"),
			limit=limit,
			start=start,
		)
	except _REGISTRY_DOWN as exc:
		return _registry_unavailable(exc)
	return success_response(
		data=rows, meta={"total": total, "limit": limit, "start": start, "summary": summary}
	)


@route("/assignments/coverage", methods=("GET",), summary="Kebele coverage for a Woreda")
@frappe.whitelist()
@handle_api_errors
def coverage(**kwargs):
	require_permission("DA Kebele Assignment", "read")
	q = request_fields(kwargs, "woreda")
	try:
		return success_response(data=svc.coverage(woreda=q.get("woreda")))
	except _REGISTRY_DOWN as exc:
		return _registry_unavailable(exc)


@route("/assignments", methods=("POST",), status=201, summary="Assign or reassign a DA to a Kebele")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def assign(**kwargs):
	require_permission("DA Kebele Assignment", "create")
	b = request_fields(
		kwargs,
		"da_id",
		"kebele",
		"effective_date",
		"reason",
		"mode",
		"da_location",
		"client_op_id",
		json_keys=("da_location",),
	)
	try:
		out = svc.assign(
			da_id=(b.get("da_id") or "").strip(),
			kebele=(b.get("kebele") or "").strip().upper(),
			effective_date=b.get("effective_date"),
			reason=b.get("reason"),
			mode=b.get("mode") or "Automatic",
			da_location=b.get("da_location") or None,
			client_op_id=b.get("client_op_id") or None,
		)
	except _REGISTRY_DOWN as exc:  # the DA lookup needs the registry; the push itself never raises
		return _registry_unavailable(exc)
	msg = "Assignment flagged (out-of-bounds)" if out["state"] == "Flagged" else "Assignment effective"
	return success_response(data=out, message=msg)
