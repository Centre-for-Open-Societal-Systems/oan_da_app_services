"""v1: the signed-in DA's team (My Teams screen).

GET /api/v1/da-services/my-team

Thin by design: authorise, call the service, wrap in the envelope. Who is on the team
lives in services/my_team.py.
"""

from __future__ import annotations

import frappe
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.services import constants as C
from da_services.services import my_team as svc
from da_services.utils.permissions import require_roles


@route("/my-team", methods=("GET",), summary="The signed-in DA's Kebele team")
@frappe.whitelist()
@handle_api_errors
def get_my_team():
	"""The caller's own DA record, then the other DAs posted in the same Kebele.

	Read from the DA Registry. Separated DAs are left out; a DA with no Kebele on
	record gets the DAs of the Woreda. Each row carries ``da_id``, ``full_name``,
	``specialisation``, ``education_tier``, ``phone``, ``kebele``, ``kebele_name``,
	``woreda``, ``woreda_name``, ``active_status`` and ``joined_at``.
	"""
	ctx = require_roles(C.ROLE_DA)
	rows = svc.list_my_team(ctx)
	return success_response(data=rows, meta={"total": len(rows)})
