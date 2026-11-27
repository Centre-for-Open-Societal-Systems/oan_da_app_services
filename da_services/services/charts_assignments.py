"""Kebele assignment aggregate feeder for the central dashboard (chart id ``assignments``).

Same contract as the visits feeder: query params scope the read, empty / ``all`` geo
filters mean no filter, and the caller is the dashboard service account, so rows are
read with ``get_all`` and not through portal User Permissions. Counts only; no names,
positions or assignment ids.

Source is ``DA Kebele Assignment``, the decision record DA Services keeps for every
assignment and reassignment. A replaced row is kept as Superseded, so the history is
complete for any past month.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import frappe
from frappe.utils import getdate

from da_services.services.charts_visits import _default_month_bounds, _filter_value
from da_services.utils.geofence import OUT_OF_BOUNDS

ASSIGNMENT = "DA Kebele Assignment"
SUPERSEDED = "Superseded"

GROUP_BY = frozenset({"month", "woreda"})

KPI_KEYS = (
	"assignments",
	"reassignments",
	"out_of_bounds",
	"superseded",
)


def _zero_kpi() -> dict[str, int]:
	return {k: 0 for k in KPI_KEYS}


def _resolve_range(from_date: Any, to_date: Any) -> tuple[date, date]:
	"""Inclusive calendar-day bounds on ``effective_date``. Default = current month."""
	if from_date is None and to_date is None:
		return _default_month_bounds()
	start = getdate(from_date) if from_date else getdate(to_date)
	end = getdate(to_date) if to_date else getdate(from_date)
	return (start, end) if start <= end else (end, start)


def _build_filters(
	*,
	region: Any = None,
	zone: Any = None,
	woreda: Any = None,
	kebele: Any = None,
	from_date: Any = None,
	to_date: Any = None,
	da_id: Any = None,
) -> dict[str, Any]:
	filters: dict[str, Any] = {
		"effective_date": ("between", list(_resolve_range(from_date, to_date))),
	}
	scope = {"region": region, "zone": zone, "woreda": woreda, "kebele": kebele, "da_id": da_id}
	for field, raw in scope.items():
		value = _filter_value(raw)
		if value:
			filters[field] = value
	return filters


def _bucket_row(row: dict[str, Any], out: dict[str, Any]) -> None:
	out["assignments"] += 1
	# A decision that replaced an earlier one of the same DA is a reassignment.
	if row.get("supersedes"):
		out["reassignments"] += 1
	if row.get("geofence_result") == OUT_OF_BOUNDS:
		out["out_of_bounds"] += 1
	if row.get("state") == SUPERSEDED:
		out["superseded"] += 1


def _group_key(row: dict[str, Any], group_by: str) -> str:
	if group_by == "month":
		d = getdate(row["effective_date"])
		return f"{d.year:04d}-{d.month:02d}"
	return str(row.get(group_by) or "").strip()


def aggregate_assignments(
	*,
	region: Any = None,
	zone: Any = None,
	woreda: Any = None,
	kebele: Any = None,
	from_date: Any = None,
	to_date: Any = None,
	da_id: Any = None,
	group_by: Any = None,
) -> list[dict[str, Any]]:
	"""Return KPI rows for the assignments chart feeder.

	Ungrouped + no matches → one zeroed KPI object (stable BFF tiles).
	Grouped + no matches → ``[]``.
	"""
	group = _filter_value(group_by)
	if group not in GROUP_BY:
		# Unknown groupBy is ignored like any other unknown param → ungrouped.
		group = None

	rows = frappe.get_all(
		ASSIGNMENT,
		filters=_build_filters(
			region=region,
			zone=zone,
			woreda=woreda,
			kebele=kebele,
			from_date=from_date,
			to_date=to_date,
			da_id=da_id,
		),
		fields=["effective_date", "woreda", "state", "geofence_result", "supersedes"],
		limit_page_length=0,
		ignore_permissions=True,
	)

	if not group:
		kpi = _zero_kpi()
		for row in rows:
			_bucket_row(row, kpi)
		return [kpi]

	buckets: dict[str, dict[str, Any]] = {}
	for row in rows:
		key = _group_key(row, group)
		if not key:
			continue
		_bucket_row(row, buckets.setdefault(key, _zero_kpi()))
	return [{**buckets[key], group: key} for key in sorted(buckets)]
