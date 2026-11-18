"""DA Attendance Punch: one row per punch-in or punch-out.

FE owns button state from GET today-latest. Backend stores the event.
Role DocPerm + Link/reqd meta own access and field rules. No punch-sequence checks.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, get_datetime, getdate

DOCTYPE = "DA Attendance Punch"

FIELDS = [
	"name",
	"user",
	"da_id",
	"action",
	"punched_at",
	"region",
	"zone",
	"woreda",
	"kebele",
	"creation",
	"modified",
]


def serialize(row) -> dict:
	out = {f: row.get(f) for f in FIELDS}
	out["id"] = out.pop("name")
	for key in ("punched_at", "creation", "modified"):
		value = out.get(key)
		if value is not None and hasattr(value, "isoformat"):
			out[key] = value.isoformat()
	return out


def _optional_link(value: str | None) -> str | None:
	text = (value or "").strip()
	return text or None


def get_today_latest() -> dict | None:
	"""Latest punch for the session user on the site calendar day, or None."""
	day = getdate()
	rows = frappe.get_list(
		DOCTYPE,
		filters=[
			["user", "=", frappe.session.user],
			["punched_at", ">=", f"{day} 00:00:00"],
			["punched_at", "<", f"{add_days(day, 1)} 00:00:00"],
		],
		fields=FIELDS,
		order_by="punched_at desc",
		limit=1,
	)
	return serialize(rows[0]) if rows else None


def create_punch(
	*,
	action: str,
	punched_at,
	region: str | None = None,
	zone: str | None = None,
	woreda: str | None = None,
	kebele: str | None = None,
) -> dict:
	"""Insert one punch for the session user. FE supplies time and geo Links."""
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"user": frappe.session.user,
			"action": (action or "").strip() or None,
			"punched_at": get_datetime(punched_at) if punched_at not in (None, "") else None,
			"region": _optional_link(region),
			"zone": _optional_link(zone),
			"woreda": _optional_link(woreda),
			"kebele": _optional_link(kebele),
		}
	)
	doc.insert()
	return serialize(doc.as_dict())
