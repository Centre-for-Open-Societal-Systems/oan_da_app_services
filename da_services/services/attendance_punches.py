"""DA Attendance Punch: one row per punch-in or punch-out.

GET today returns next_action for the FE button. POST stores the event only.
GET list returns one attendance row per punched_date for a required ``user``.
Role DocPerm + Link/reqd meta own access and field rules. No punch-sequence enforcement on create.
"""

from __future__ import annotations

from collections import OrderedDict

import frappe
from frappe import _
from frappe.utils import get_time, getdate

DOCTYPE = "DA Attendance Punch"
LIST_FIELDS = [
	"name",
	"user",
	"da_id",
	"action",
	"punched_date",
	"punched_time",
	"kebele",
]


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
	punched_time = None
	if row.get("punched_time") not in (None, ""):
		punched_time = get_time(row.punched_time).strftime("%H:%M:%S")
	return {
		"next_action": "Punch Out" if row.get("action") == "Punch In" else "Punch In",
		"id": row.get("name"),
		"punched_date": str(getdate(row.punched_date)) if row.get("punched_date") else None,
		"punched_time": punched_time,
	}


def list_attendance(
	*,
	user: str,
	from_date=None,
	to_date=None,
	da_id: str | None = None,
	region: str | None = None,
	zone: str | None = None,
	woreda: str | None = None,
	kebele: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int]:
	"""Daily attendance for one user, scoped by Role DocPerm + User Permissions.

	``user`` is required from the FE. Date range uses ``from`` + ``to`` only
	(defaults to site today when both omitted). Other query args only narrow get_list.
	"""
	user = user.strip()
	if not user:
		frappe.throw(_("user is required."), frappe.ValidationError)

	filters: dict = {"user": user}
	if from_date and to_date:
		filters["punched_date"] = ("between", [getdate(from_date), getdate(to_date)])
	elif from_date or to_date:
		frappe.throw(_("from and to are both required for a date range."), frappe.ValidationError)
	else:
		filters["punched_date"] = getdate()

	for field, value in (
		("da_id", da_id),
		("region", region),
		("zone", zone),
		("woreda", woreda),
		("kebele", kebele),
	):
		value = (value or "").strip()
		if value:
			filters[field] = value

	limit = max(1, min(int(limit), 500))
	start = max(0, int(start))
	punches = frappe.get_list(
		DOCTYPE,
		filters=filters,
		fields=LIST_FIELDS,
		order_by="punched_date asc, punched_time asc, creation asc",
		limit_page_length=0,
	)

	# Asc order → first Punch In / last Punch Out per day.
	by_day: OrderedDict[str, dict] = OrderedDict()
	for punch in punches:
		day = punch.get("punched_date")
		if not day:
			continue
		day = str(getdate(day))
		row = by_day.get(day)
		if row is None:
			row = {
				"user": punch.get("user"),
				"da_id": punch.get("da_id"),
				"kebele": punch.get("kebele") or None,
				"punched_date": day,
				"check_in": None,
				"check_out": None,
			}
			by_day[day] = row
		else:
			row["da_id"] = row["da_id"] or punch.get("da_id")
			row["kebele"] = row["kebele"] or punch.get("kebele") or None

		if punch.get("action") == "Punch In" and row["check_in"] is None:
			row["check_in"] = punch.get("punched_time")
			row["kebele"] = punch.get("kebele") or row["kebele"]
		elif punch.get("action") == "Punch Out":
			row["check_out"] = punch.get("punched_time")

	user_name = frappe.db.get_value("User", user, "full_name")

	rows = []
	for r in by_day.values():
		check_in = get_time(r["check_in"]) if r["check_in"] not in (None, "") else None
		check_out = get_time(r["check_out"]) if r["check_out"] not in (None, "") else None
		hours = None
		if check_in and check_out:
			secs = (
				check_out.hour * 3600
				+ check_out.minute * 60
				+ check_out.second
				- check_in.hour * 3600
				- check_in.minute * 60
				- check_in.second
			)
			if secs >= 0:
				hours = f"{secs // 3600}h {(secs % 3600) // 60}m"
		rows.append(
			{
				"user": r["user"],
				"user_name": user_name,
				"da_id": r["da_id"],
				"kebele": r["kebele"],
				"punched_date": r["punched_date"],
				"status": "Checked In" if check_in else None,
				"check_in": check_in.strftime("%H:%M:%S") if check_in else None,
				"check_out": check_out.strftime("%H:%M:%S") if check_out else None,
				"hours_logged": hours,
			}
		)

	page = rows[start : start + limit]
	return page, len(page)


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
