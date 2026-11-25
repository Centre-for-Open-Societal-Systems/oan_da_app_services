"""Central dashboard chart feeders (Confluence Dashboard APIs contract).

``GET /api/v1/da-services/charts/visits`` returns a raw JSON array of aggregate
rows — not ``success_response`` / ``{status,data}``. Auth is Keycloak
client-credentials + ``charts:read`` (see ``charts_auth``), not portal roles.
"""

from __future__ import annotations

import frappe

from da_services.api.router import route
from da_services.services import charts_auth, charts_visits


def _query(*names: str) -> dict:
	form = frappe.local.form_dict or {}
	return {n: form.get(n) for n in names}


@route(
	"/charts/visits",
	methods=("GET",),
	allow_guest=True,
	summary="Visit KPI aggregate for central dashboard (raw JSON array)",
)
@frappe.whitelist(allow_guest=True)
def visits_chart(**_kwargs):
	"""Feeder for BFF chart id ``visits``.

	Query: region, zone, woreda, kebele, from, to, da_id, visit_type, status, groupBy.
	Empty / all geo = no filter. Default date range = current calendar month.
	Does not use ``handle_api_errors`` so the body stays a bare array.
	"""
	charts_auth.require_charts_read()
	q = _query(
		"region",
		"zone",
		"woreda",
		"kebele",
		"from",
		"to",
		"da_id",
		"visit_type",
		"status",
		"groupBy",
	)
	return charts_visits.aggregate_visits(
		region=q["region"],
		zone=q["zone"],
		woreda=q["woreda"],
		kebele=q["kebele"],
		from_date=q["from"],
		to_date=q["to"],
		da_id=q["da_id"],
		visit_type=q["visit_type"],
		status=q["status"],
		group_by=q["groupBy"],
	)
