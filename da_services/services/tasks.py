"""DA Task use-cases behind the dashboard and the task APIs (data-model 5.5).

Who may do what (FSD Appendix D, enforced here, not in the UI):

- Development Agent: create and complete tasks for their own DA-ID only.
- Supervisor: create and complete tasks for DAs in their Woreda.
- Executive: read only.

Which rows a list returns: Role permissions, then the user's User Permissions on
DA Region / Zone / Woreda (the `region` / `zone` / `woreda` Link fields), applied by
frappe.get_list.
- Communications Officer: nothing.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate, now_datetime

from da_services.services import constants as C
from da_services.services.da_lookup import get_da_reference
from da_services.utils.permissions import require_permission

FIELDS = [
	"name",
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
	"woreda",
	"client_op_id",
	"modified",
]


def serialize(doc) -> dict:
	out = {f: doc.get(f) for f in FIELDS}
	for f in ("due_at", "completed_at", "modified"):
		if out.get(f) is not None and hasattr(out[f], "isoformat"):
			out[f] = out[f].isoformat()
	return out


def _resolve_da_for_write(da_id: str | None) -> str:
	"""The DA-ID a caller may create / change tasks for.

	The DA's registry posting must be inside the caller's scope; get_da_reference
	applies that rule and audits a refusal.
	"""
	if not da_id:
		frappe.throw(_("da_id is required."), frappe.ValidationError)
	get_da_reference(da_id)  # raises PermissionError when out of scope
	return da_id


def create_task(
	*,
	title: str,
	da_id: str | None = None,
	task_type: str = "Custom",
	description: str | None = None,
	due_at=None,
	priority: str = "Normal",
	farmer_id: str | None = None,
	visit: str | None = None,
	generated_by_rule: str | None = None,
	client_op_id: str | None = None,
) -> dict:
	require_permission("DA Task", "create")
	if not (title or "").strip():
		frappe.throw(_("title is required."), frappe.ValidationError)
	da_id = _resolve_da_for_write(da_id)

	if client_op_id:
		existing = frappe.db.get_value("DA Task", {"client_op_id": client_op_id}, "name")
		if existing:
			# Device replay: same operation, same answer, no second row.
			return serialize(frappe.get_doc("DA Task", existing))

	doc = frappe.get_doc(
		{
			"doctype": "DA Task",
			"da_id": da_id,
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
		}
	)
	doc.insert(ignore_permissions=True)  # scope was checked above; DocPerms stay for Desk
	return serialize(doc)


def _load_in_scope(name: str, ptype: str = "read"):
	"""The task, when the caller's role and User Permissions allow `ptype` on it."""
	doc = frappe.get_doc("DA Task", name)
	require_permission("DA Task", ptype, doc=doc, user=frappe.session.user)
	return doc


def get_task(name: str) -> dict:
	require_permission("DA Task", "read")
	return serialize(_load_in_scope(name))


def complete_task(name: str) -> dict:
	require_permission("DA Task", "write")
	doc = _load_in_scope(name, "write")
	doc.flags.ignore_permissions = True
	doc.mark_done()
	return serialize(doc)


def cancel_task(name: str, reason: str | None = None) -> dict:
	# No permission check: any signed-in user may cancel a task.
	doc = frappe.get_doc("DA Task", name)
	doc.flags.ignore_permissions = True
	doc.cancel_task(reason)
	return serialize(doc)


def list_tasks(
	*,
	status: str | None = None,
	due: str | None = None,
	da_id: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int]:
	"""Tasks the caller may see. Scope comes from the caller's User Permissions via frappe.get_list.

	`due`: "today" (due today), "overdue" (open and past due), or an ISO date.
	"""
	require_permission("DA Task", "read")
	filters: dict = {}
	if status:
		filters["status"] = status
	if da_id:
		filters["da_id"] = da_id
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
	# Scoped total: frappe.get_list applies User Permissions; frappe.db.count would not.
	total = len(frappe.get_list("DA Task", filters=filters, pluck="name", limit_page_length=0))
	return [serialize(frappe._dict(r)) for r in rows], total


def dashboard_counts(da_id: str, from_date=None, to_date=None) -> dict:
	"""The Tasks tile: open / done in the range, urgent open, due today (data-model 5.5)."""
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
