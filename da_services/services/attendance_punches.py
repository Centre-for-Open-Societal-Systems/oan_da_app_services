"""DA Attendance Punch: one row per punch-in or punch-out.

GET today returns next_action for the FE button. POST stores the event only.
Role DocPerm + Link/reqd meta own access and field rules. No punch-sequence enforcement on create.
"""

from __future__ import annotations

import frappe
from frappe.utils import format_timedelta, getdate

DOCTYPE = "DA Attendance Punch"


def get_today_latest() -> dict:
	"""Today's latest punch for the session user, plus next_action for the FE button."""
	rows = frappe.get_list(
		DOCTYPE,
		filters={"user": frappe.session.user, "punched_date": getdate()},
		fields=["name", "action", "punched_date", "punched_time"],
		order_by="punched_date desc, punched_time desc, creation desc",
		limit=1,
	)
	if not rows:
		return {
			"next_action": "Punch In",
			"id": None,
			"punched_date": None,
			"punched_time": None,
		}
	row = rows[0]
	punched_time = row.get("punched_time")
	if punched_time is not None and hasattr(punched_time, "total_seconds"):
		punched_time = format_timedelta(punched_time)
	elif punched_time not in (None, ""):
		punched_time = str(punched_time).split(".", 1)[0]
	else:
		punched_time = None
	return {
		"next_action": "Punch Out" if row.get("action") == "Punch In" else "Punch In",
		"id": row.get("name"),
		"punched_date": str(getdate(row.punched_date)) if row.get("punched_date") else None,
		"punched_time": punched_time or None,
	}


def create_punch(
	*,
	action: str,
	punched_date=None,
	punched_time=None,
	region: str | None = None,
	zone: str | None = None,
	woreda: str | None = None,
	kebele: str | None = None,
	lat=None,
	lng=None,
) -> None:
	"""Insert one punch for the session user. FE supplies date, time, optional geo and GPS."""
	payload = {
		"doctype": DOCTYPE,
		"user": frappe.session.user,
		"action": (action or "").strip() or None,
		"punched_date": getdate(punched_date) if punched_date not in (None, "") else None,
		"punched_time": punched_time if punched_time not in (None, "") else None,
		"region": (region or "").strip() or None,
		"zone": (zone or "").strip() or None,
		"woreda": (woreda or "").strip() or None,
		"kebele": (kebele or "").strip() or None,
	}
	# Only set GPS when FE sent coordinates (Float defaults to 0.0 otherwise).
	if lat not in (None, ""):
		payload["gps_lat"] = float(lat)
	if lng not in (None, ""):
		payload["gps_lng"] = float(lng)
	frappe.get_doc(payload).insert()
