"""DA dashboard read models (docs/api-da-screens.md 2.1, DA-198 / DA-373).

Two DA-agent calls feed the Development Agent home:

- ``summary(from, to)``: the three tiles. Farm visits and Tasks come from our DA Visit and
  DA Task rows; Sync status is what the server knows (queued integration events, visit
  attachments the device still owes or that clashed). The device merges its own queue count.
- ``today(date)``: Today's Plan, visits and tasks for one day merged into one list.

``summary`` is role-aware (DA-373):

- Development Agent (or officer with ``da_id``) → agent tiles (visits / tasks / sync).
- Supervisor / Admin with no ``da_id`` → woreda KPI tiles and kebele breakdown for the
  caller's assigned Woreda only. Filters (kebele / status / agents / q) apply to the
  table; stats stay full-woreda.

Scope: a DA sees their own DA-ID only; an officer naming ``da_id`` must be in scope
(get_da_reference). Supervisor woreda view never returns another Woreda's kebeles or totals.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime, getdate, now_datetime

from da_services.da_services.doctype.da_visit.da_visit import COMPLETE, PLANNED_STATES
from da_services.integrations.grievance.client import (
	GrievanceServiceError,
	GrievanceServiceUnavailable,
)
from da_services.integrations.grievance.client import (
	get_client as get_grievance_client,
)
from da_services.services import assignments, da_service, geo, tasks, visits
from da_services.services import constants as C
from da_services.services.da_lookup import get_da_reference
from da_services.utils.permissions import ScopeContext, require_roles

READ_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)
WOREDA_HEALTH_ROLES = (C.ROLE_SUPERVISOR, C.ROLE_ADMIN)
DEFAULT_RANGE_DAYS = 30
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 500

STATUS_ON_TRACK = "On track"
STATUS_WATCH = "Watch"
STATUS_AT_RISK = "At risk"
KEBELE_STATUSES = frozenset({STATUS_ON_TRACK, STATUS_WATCH, STATUS_AT_RISK})

# Grievance statuses that still need action (terminal Resolved / Closed / Rejected excluded).
# Includes mock "Open" plus live summary-card open statuses.
OPEN_GRIEVANCE_STATUSES = frozenset({"Open", "Submitted", "Reopened", "In Progress", "More Info Needed"})


def resolve_da(da_id: str | None, ctx: ScopeContext) -> str:
	"""The DA-ID the dashboard is about. A DA is always shown their own."""
	if ctx.has_any_role(C.ROLE_DA) and not ctx.unrestricted:
		own = sorted(ctx.da_ids)
		if not own:
			frappe.throw(_("Your account is not bound to a DA-ID."), frappe.PermissionError)
		return own[0]
	if not da_id:
		frappe.throw(_("da_id is required."), frappe.ValidationError)
	get_da_reference(da_id, ctx)  # PermissionError when outside the officer's scope
	return da_id


def sync_status(da_id: str, user: str) -> dict:
	"""Server-side half of the Sync tile.

	- server_pending / failed: integration events this user queued (grievance writes
	  waiting for the Grievance Service) still open or dead.
	- conflicts: visit attachments of this DA the server could not reconcile.
	- last_received_at: the last time the server accepted anything from this DA.
	"""
	pending = frappe.db.count(
		"DA Integration Event", {"owner": user, "status": ("in", ["Pending", "Processing"])}
	)
	failed = frappe.db.count("DA Integration Event", {"owner": user, "status": "Failed"})
	visit_names = frappe.get_all("DA Visit", filters={"da_id": da_id}, pluck="name")
	conflicts = 0
	if visit_names:
		conflicts = frappe.db.count(
			"DA Visit Attachment",
			{"parent": ("in", visit_names), "sync_state": ("in", ["Conflict", "Dead letter"])},
		)
	stamps = []
	for dt in ("DA Visit", "DA Task"):
		latest = frappe.get_all(
			dt, filters={"da_id": da_id}, pluck="modified", order_by="modified desc", limit_page_length=1
		)
		if latest:
			stamps.append(get_datetime(latest[0]))
	return {
		"server_pending": pending,
		"failed": failed,
		"conflicts": conflicts,
		"last_received_at": max(stamps).isoformat() if stamps else None,
	}


def _agent_summary(ctx: ScopeContext, *, from_date=None, to_date=None, da_id: str | None = None) -> dict:
	"""Development Agent (or officer with ``da_id``) home tiles: visits / tasks / sync."""
	da_id = resolve_da(da_id, ctx)
	from_d, to_d = _parse_range(from_date, to_date)
	return {
		"view": "agent",
		"da_id": da_id,
		"range": {"from": str(from_d), "to": str(to_d)},
		"visits": visits.dashboard_counts(da_id, from_d, to_d),
		"tasks": tasks.dashboard_counts(da_id, from_d, to_d),
		"sync": sync_status(da_id, ctx.user),
	}


def _wants_woreda_summary(ctx: ScopeContext, da_id: str | None) -> bool:
	"""Supervisor / Admin programme-health view when no ``da_id`` is named."""
	if da_id:
		return False
	if ctx.unrestricted:
		return True
	if ctx.has_any_role(C.ROLE_SUPERVISOR):
		return True
	return False


def summary(
	ctx: ScopeContext | None = None,
	*,
	from_date=None,
	to_date=None,
	da_id: str | None = None,
	woreda: str | None = None,
	kebele: str | None = None,
	status: str | None = None,
	agents: str | None = None,
	q: str | None = None,
	limit=None,
	start=None,
) -> dict:
	"""Role-aware dashboard summary (token scope + query params).

	- Development Agent → agent tiles (own DA-ID).
	- Supervisor / Admin with no ``da_id`` → woreda programme health + kebele filters.
	- Officer with ``da_id`` → that DA's agent tiles (scope-checked).
	- Executive without ``da_id`` → still requires ``da_id`` (no region roll-up here yet).
	"""
	ctx = ctx or require_roles(*READ_ROLES)
	if _wants_woreda_summary(ctx, da_id):
		out = woreda_health(
			ctx,
			from_date=from_date,
			to_date=to_date,
			woreda=woreda,
			kebele=kebele,
			status=status,
			agents=agents,
			q=q,
			limit=limit,
			start=start,
		)
		out["view"] = "woreda"
		return out
	return _agent_summary(ctx, from_date=from_date, to_date=to_date, da_id=da_id)


def _task_items(da_id: str, date) -> list[dict]:
	d = getdate(date)
	rows = frappe.get_all(
		"DA Task",
		filters={
			"da_id": da_id,
			"due_at": ("between", [f"{d} 00:00:00", f"{d} 23:59:59"]),
			"status": ("in", ["Open", "Done"]),
		},
		fields=["name", "title", "task_type", "due_at", "status", "priority", "farmer_id"],
		order_by="due_at asc",
	)
	items = []
	for r in rows:
		if r.status == "Done":
			status = "Done"
		elif d < getdate():
			status = "Overdue"
		else:
			status = "Due today" if d == getdate() else "Upcoming"
		items.append(
			{
				"kind": "task",
				"id": r.name,
				"time": get_datetime(r.due_at).strftime("%H:%M") if r.due_at else None,
				"title": r.title,
				"subtitle": r.task_type + (f" · {r.farmer_id}" if r.farmer_id else ""),
				"status": status,
				"priority": r.priority,
				"href": f"/tasks/{r.name}",
			}
		)
	return items


def today(ctx: ScopeContext | None = None, *, date=None, da_id: str | None = None) -> dict:
	ctx = ctx or require_roles(*READ_ROLES)
	da_id = resolve_da(da_id, ctx)
	d = getdate(date) if date else getdate()
	items = visits.today_items(da_id, d) + _task_items(da_id, d)
	# Timed entries first in clock order, undated tasks at the end.
	items.sort(key=lambda i: (i["time"] is None, i["time"] or "", i["kind"]))
	return {"da_id": da_id, "date": str(d), "items": items}


# -- Supervisor Programme Health (DA-373) -------------------------------------------


def status_for_visit_pct(visit_pct: int) -> str:
	"""FE ``statusFor``: >=80 On track, 60-79 Watch, <60 At risk."""
	if visit_pct >= 80:
		return STATUS_ON_TRACK
	if visit_pct >= 60:
		return STATUS_WATCH
	return STATUS_AT_RISK


def resolve_supervisor_woreda(ctx: ScopeContext, woreda: str | None = None) -> str:
	"""Assigned Woreda code for the Supervisor Programme Health screen.

	Single grant → that Woreda. Multiple grants / Admin → ``woreda`` query required and
	must fall inside the caller's scope (``assignments._resolve_officer_scope``).
	"""
	code, _region = assignments._resolve_officer_scope(ctx, woreda)
	if not code:
		frappe.throw(_("woreda is required."), frappe.ValidationError)
	return code


def _area_name(code: str) -> str:
	# Woreda health tiles pass a Woreda code; fall back to the code if unsynced.
	return geo.display_name("DA Woreda", code) or code


def _parse_range(from_date, to_date) -> tuple:
	to_d = getdate(to_date) if to_date else getdate()
	from_d = getdate(from_date) if from_date else add_days(to_d, -(DEFAULT_RANGE_DAYS - 1))
	if from_d > to_d:
		frappe.throw(_("from must not be after to."), frappe.ValidationError)
	return from_d, to_d


def _parse_page(limit, start) -> tuple[int, int]:
	try:
		limit_i = cint(limit if limit is not None else DEFAULT_PAGE_LIMIT)
		start_i = cint(start if start is not None else 0)
	except TypeError, ValueError:
		frappe.throw(_("limit and start must be integers."), frappe.ValidationError)
	if limit_i < 1:
		limit_i = DEFAULT_PAGE_LIMIT
	limit_i = min(limit_i, MAX_PAGE_LIMIT)
	if start_i < 0:
		start_i = 0
	return limit_i, start_i


def _validate_kebele_in_woreda(woreda: str, kebele: str, allowed: set[str]) -> None:
	if kebele not in allowed:
		frappe.throw(
			_("Kebele {0} is not in Woreda {1}.").format(kebele, woreda),
			frappe.ValidationError,
		)


def _farmer_counts_by_kebele(woreda: str, kebele_codes: list[str]) -> dict[str, int]:
	"""Registered farmers per Kebele from the DA Farmer Reference projection."""
	counts = {code: 0 for code in kebele_codes}
	if not kebele_codes:
		return counts
	# Aggregate in Python: Frappe disallows SQL function strings in ``fields``.
	for kebele in frappe.get_all(
		"DA Farmer Reference",
		filters={"woreda": woreda, "kebele": ("in", kebele_codes)},
		pluck="kebele",
		limit_page_length=0,
	):
		if kebele in counts:
			counts[kebele] += 1
	return counts


def _farmer_total(woreda: str) -> int:
	return frappe.db.count("DA Farmer Reference", {"woreda": woreda})


def _agent_counts(ctx: ScopeContext, woreda: str, kebele_codes: list[str]) -> tuple[int, dict[str, int]]:
	"""Active agents in the Woreda and per-Kebele agent counts (any active_status except Separated)."""
	agents, _total = da_service.list_agents(ctx, woreda=woreda, limit=MAX_PAGE_LIMIT, start=0)
	by_kebele = {code: 0 for code in kebele_codes}
	active = 0
	for agent in agents:
		status = agent.get("active_status")
		kebele = agent.get("kebele")
		if status == C.ACTIVE_STATUS_ACTIVE:
			active += 1
		if status == C.ACTIVE_STATUS_SEPARATED:
			continue
		if kebele in by_kebele:
			by_kebele[kebele] += 1
	return active, by_kebele


def _visit_metrics_by_kebele(
	kebele_codes: list[str], from_d, to_d
) -> tuple[dict[str, dict[str, int]], dict[str, str | None]]:
	"""Per-kebele planned/completed in range, plus latest visit ``modified`` (any time)."""
	metrics = {code: {"planned": 0, "completed": 0} for code in kebele_codes}
	last_sync: dict[str, str | None] = {code: None for code in kebele_codes}
	if not kebele_codes:
		return metrics, last_sync

	rng = ("between", [f"{from_d} 00:00:00", f"{to_d} 23:59:59"])
	in_range = frappe.get_all(
		"DA Visit",
		filters={
			"kebele": ("in", kebele_codes),
			"scheduled_at": rng,
			"physical_status": ("in", list(PLANNED_STATES)),
		},
		fields=["kebele", "physical_status"],
		limit_page_length=0,
	)
	for row in in_range:
		bucket = metrics.get(row.kebele)
		if not bucket:
			continue
		bucket["planned"] += 1
		if row.physical_status == COMPLETE:
			bucket["completed"] += 1

	# Aggregate in Python: Frappe disallows SQL function strings in ``fields``.
	latest_rows = frappe.get_all(
		"DA Visit",
		filters={"kebele": ("in", kebele_codes)},
		fields=["kebele", "modified"],
		order_by="modified desc",
		limit_page_length=0,
	)
	for row in latest_rows:
		code = row.kebele
		if code not in last_sync or last_sync[code] is not None:
			continue
		stamp = row.modified
		last_sync[code] = get_datetime(stamp).isoformat() if stamp else None
	return metrics, last_sync


def _open_grievance_count(woreda: str) -> int:
	"""Unresolved grievances for the Woreda; 0 when the grievance client is unavailable."""
	try:
		client = get_grievance_client()
		open_total = 0
		for status in OPEN_GRIEVANCE_STATUSES:
			_items, total = client.list_grievances(woreda=woreda, status=status, page=1, page_size=1)
			open_total += int(total or 0)
		return open_total
	except GrievanceServiceUnavailable, GrievanceServiceError:
		return 0


def _build_kebele_rows(
	kebeles: list[dict],
	*,
	farmer_by_kebele: dict[str, int],
	agents_by_kebele: dict[str, int],
	visit_metrics: dict[str, dict[str, int]],
	last_sync: dict[str, str | None],
) -> list[dict]:
	rows = []
	for area in kebeles:
		code = area["code"]
		planned = visit_metrics.get(code, {}).get("planned", 0)
		completed = visit_metrics.get(code, {}).get("completed", 0)
		visit_pct = round((completed / planned) * 100) if planned else 0
		rows.append(
			{
				"kebele": area.get("name_en") or code,
				"kebele_code": code,
				"farmers": farmer_by_kebele.get(code, 0),
				"agents": agents_by_kebele.get(code, 0),
				"visit_pct": visit_pct,
				"last_sync": last_sync.get(code),
				"status": status_for_visit_pct(visit_pct),
			}
		)
	return rows


def _apply_kebele_filters(
	rows: list[dict],
	*,
	kebele: str | None,
	status: str | None,
	agents: str | None,
	q: str | None,
) -> list[dict]:
	out = rows
	if kebele:
		out = [r for r in out if r["kebele_code"] == kebele]
	if status:
		if status not in KEBELE_STATUSES:
			frappe.throw(
				_("Invalid status: {0}. Valid values: {1}").format(
					status, ", ".join(sorted(KEBELE_STATUSES))
				),
				frappe.ValidationError,
			)
		out = [r for r in out if r["status"] == status]
	if agents is not None and agents != "":
		try:
			want = cint(agents)
		except TypeError, ValueError:
			frappe.throw(_("agents must be an integer agent count."), frappe.ValidationError)
		out = [r for r in out if r["agents"] == want]
	if q:
		needle = q.strip().lower()
		if needle:
			out = [
				r
				for r in out
				if needle in (r["kebele"] or "").lower()
				or needle in (r["kebele_code"] or "").lower()
				or needle in str(r["farmers"])
				or needle in str(r["agents"])
				or needle in str(r["visit_pct"])
				or needle in (r["status"] or "").lower()
				or needle in (r["last_sync"] or "").lower()
			]
	return out


def woreda_health(
	ctx: ScopeContext | None = None,
	*,
	from_date=None,
	to_date=None,
	woreda: str | None = None,
	kebele: str | None = None,
	status: str | None = None,
	agents: str | None = None,
	q: str | None = None,
	limit=None,
	start=None,
) -> dict:
	"""Supervisor Programme Health for one assigned Woreda (DA-373).

	Stats are full-woreda. Kebele / status / agents / q filters apply only to ``kebeles``.
	"""
	ctx = ctx or require_roles(*WOREDA_HEALTH_ROLES)
	if not ctx.unrestricted and not ctx.has_any_role(C.ROLE_SUPERVISOR):
		frappe.throw(_("Supervisor Programme Health is for Supervisors."), frappe.PermissionError)

	woreda_code = resolve_supervisor_woreda(ctx, woreda)
	from_d, to_d = _parse_range(from_date, to_date)
	page_limit, page_start = _parse_page(limit, start)

	kebeles = assignments.areas(level="Kebele", parent=woreda_code)
	kebele_codes = [k["code"] for k in kebeles]
	allowed = set(kebele_codes)
	if kebele:
		_validate_kebele_in_woreda(woreda_code, kebele, allowed)

	farmer_by_kebele = _farmer_counts_by_kebele(woreda_code, kebele_codes)
	active_agents, agents_by_kebele = _agent_counts(ctx, woreda_code, kebele_codes)
	visit_metrics, last_sync = _visit_metrics_by_kebele(kebele_codes, from_d, to_d)
	all_rows = _build_kebele_rows(
		kebeles,
		farmer_by_kebele=farmer_by_kebele,
		agents_by_kebele=agents_by_kebele,
		visit_metrics=visit_metrics,
		last_sync=last_sync,
	)

	data_quality = round(sum(r["visit_pct"] for r in all_rows) / len(all_rows)) if all_rows else 0
	stats = {
		"farmers": _farmer_total(woreda_code),
		"active_agents": active_agents,
		"data_quality": data_quality,
		"open_grievances": _open_grievance_count(woreda_code),
	}

	filtered = _apply_kebele_filters(all_rows, kebele=kebele, status=status, agents=agents, q=q)
	total = len(filtered)
	page_rows = filtered[page_start : page_start + page_limit]

	return {
		"woreda": {"code": woreda_code, "name": _area_name(woreda_code)},
		"updated_at": now_datetime().isoformat(),
		"range": {"from": str(from_d), "to": str(to_d)},
		"stats": stats,
		"kebeles": page_rows,
		"total": total,
		"page": {"limit": page_limit, "start": page_start},
	}
