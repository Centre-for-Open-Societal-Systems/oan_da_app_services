"""DA Task use-cases behind the dashboard and the task APIs (data-model 5.5).

Who may do what (FSD Appendix D, enforced here, not in the UI):

- Development Agent: create and complete tasks for their own DA-ID only.
- Supervisor: create and complete tasks for DAs in their Woreda.
- Executive: read only (lists come through permission_query_conditions).
- OAN Administrator / System Manager: unrestricted.
- Communications Officer: nothing.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate, now_datetime

from da_services.services import constants as C
from da_services.services.da_lookup import get_da_reference
from da_services.utils.permissions import ScopeContext, require_roles

TASK_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_ADMIN)
READ_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)

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


def _resolve_da_for_write(da_id: str | None, ctx: ScopeContext) -> str:
	"""The DA-ID a caller may create / change tasks for.

	A DA always acts on their own DA-ID (a different one in the body is ignored). An
	officer must name the DA, and the DA's registry posting must be inside their scope;
	get_da_reference applies that rule and audits a refusal.
	"""
	if ctx.has_any_role(C.ROLE_DA) and not ctx.unrestricted:
		own = sorted(ctx.da_ids)
		if not own:
			frappe.throw(_("Your account is not bound to a DA-ID."), frappe.PermissionError)
		return own[0]
	if not da_id:
		frappe.throw(_("da_id is required."), frappe.ValidationError)
	get_da_reference(da_id, ctx)  # raises PermissionError when out of scope
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
	ctx: ScopeContext | None = None,
) -> dict:
	ctx = ctx or require_roles(*TASK_ROLES)
	if not (title or "").strip():
		frappe.throw(_("title is required."), frappe.ValidationError)
	da_id = _resolve_da_for_write(da_id, ctx)

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


def _load_in_scope(name: str, ctx: ScopeContext):
	doc = frappe.get_doc("DA Task", name)
	if ctx.unrestricted:
		return doc
	if ctx.has_any_role(C.ROLE_DA):
		if ctx.owns_da(doc.da_id):
			return doc
	elif ctx.has_any_role(C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE) and ctx.covers(
		doc.region, doc.woreda, doc.zone
	):
		return doc
	frappe.throw(_("Task {0} is outside your scope.").format(name), frappe.PermissionError)


def get_task(name: str, ctx: ScopeContext | None = None) -> dict:
	ctx = ctx or require_roles(*READ_ROLES)
	return serialize(_load_in_scope(name, ctx))


def complete_task(name: str, ctx: ScopeContext | None = None) -> dict:
	ctx = ctx or require_roles(*TASK_ROLES)
	doc = _load_in_scope(name, ctx)
	doc.flags.ignore_permissions = True
	doc.mark_done()
	return serialize(doc)


def cancel_task(name: str, reason: str | None = None, ctx: ScopeContext | None = None) -> dict:
	ctx = ctx or require_roles(C.ROLE_SUPERVISOR, C.ROLE_ADMIN)
	doc = _load_in_scope(name, ctx)
	doc.flags.ignore_permissions = True
	doc.cancel_task(reason)
	return serialize(doc)


def list_tasks(
	ctx: ScopeContext | None = None,
	*,
	status: str | None = None,
	due: str | None = None,
	da_id: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int]:
	"""Tasks the caller may see. Scope comes from permission_query_conditions via frappe.get_list.

	`due`: "today" (due today), "overdue" (open and past due), or an ISO date.
	"""
	ctx = ctx or require_roles(*READ_ROLES)
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
	# Scoped total: frappe.get_list applies permission_query_conditions; frappe.db.count would not.
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
