"""v1 DA dashboard (docs/api-da-screens.md 2.1, DA-198 / DA-373).

GET /api/v1/da-services/dashboard/summary?from=&to=
  [&da_id=][&woreda=&kebele=&status=&agents=&q=&limit=&start=]
GET /api/v1/da-services/dashboard/today?date=YYYY-MM-DD[&da_id=]

Role comes from the access token (``ScopeContext``), not from the client:

- Development Agent → agent tiles (visits / tasks / sync); ``da_id`` ignored.
- Supervisor / Admin with no ``da_id`` → woreda programme health + kebele filters.
- Officer with ``da_id`` → that DA's agent tiles (must be in scope).
"""

from __future__ import annotations

import frappe
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.services import dashboard as svc
from da_services.utils.permissions import require_roles


def _query(*names: str) -> dict:
	# `from` is a Python keyword, so query parameters are read from the request, not kwargs.
	form = frappe.local.form_dict or {}
	return {n: form.get(n) for n in names}


@route(
	"/dashboard/summary",
	methods=("GET",),
	summary="Dashboard summary (agent tiles or Supervisor woreda health by role)",
)
@frappe.whitelist()
@handle_api_errors
def summary(**_kwargs):
	ctx = require_roles(*svc.READ_ROLES)
	q = _query("from", "to", "da_id", "woreda", "kebele", "status", "agents", "q", "limit", "start")
	return success_response(
		data=svc.summary(
			ctx,
			from_date=q["from"],
			to_date=q["to"],
			da_id=q["da_id"],
			woreda=q["woreda"],
			kebele=q["kebele"],
			status=q["status"],
			agents=q["agents"],
			q=q["q"],
			limit=q["limit"],
			start=q["start"],
		)
	)


@route("/dashboard/today", methods=("GET",), summary="Today's Plan: visits and tasks for one day")
@frappe.whitelist()
@handle_api_errors
def today(**_kwargs):
	ctx = require_roles(*svc.READ_ROLES)
	q = _query("date", "da_id")
	return success_response(data=svc.today(ctx, date=q["date"], da_id=q["da_id"]))
