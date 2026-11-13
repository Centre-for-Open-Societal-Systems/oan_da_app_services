"""v1: administer RBAC assignments (OAN Administrator only).

GET    /api/v1/da-services/rbac-assignments?user=&role=&region=&woreda=&active=&limit=&start=
POST   /api/v1/da-services/rbac-assignments
GET    /api/v1/da-services/rbac-assignments/{assignment_id}
PATCH  /api/v1/da-services/rbac-assignments/{assignment_id}
POST   /api/v1/da-services/rbac-assignments/{assignment_id}/revoke
"""

import frappe
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.services import rbac_service


@route("/rbac-assignments", methods=("GET",), summary="List RBAC assignments")
@frappe.whitelist()
@handle_api_errors
def list_assignments(user=None, role=None, region=None, woreda=None, active=None, limit=50, start=0):
	rows = rbac_service.list_assignments(user, role, region, woreda, active, limit, start)
	limit, start = rbac_service.clamp_page(limit, start)
	return success_response(data=rows, meta={"count": len(rows), "limit": limit, "start": start})


@route("/rbac-assignments", methods=("POST",), status=201, summary="Grant a role within a scope")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def create_assignment(
	user=None,
	role=None,
	effective_from=None,
	effective_to=None,
	region_scope=None,
	woreda_scope=None,
	da_id=None,
	notes=None,
	zone_scope=None,
):
	row = rbac_service.create_assignment(
		user=user,
		role=role,
		effective_from=effective_from,
		effective_to=effective_to,
		region_scope=region_scope,
		zone_scope=zone_scope,
		woreda_scope=woreda_scope,
		da_id=da_id,
		notes=notes,
	)
	return success_response(data=row, message="Assignment created")


@route("/rbac-assignments/<assignment_id>", methods=("GET",), summary="One RBAC assignment")
@frappe.whitelist()
@handle_api_errors
def get_assignment(assignment_id: str):
	return success_response(data=rbac_service.get_assignment(assignment_id))


@route("/rbac-assignments/<assignment_id>", methods=("PATCH",), summary="Change scope, window or active flag")
@frappe.whitelist(methods=["PATCH", "POST"])
@handle_api_errors
def update_assignment(assignment_id: str, **changes):
	return success_response(
		data=rbac_service.update_assignment(assignment_id, **changes), message="Assignment updated"
	)


@route(
	"/rbac-assignments/<assignment_id>/revoke", methods=("POST",), summary="Revoke a grant (kept for audit)"
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def revoke_assignment(assignment_id: str, reason=None):
	return success_response(
		data=rbac_service.revoke_assignment(assignment_id, reason), message="Assignment revoked"
	)
