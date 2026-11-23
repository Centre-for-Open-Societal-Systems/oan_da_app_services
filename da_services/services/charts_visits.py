"""Visit aggregate feeder for the central dashboard (chart id ``visits``).

Filter-only: query params scope the read. Empty / ``all`` geo filters mean no
filter. Uses ``get_all`` (service-account surface) — not portal User Permissions.
Aggregates only; no PII, GPS, or visit ids.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any

import frappe
from frappe.utils import getdate

from da_services.da_services.doctype.da_visit.da_visit import (
	CANCELLED,
	COMPLETE,
	IN_PROGRESS,
	MISSED,
	PLANNED_STATES,
	RESCHEDULED,
)

GROUP_BY = frozenset({"visit_type", "woreda", "da_id", "month"})

KPI_KEYS = (
	"planned",
	"completed",
	"missed",
	"rescheduled",
	"in_progress",
	"cancelled",
	"total",
	"farmers_reached",
)


def _zero_kpi() -> dict[str, int]:
	return {k: 0 for k in KPI_KEYS}


def _filter_value(raw: Any) -> str | None:
	"""Empty / all / missing → no filter; otherwise stripped string."""
	if raw is None:
		return None
	value = str(raw).strip()
	if not value or value.lower() == "all":
		return None
	return value


def _default_month_bounds(today: date | None = None) -> tuple[date, date]:
	d = today or date.today()
	last = monthrange(d.year, d.month)[1]
	return date(d.year, d.month, 1), date(d.year, d.month, last)


def _resolve_range(from_date: Any, to_date: Any) -> tuple[str, str]:
	"""Inclusive calendar-day bounds on ``scheduled_at``. Default = current month."""
	if from_date is None and to_date is None:
		start, end = _default_month_bounds()
	else:
		start = getdate(from_date) if from_date else getdate(to_date)
		end = getdate(to_date) if to_date else getdate(from_date)
		if start > end:
			start, end = end, start
	return f"{start} 00:00:00", f"{end} 23:59:59"


def _build_filters(
	*,
	region: Any = None,
	zone: Any = None,
	woreda: Any = None,
	kebele: Any = None,
	from_date: Any = None,
	to_date: Any = None,
	da_id: Any = None,
	visit_type: Any = None,
	status: Any = None,
) -> dict[str, Any]:
	filters: dict[str, Any] = {
		"scheduled_at": ("between", list(_resolve_range(from_date, to_date))),
	}
	geo = {"region": region, "zone": zone, "woreda": woreda, "kebele": kebele}
	for field, raw in geo.items():
		value = _filter_value(raw)
		if value:
			filters[field] = value
	for field, raw in (("da_id", da_id), ("visit_type", visit_type), ("physical_status", status)):
		value = _filter_value(raw)
		if value:
			filters[field] = value
	return filters


def _bucket_row(row: dict[str, Any], out: dict[str, Any]) -> None:
	status = row.get("physical_status")
	out["total"] += 1
	if status in PLANNED_STATES:
		out["planned"] += 1
	if status == COMPLETE:
		out["completed"] += 1
	elif status == MISSED:
		out["missed"] += 1
	elif status == RESCHEDULED:
		out["rescheduled"] += 1
	elif status == IN_PROGRESS:
		out["in_progress"] += 1
	elif status == CANCELLED:
		out["cancelled"] += 1
	farmer = (row.get("farmer_id") or "").strip()
	if farmer:
		farmers = out.setdefault("_farmers", set())
		farmers.add(farmer)


def _finalize(out: dict[str, Any]) -> dict[str, int]:
	farmers = out.pop("_farmers", set())
	kpi = {k: int(out.get(k, 0)) for k in KPI_KEYS if k != "farmers_reached"}
	kpi["farmers_reached"] = len(farmers)
	return kpi


def _group_key(row: dict[str, Any], group_by: str) -> str:
	if group_by == "month":
		sched = row.get("scheduled_at")
		if not sched:
			return ""
		d = getdate(sched)
		return f"{d.year:04d}-{d.month:02d}"
	return str(row.get(group_by) or "").strip()


def aggregate_visits(
	*,
	region: Any = None,
	zone: Any = None,
	woreda: Any = None,
	kebele: Any = None,
	from_date: Any = None,
	to_date: Any = None,
	da_id: Any = None,
	visit_type: Any = None,
	status: Any = None,
	group_by: Any = None,
) -> list[dict[str, Any]]:
	"""Return KPI rows for the visits chart feeder.

	Ungrouped + no matches → one zeroed KPI object (stable BFF tiles).
	Grouped + no matches → ``[]``.
	"""
	group = _filter_value(group_by)
	if group and group not in GROUP_BY:
		# Unknown groupBy: ignore (plan: ignore unknown params) → treat as ungrouped.
		group = None

	filters = _build_filters(
		region=region,
		zone=zone,
		woreda=woreda,
		kebele=kebele,
		from_date=from_date,
		to_date=to_date,
		da_id=da_id,
		visit_type=visit_type,
		status=status,
	)
	fields = ["physical_status", "farmer_id", "scheduled_at", "visit_type", "woreda", "da_id"]
	rows = frappe.get_all(
		"DA Visit",
		filters=filters,
		fields=fields,
		limit_page_length=0,
		ignore_permissions=True,
	)

	if not group:
		acc = _zero_kpi()
		acc["_farmers"] = set()
		for row in rows:
			_bucket_row(row, acc)
		return [_finalize(acc)]

	buckets: dict[str, dict[str, Any]] = {}
	for row in rows:
		key = _group_key(row, group)
		if not key:
			continue
		acc = buckets.get(key)
		if acc is None:
			acc = _zero_kpi()
			acc["_farmers"] = set()
			acc[group] = key
			buckets[key] = acc
		_bucket_row(row, acc)

	if not buckets:
		return []

	out: list[dict[str, Any]] = []
	for key in sorted(buckets):
		acc = buckets[key]
		group_value = acc.pop(group, key)
		kpi = _finalize(acc)
		kpi[group] = group_value
		out.append(kpi)
	return out
