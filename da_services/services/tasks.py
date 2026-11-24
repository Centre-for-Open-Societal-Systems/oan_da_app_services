"""DA Task use-cases behind the dashboard, surveys, and visit follow-ups (data-model 5.5).

Visibility and create/write access are Frappe DocType Role permissions + User
Permissions only (no custom Task permission hooks). These services apply request
filters and product rules only — there is no dedicated Task HTTP API in this change.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate, now_datetime

FIELDS = [
	"name",
	"user",
	"da_id",
	"task_type",
	"title",
	"description",
	"farmer_id",
	"visit",
	"due_at",
	"priority",
	"status",
	"completed_at",
	"generated_by_rule",
	"region",
	"zone",
	"woreda",
	"kebele",
	"client_op_id",
	"modified",
]


def serialize(doc) -> dict:
	out = {f: doc.get(f) for f in FIELDS}
	for f in ("due_at", "completed_at", "modified"):
		if out.get(f) is not None and hasattr(out[f], "isoformat"):
			out[f] = out[f].isoformat()
	return out


def create_task(
	*,
	title: str,
	user: str | None = None,
	da_id: str | None = None,
	task_type: str = "Custom",
	description: str | None = None,
	due_at=None,
	priority: str = "Normal",
	farmer_id: str | None = None,
	visit: str | None = None,
	generated_by_rule: str | None = None,
	client_op_id: str | None = None,
	region: str | None = None,
	zone: str | None = None,
	woreda: str | None = None,
	kebele: str | None = None,
) -> dict:
	"""Create one task. ``user`` is required from the FE.

	No role / scope / permission helpers here — Frappe DocType Role permissions + User Permissions own access.
	``da_id`` is optional (DocType ``fetch_from: user.da_registry_id`` when present). Body ``da_id`` is
	legacy/envelope only. Optional geo stamps come from the caller / masters / tests.
	"""
	if not (title or "").strip():
		frappe.throw(_("title is required."), frappe.ValidationError)
	user = (user or "").strip()
	if not user:
		frappe.throw(_("user is required."), frappe.ValidationError)
	# ``da_id`` kept for API/envelope compatibility; optional on the row.
	# Do not require / stamp from User.da_registry_id — DocType fetch_from fills when set.
	# registry_da = frappe.db.get_value("User", user, "da_registry_id")
	# if not registry_da:
	# 	frappe.throw(_("user has no DA Registry ID."), frappe.ValidationError)

	if client_op_id:
		existing = frappe.db.get_value("DA Task", {"client_op_id": client_op_id}, "name")
		if existing:
			# Device replay: same operation, same answer, no second row.
			return serialize(frappe.get_doc("DA Task", existing))

	doc = frappe.get_doc(
		{
			"doctype": "DA Task",
			"user": user,
			# Optional: DocType fetch_from fills when User.da_registry_id is set.
			# "da_id": registry_da,
			"task_type": task_type or "Custom",
			"title": title.strip(),
			"description": description,
			"due_at": get_datetime(due_at) if due_at else None,
			"priority": priority or "Normal",
			"status": "Open",
			"farmer_id": farmer_id,
			"visit": visit,
			"generated_by_rule": generated_by_rule,
			"client_op_id": client_op_id,
			"region": region,
			"zone": zone,
			"woreda": woreda,
			"kebele": kebele,
		}
	)
	doc.insert()
	return serialize(doc)


def get_task(name: str) -> dict:
	return serialize(frappe.get_doc("DA Task", name))


def complete_task(name: str) -> dict:
	doc = frappe.get_doc("DA Task", name)
	doc.mark_done()
	return serialize(doc)


def cancel_task(name: str, reason: str | None = None) -> dict:
	doc = frappe.get_doc("DA Task", name)
	doc.cancel_task(reason)
	return serialize(doc)


def list_tasks(
	*,
	status: str | None = None,
	due: str | None = None,
	da_id: str | None = None,
	region: str | None = None,
	zone: str | None = None,
	woreda: str | None = None,
	kebele: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int]:
	"""Tasks visible via Role DocPerm + User Permissions. Optional query args narrow that set.

	`due`: "today" (due today), "overdue" (open and past due), or an ISO date.
	"""
	filters: dict = {}
	if status:
		filters["status"] = status
	if da_id:
		filters["da_id"] = da_id
	if region:
		filters["region"] = region
	if zone:
		filters["zone"] = zone
	if woreda:
		filters["woreda"] = woreda
	if kebele:
		filters["kebele"] = kebele
	if due == "today":
		d = getdate()
		filters["due_at"] = ("between", [f"{d} 00:00:00", f"{d} 23:59:59"])
	elif due == "overdue":
		filters["due_at"] = ("<", now_datetime())
		filters.setdefault("status", "Open")
	elif due:
		d = getdate(due)
		filters["due_at"] = ("between", [f"{d} 00:00:00", f"{d} 23:59:59"])

	limit = max(1, min(int(limit), 500))
	start = max(0, int(start))
	rows = frappe.get_list(
		"DA Task",
		filters=filters,
		fields=FIELDS,
		order_by="due_at asc, modified desc",
		limit_page_length=limit,
		limit_start=start,
	)
	return rows, len(rows)


def dashboard_counts(da_id: str, from_date=None, to_date=None) -> dict:
	"""Tasks tile counts for ``GET /dashboard/summary`` (data-model 5.5)."""
	base = {"da_id": da_id}
	rng = {}
	if from_date and to_date:
		rng = {"due_at": ("between", [f"{getdate(from_date)} 00:00:00", f"{getdate(to_date)} 23:59:59"])}
	today = getdate()
	return {
		"open": frappe.db.count("DA Task", {**base, "status": "Open"}),
		"done": frappe.db.count("DA Task", {**base, "status": "Done", **rng}),
		"urgent": frappe.db.count("DA Task", {**base, "status": "Open", "priority": "Urgent"}),
		"due_today": frappe.db.count(
			"DA Task",
			{**base, "status": "Open", "due_at": ("between", [f"{today} 00:00:00", f"{today} 23:59:59"])},
		),
	}
