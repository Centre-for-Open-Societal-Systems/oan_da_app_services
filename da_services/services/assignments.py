"""Agent ↔ Kebele assignment: list, coverage, assign / reassign (FSD FR-05; DA-37, DA-38).

What is ours and what is not:

- The DA's posting of record lives in the DA Registry. We read it (`list_das`,
  `get_da`) and, when a Supervisor assigns a Kebele here, push the decision back
  (`push_lifecycle_outcome`, transition Transfer). If the registry cannot take it the
  row stays `registry_sync_state = Failed` with the error; the Registry Sync screen
  (DA-36) retries.
- `DA Kebele Assignment` is the decision record: who, when, why, farmer-association
  mode, geofence result, and the chain of superseded rows. Only an Effective row is a
  posting in force; a Flagged row is a pending decision shown next to it.
- Farmer counts come from the registry projection (`farmer_count`) until the Farmer
  Registry link exists; households are unknown today and returned as null.
- Geofence: the DA position (sent by the client, or unknown) against the Kebele's
  boundary from DA Administrative Area. No boundary → "Not checked", recorded, not
  blocking.

Consistency: the decision (new row + supersessions) is written under a per-DA lock and
committed before the registry push and the notification, which are best-effort and
recorded on the row; a failure there never loses the decision.

Scope: Supervisor acts within their Woreda, Executive reads their Region, Admin all;
a DA reads their own row only.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import getdate

from da_services.da_services.doctype.da_administrative_area.da_administrative_area import ancestors_of
from da_services.da_services.doctype.da_kebele_assignment.da_kebele_assignment import LIVE_STATES
from da_services.integrations.da_registry.client import (
	DARegistryContractPending,
	DARegistryError,
	DARegistryUnavailable,
	get_client,
)
from da_services.integrations.da_registry.schemas import DAReference, LifecycleOutcome
from da_services.services import constants as C
from da_services.services.da_lookup import get_da_reference
from da_services.services.notifications import notify_da
from da_services.utils import geofence
from da_services.utils.permissions import ScopeContext, require_roles

READ_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)
WRITE_ROLES = (C.ROLE_SUPERVISOR, C.ROLE_ADMIN)
LOCK_TIMEOUT_SECONDS = 5

ASSIGNMENT_FIELDS = [
	"name",
	"da_id",
	"kebele",
	"state",
	"mode",
	"effective_date",
	"reason",
	"assigned_by",
	"region",
	"zone",
	"woreda",
	"geofence_result",
	"geofence_reason",
	"supersedes",
	"superseded_by",
	"registry_sync_state",
	"registry_reference",
	"registry_error",
	"client_op_id",
	"modified",
]


# -- master data ------------------------------------------------------------------


def areas(*, level: str | None = None, parent: str | None = None, q: str | None = None) -> list[dict]:
	filters: dict = {"active": 1}
	if level:
		filters["level"] = level
	if parent:
		filters["parent_area"] = parent
	if q:
		# Parameterised by Frappe; only the LIKE wildcards need escaping so "Bako_01"
		# matches literally.
		needle = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")[:80]
		filters["name_en"] = ("like", f"%{needle}%")
	return frappe.get_all(
		"DA Administrative Area",
		filters=filters,
		fields=["name as code", "level", "parent_area", "name_en", "name_am", "boundary_ref"],
		order_by="name_en asc",
		limit_page_length=0,
	)


def _area_names(codes: set[str]) -> dict[str, str]:
	codes = {c for c in codes if c}
	if not codes:
		return {}
	rows = frappe.get_all(
		"DA Administrative Area", filters={"name": ("in", list(codes))}, fields=["name", "name_en"]
	)
	return {r.name: r.name_en for r in rows}


# -- rows ---------------------------------------------------------------------------


def serialize(doc) -> dict:
	out = {f: doc.get(f) for f in ASSIGNMENT_FIELDS}
	for f in ("effective_date", "modified"):
		if out.get(f) is not None and hasattr(out[f], "isoformat"):
			out[f] = out[f].isoformat()
	return out


def _agent_status(ref: DAReference, kebele: str | None) -> str:
	if ref.active_status == C.ACTIVE_STATUS_SEPARATED:
		return "Separated"
	if not kebele:
		return "Unassigned"
	return "On-leave" if ref.active_status == C.ACTIVE_STATUS_ON_LEAVE else "Active"


def _live_assignments(da_ids: list[str]) -> dict[str, dict]:
	"""{da_id: {"current": Effective row or None, "flagged": pending Flagged row or None}}."""
	if not da_ids:
		return {}
	rows = frappe.get_all(
		"DA Kebele Assignment",
		filters={"da_id": ("in", da_ids), "state": ("in", list(LIVE_STATES))},
		fields=ASSIGNMENT_FIELDS,
		order_by="effective_date desc, creation desc",
	)
	out: dict[str, dict] = {}
	for r in rows:
		slot = out.setdefault(r.da_id, {"current": None, "flagged": None})
		key = "current" if r.state == "Effective" else "flagged"
		if slot[key] is None:
			slot[key] = serialize(r)
	return out


def _row(ref: DAReference, live: dict | None, names: dict[str, str]) -> dict:
	"""One list row: the posting in force (ours, else the registry's) plus any pending flag."""
	current = (live or {}).get("current")
	flagged = (live or {}).get("flagged")
	kebele = current["kebele"] if current else ref.kebele
	if current:
		assignment_status = "Flagged" if flagged else current["state"]
	else:
		assignment_status = "Flagged" if flagged else ("Effective" if ref.kebele else "Proposed")
	decision = flagged or current  # the latest decision carries the geofence result shown
	return {
		"da_id": ref.da_id,
		"agent": ref.full_name,
		"region": ref.region,
		"woreda": ref.woreda,
		"kebele": kebele,
		"kebele_name": names.get(kebele) if kebele else None,
		"farmers": ref.farmer_count,
		"status": _agent_status(ref, kebele),
		"assignment_status": assignment_status,
		"assignment_source": "da_services" if current else "registry",
		"assignment_id": current["name"] if current else None,
		"effective_date": current["effective_date"] if current else None,
		"geofence": decision["geofence_result"] if decision else "Not checked",
		"geofence_reason": decision["geofence_reason"] if decision else None,
		"mode": current["mode"] if current else None,
		"assigned_by": (decision or {}).get("assigned_by"),
		"registry_sync_state": current["registry_sync_state"] if current else None,
		"flagged": (
			{
				"assignment_id": flagged["name"],
				"kebele": flagged["kebele"],
				"kebele_name": names.get(flagged["kebele"]),
				"effective_date": flagged["effective_date"],
				"reason": flagged["geofence_reason"],
			}
			if flagged
			else None
		),
	}


def _resolve_officer_scope(ctx: ScopeContext, woreda: str | None) -> tuple[str | None, str | None]:
	"""(woreda, region) an officer may list. A Supervisor defaults to their only Woreda."""
	if ctx.unrestricted:
		return woreda, None
	if woreda:
		tree = ancestors_of(woreda)
		if tree["woreda"] != woreda:
			frappe.throw(_("{0} is not a Woreda in the administrative area master.").format(woreda))
		if not ctx.covers(tree["region"], woreda, tree["zone"]):
			frappe.throw(_("Woreda {0} is outside your scope.").format(woreda), frappe.PermissionError)
		return woreda, None
	if len(ctx.woreda_scopes) == 1:
		return next(iter(ctx.woreda_scopes)), None
	if ctx.woreda_scopes:
		frappe.throw(_("woreda is required (you hold more than one)."), frappe.ValidationError)
	if len(ctx.region_scopes) == 1:
		return None, next(iter(ctx.region_scopes))
	frappe.throw(_("woreda is required."), frappe.ValidationError)


def list_assignments(
	ctx: ScopeContext | None = None,
	*,
	woreda: str | None = None,
	status: str | None = None,
	assignment_status: str | None = None,
	q: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int, dict]:
	"""Agent assignment list for a Woreda (DA-37). Returns (rows, total, summary).

	Raises DARegistryUnavailable / DARegistryContractPending when the registry cannot
	answer; the API layer turns those into 503.
	"""
	ctx = ctx or require_roles(C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)
	if ctx.has_any_role(C.ROLE_DA) and not ctx.unrestricted:
		frappe.throw(_("Use /assignments/me."), frappe.PermissionError)
	woreda, region = _resolve_officer_scope(ctx, woreda)
	refs = get_client().list_das(woreda=woreda, region=region)
	live = _live_assignments([r.da_id for r in refs])
	codes = {r.kebele for r in refs}
	for slot in live.values():
		codes |= {(slot["current"] or {}).get("kebele"), (slot["flagged"] or {}).get("kebele")}
	names = _area_names(codes)
	rows = [_row(r, live.get(r.da_id), names) for r in refs]
	summary = {
		"agents": len(rows),
		"unassigned": sum(1 for r in rows if r["status"] == "Unassigned"),
		"on_leave": sum(1 for r in rows if r["status"] == "On-leave"),
		"flagged": sum(1 for r in rows if r["flagged"]),
	}
	if status:
		rows = [r for r in rows if r["status"] == status]
	if assignment_status:
		rows = [r for r in rows if r["assignment_status"] == assignment_status]
	if q:
		needle = q.strip().lower()[:80]
		rows = [
			r
			for r in rows
			if needle
			in " ".join(str(v) for v in (r["agent"], r["da_id"], r["kebele_name"], r["kebele"]) if v).lower()
		]
	total = len(rows)
	limit = max(1, min(int(limit), 500))
	start = max(0, int(start))
	return rows[start : start + limit], total, summary


def my_assignment(ctx: ScopeContext | None = None) -> dict:
	"""The signed-in DA's own row (Assignments tab on My Farmers)."""
	ctx = ctx or require_roles(*READ_ROLES)
	own = sorted(ctx.da_ids)
	if not own:
		frappe.throw(_("Your account is not bound to a DA-ID."), frappe.PermissionError)
	ref = get_da_reference(own[0], ctx)
	live = _live_assignments([ref.da_id])
	slot = live.get(ref.da_id) or {"current": None, "flagged": None}
	names = _area_names(
		{ref.kebele, (slot["current"] or {}).get("kebele"), (slot["flagged"] or {}).get("kebele")}
	)
	row = _row(ref, slot, names)
	row["history"] = [
		serialize(r)
		for r in frappe.get_all(
			"DA Kebele Assignment",
			filters={"da_id": ref.da_id},
			fields=ASSIGNMENT_FIELDS,
			order_by="effective_date desc, creation desc",
			limit_page_length=20,
		)
	]
	return row


def coverage(ctx: ScopeContext | None = None, *, woreda: str | None = None) -> dict:
	"""Kebele coverage tiles for a Woreda (DA-37): agents per Kebele and a coverage state.

	Agents are counted on the posting in force; a Kebele that is the target of a pending
	Flagged decision is shown as Flagged so the Supervisor sees what needs a look.
	"""
	ctx = ctx or require_roles(C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)
	woreda, _region = _resolve_officer_scope(ctx, woreda)
	if not woreda:
		frappe.throw(_("woreda is required for coverage."), frappe.ValidationError)
	rows, _total, _summary = list_assignments(ctx, woreda=woreda, limit=500)
	kebeles = areas(level="Kebele", parent=woreda)
	by_kebele: dict[str, list[dict]] = {}
	flagged_targets: set[str] = set()
	for r in rows:
		if r["kebele"] and r["status"] != "Separated":
			by_kebele.setdefault(r["kebele"], []).append(r)
		if r["flagged"]:
			flagged_targets.add(r["flagged"]["kebele"])
	out = []
	for k in kebeles:
		agents = by_kebele.get(k["code"], [])
		if k["code"] in flagged_targets:
			state = "Flagged"
		elif not agents:
			state = "Uncovered"
		elif all(a["status"] == "On-leave" for a in agents):
			state = "Agent on leave"
		else:
			state = "Full"
		out.append(
			{
				"kebele": k["code"],
				"kebele_name": k["name_en"],
				"woreda": woreda,
				"agents": len(agents),
				"agent_ids": [a["da_id"] for a in agents],
				"farmers": sum(a["farmers"] or 0 for a in agents),
				"households": None,  # Farmer Registry link pending
				"coverage": state,
			}
		)
	known = {k["code"] for k in kebeles}
	unknown = sorted(set(by_kebele) - known)
	return {
		"woreda": woreda,
		"kebeles": out,
		"summary": {
			"kebeles": len(out),
			"full": sum(1 for r in out if r["coverage"] == "Full"),
			"uncovered": sum(1 for r in out if r["coverage"] == "Uncovered"),
			"flagged": sum(1 for r in out if r["coverage"] == "Flagged"),
			"agent_on_leave": sum(1 for r in out if r["coverage"] == "Agent on leave"),
		},
		"kebeles_not_in_master": unknown,  # registry postings we cannot place on the master
	}


# -- assign / reassign ----------------------------------------------------------------


def _lock_da(da_id: str) -> str:
	"""Serialise decisions for one DA across requests (MariaDB named lock)."""
	key = f"da_services:kebele_assignment:{da_id}"
	got = frappe.db.sql("select get_lock(%s, %s)", (key, LOCK_TIMEOUT_SECONDS))[0][0]
	if not got:
		frappe.throw(
			_("Another assignment for DA {0} is being saved; try again in a moment.").format(da_id),
			frappe.ValidationError,
		)
	return key


def _unlock(key: str) -> None:
	frappe.db.sql("select release_lock(%s)", (key,))


def _replayed(client_op_id):
	"""The same device operation landed from a parallel request: answer with its row."""
	existing = client_op_id and frappe.db.get_value(
		"DA Kebele Assignment", {"client_op_id": client_op_id}, "name"
	)
	if not existing:
		raise
	return serialize(frappe.get_doc("DA Kebele Assignment", existing))


def assign(
	*,
	da_id: str,
	kebele: str,
	effective_date=None,
	reason: str | None = None,
	mode: str = "Automatic",
	da_location: dict | None = None,
	client_op_id: str | None = None,
	ctx: ScopeContext | None = None,
) -> dict:
	"""Assign or reassign a DA to a Kebele with geofence validation (DA-38)."""
	ctx = ctx or require_roles(*WRITE_ROLES)
	if not ctx.unrestricted and not ctx.has_any_role(*WRITE_ROLES):
		frappe.throw(_("Only a Supervisor or Administrator can assign a Kebele."), frappe.PermissionError)
	if not da_id or not kebele:
		frappe.throw(_("da_id and kebele are required."), frappe.ValidationError)
	if mode not in ("Automatic", "Manual"):
		frappe.throw(_("mode must be Automatic or Manual."), frappe.ValidationError)
	client_op_id = (client_op_id or "").strip() or None

	loc = dict(da_location or {})
	if loc.get("lat") is not None or loc.get("lng") is not None:
		try:
			loc["lat"], loc["lng"] = geofence.validate_coordinates(loc.get("lat"), loc.get("lng"))
		except ValueError as exc:
			frappe.throw(_("da_location: {0}.").format(exc), frappe.ValidationError)

	if client_op_id:
		existing = frappe.db.get_value("DA Kebele Assignment", {"client_op_id": client_op_id}, "name")
		if existing:
			return serialize(frappe.get_doc("DA Kebele Assignment", existing))

	ref = get_da_reference(da_id, ctx)  # the DA must be inside the officer's scope
	if ref.active_status == C.ACTIVE_STATUS_SEPARATED:
		frappe.throw(_("DA {0} is separated and cannot be assigned.").format(da_id), frappe.ValidationError)

	area = frappe.db.get_value(
		"DA Administrative Area", kebele, ["level", "active", "name_en", "boundary_geojson"], as_dict=True
	)
	if not area or area.level != "Kebele":
		frappe.throw(_("{0} is not a Kebele in the administrative area master.").format(kebele))
	tree = ancestors_of(kebele)
	if not ctx.unrestricted and not ctx.covers(tree["region"], tree["woreda"], tree["zone"]):
		frappe.throw(_("Kebele {0} is outside your Woreda.").format(kebele), frappe.PermissionError)

	boundary = area.boundary_geojson
	if isinstance(boundary, str):
		try:
			boundary = json.loads(boundary) if boundary else None
		except ValueError:
			boundary = None
	result, why = geofence.check(loc.get("lat"), loc.get("lng"), boundary, area.name_en)
	state = "Flagged" if result == geofence.OUT_OF_BOUNDS else "Effective"

	# The decision itself is written under a per-DA lock and committed, so it is never
	# lost to a later failure of the registry push or the notification.
	lock = _lock_da(ref.da_id)
	try:
		doc = frappe.get_doc(
			{
				"doctype": "DA Kebele Assignment",
				"da_id": ref.da_id,
				"kebele": kebele,
				"state": state,
				"mode": mode,
				"effective_date": getdate(effective_date) if effective_date else getdate(),
				"reason": reason,
				"assigned_by": ctx.user,
				"geofence_result": result,
				"geofence_reason": why,
				"da_lat": loc.get("lat"),
				"da_lng": loc.get("lng"),
				"registry_sync_state": "Pending" if state == "Effective" else "Not sent",
				"client_op_id": client_op_id,
			}
		)
		try:
			doc.insert(ignore_permissions=True)
		except frappe.DuplicateEntryError:
			return _replayed(client_op_id)
		except frappe.UniqueValidationError:
			return _replayed(client_op_id)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
	finally:
		_unlock(lock)

	if state == "Effective":
		_push_to_registry(doc, ref, reason, ctx)
	_notify(doc, ref, area.name_en, state, why, reason)
	doc.reload()
	return serialize(doc)


def _notify(doc, ref: DAReference, kebele_name: str, state: str, why: str, reason: str | None) -> None:
	"""Best effort: a notification problem is logged, never fails the assignment."""
	try:
		notify_da(
			ref.da_id,
			kind="Assignment",
			title=(
				_("Assigned to {0}").format(kebele_name)
				if state == "Effective"
				else _("Assignment to {0} flagged").format(kebele_name)
			)[:140],
			body=why if state == "Flagged" else (reason or None),
			href="/assignments",
			event="assignment_created" if state == "Effective" else "assignment_flagged",
			reference_doctype="DA Kebele Assignment",
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(title=f"DA Kebele Assignment {doc.name}: notification failed")


def _push_to_registry(doc, ref: DAReference, reason: str | None, ctx: ScopeContext) -> None:
	outcome = LifecycleOutcome(
		da_id=ref.da_id,
		transition_type="Transfer",
		new_active_status=None,
		effective_date=str(doc.effective_date),
		reason=reason or "Kebele assignment",
		approver=ctx.user,
		reference=doc.name,
		target_kebele=doc.kebele,
	)
	try:
		result = get_client().push_lifecycle_outcome(outcome)
	except (DARegistryUnavailable, DARegistryContractPending, DARegistryError) as exc:
		doc.db_set("registry_sync_state", "Failed", update_modified=False)
		doc.db_set("registry_error", f"{exc.__class__.__name__}: {exc}"[:500], update_modified=False)
		return
	# db_set on purpose: these are integration bookkeeping fields, not user input, and
	# the row must not be re-validated or have its modified stamp moved by a sync result.
	if result.accepted:
		doc.db_set("registry_sync_state", "Accepted", update_modified=False)
		doc.db_set("registry_reference", result.external_reference, update_modified=False)
		if result.message:
			doc.db_set("registry_error", result.message[:500], update_modified=False)  # warnings, if any
	else:
		doc.db_set("registry_sync_state", "Failed", update_modified=False)
		doc.db_set("registry_error", (result.message or "rejected")[:500], update_modified=False)
