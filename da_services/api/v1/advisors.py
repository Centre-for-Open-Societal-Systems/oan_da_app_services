"""v1 advisors: the MoA subject-matter expert directory DAs reference (Add Advisor form).

POST /api/v1/da-services/advisors
GET  /api/v1/da-services/advisors?q=&expertise=&region=&woreda=&status=&limit=50&start=0
GET  /api/v1/da-services/advisors/<advisor_id>

Thin by design: authorise, call the service, wrap in the envelope.
"""

from __future__ import annotations

import frappe
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.api.v1.common import request_fields
from da_services.services import advisors as svc
from da_services.utils.permissions import require_roles


@route("/advisors", methods=("POST",), status=201, summary="Admin: add an advisor to the directory")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def create_advisor(**kwargs):
	"""Body: name, expertise, unit, region, woreda, phone, email, status?, availability?."""
	require_roles(*svc.WRITE_ROLES)
	b = request_fields(
		kwargs, "name", "expertise", "unit", "region", "woreda", "phone", "email", "status", "availability"
	)
	return success_response(data=svc.create_advisor(**b), message="Advisor added")


@route("/advisors", methods=("GET",), summary="Advisor directory")
@frappe.whitelist()
@handle_api_errors
def list_advisors(**kwargs):
	"""A Development Agent gets the advisors of their own Woreda(s); other roles get the whole directory."""
	ctx = require_roles(*svc.READ_ROLES)
	q = request_fields(kwargs, "q", "expertise", "region", "woreda", "status", "limit", "start")
	limit = svc.int_param("limit", q.get("limit"), 50)
	start = svc.int_param("start", q.get("start"), 0)
	rows, total = svc.list_advisors(
		ctx,
		q=q.get("q"),
		expertise=q.get("expertise"),
		region=q.get("region"),
		woreda=q.get("woreda"),
		status=q.get("status"),
		limit=limit,
		start=start,
	)
	return success_response(data=rows, meta={"total": total, "limit": limit, "start": start})


@route("/advisors/<advisor_id>", methods=("GET",), summary="One advisor")
@frappe.whitelist()
@handle_api_errors
def get_advisor(advisor_id: str):
	ctx = require_roles(*svc.READ_ROLES)
	return success_response(data=svc.get_advisor(advisor_id, ctx))
