"""DA leave requests (lifecycle slice 1: "Supervisor can manage DA").

A DA asks for leave; the Woreda Supervisor approves or rejects. When an approved
leave starts, the DA's Active Status becomes On-leave in the DA Registry
(`push_lifecycle_outcome`, transition "Leave") so Kebele coverage shows
"Agent on leave"; when it ends the DA is reactivated (transition "Reactivation").
The scheduler moves leaves through those two steps daily.

Scope mirrors profile change requests: DA → own; Supervisor → their Woreda;
Executive → their Region (read); Admin → all.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import getdate, now_datetime, today

from da_services.da_services.doctype.da_leave_request.da_leave_request import LEAVE_TYPES, OPEN_STATES
from da_services.integrations.da_registry.client import (
	DARegistryContractPending,
	DARegistryError,
	DARegistryUnavailable,
	get_client,
)
from da_services.integrations.da_registry.schemas import LifecycleOutcome
from da_services.services import constants as C
from da_services.services.da_lookup import get_da_reference
from da_services.services.notifications import notify_da
from da_services.utils import audit
from da_services.utils.permissions import ScopeContext, require_roles

DOCTYPE = "DA Leave Request"
READ_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)
DECIDE_ROLES = (C.ROLE_SUPERVISOR, C.ROLE_ADMIN)
DECISIONS = {"Approve": "Approved", "Reject": "Rejected"}
MAX_DAYS = 365
MAX_REASON = 500
LOCK_TIMEOUT_SECONDS = 10

FIELDS = [
	"name",
	"da_id",
	"leave_type",
	"status",
	"requested_by",
	"reason",
	"from_date",
	"to_date",
	"days",
	"region",
	"zone",
	"woreda",
	"approver",
	"decided_at",
	"decision_note",
	"registry_sync_state",
	"registry_reference",
	"registry_error",
	"activated_at",
	"completed_at",
	"client_op_id",
	"creation",
	"modified",
]


def serialize(row) -> dict:
	out = {f: row.get(f) for f in FIELDS}
	out["id"] = out.pop("name")
	for f in ("from_date", "to_date", "decided_at", "activated_at", "completed_at", "creation", "modified"):
		if out.get(f) is not None and hasattr(out[f], "isoformat"):
			out[f] = out[f].isoformat()
	return out


def _own_da_id(ctx: ScopeContext) -> str:
	if not ctx.da_ids:
		frappe.throw(_("Your account is not bound to a DA-ID."), frappe.PermissionError)
	return sorted(ctx.da_ids)[0]


# -- submit / cancel -----------------------------------------------------------------


def submit_leave_request(
	*,
	leave_type: str,
	from_date,
	to_date,
	reason: str | None,
	client_op_id: str | None = None,
	ctx: ScopeContext | None = None,
) -> dict:
	"""A DA asks for leave; goes to their Woreda Supervisor."""
	ctx = ctx or require_roles(C.ROLE_DA)
	if not ctx.has_any_role(C.ROLE_DA):
		frappe.throw(_("Only a Development Agent can request leave."), frappe.PermissionError)
	client_op_id = (client_op_id or "").strip() or None
	if client_op_id:
		existing = frappe.db.get_value(DOCTYPE, {"client_op_id": client_op_id}, "name")
		if existing:
			return serialize(frappe.get_doc(DOCTYPE, existing))

	if leave_type not in LEAVE_TYPES:
		frappe.throw(
			_("leave_type must be one of {0}.").format(", ".join(LEAVE_TYPES)), frappe.ValidationError
		)
	try:
		start, end = getdate(from_date), getdate(to_date)
	except Exception:
		frappe.throw(_("from_date and to_date must be dates (YYYY-MM-DD)."), frappe.ValidationError)
	if end < start:
		frappe.throw(_("to_date cannot be before from_date."), frappe.ValidationError)
	if start < getdate(today()):
		frappe.throw(_("from_date cannot be in the past."), frappe.ValidationError)
	if (end - start).days + 1 > MAX_DAYS:
		frappe.throw(_("A leave request cannot exceed {0} days.").format(MAX_DAYS), frappe.ValidationError)
	reason = (reason or "").strip()
	if len(reason) < 5:
		frappe.throw(_("A reason of at least 5 characters is required."), frappe.ValidationError)
	if len(reason) > MAX_REASON:
		frappe.throw(_("The reason cannot exceed {0} characters.").format(MAX_REASON), frappe.ValidationError)

	da_id = _own_da_id(ctx)
	ref = get_da_reference(da_id, ctx)

	# Overlap check and insert are one critical section per DA: two requests from
	# the same DA racing each other (double tap, two devices) are serialised by a
	# database lock, so the second one sees the first one's row and is rejected.
	lock = _lock_da(da_id)
	try:
		overlap = frappe.get_all(
			DOCTYPE,
			filters={
				"da_id": da_id,
				"status": ("in", list(OPEN_STATES)),
				"from_date": ("<=", end),
				"to_date": (">=", start),
			},
			pluck="name",
			limit_page_length=1,
		)
		if overlap:
			frappe.throw(
				_("You already have an open leave request ({0}) overlapping these dates.").format(overlap[0]),
				frappe.ValidationError,
			)

		doc = frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"da_id": da_id,
				"leave_type": leave_type,
				"status": "Pending",
				"requested_by": ctx.user,
				"reason": reason,
				"from_date": start,
				"to_date": end,
				"region": ref.region,
				"zone": ref.zone,
				"woreda": ref.woreda,
				"client_op_id": client_op_id,
			}
		)
		doc.insert(ignore_permissions=True)
	finally:
		_unlock(lock)
	audit.record(
		"leave_requested",
		entity_type=DOCTYPE,
		entity_name=doc.name,
		details={"leave_type": leave_type, "from": str(start), "to": str(end), "days": doc.days},
	)
	return serialize(doc)


def _lock_da(da_id: str) -> str | None:
	"""Serialise leave submissions for one DA across workers (MariaDB named lock).

	Same pattern as services/assignments._lock_da. The lock is released in the
	caller's finally block, and by MariaDB itself if the connection drops.
	"""
	if frappe.db.db_type != "mariadb":
		return None  # no named locks on other engines; the request transaction still applies
	key = f"da_services:leave_request:{da_id}"
	got = frappe.db.sql("select get_lock(%s, %s)", (key, LOCK_TIMEOUT_SECONDS))[0][0]
	if not got:
		frappe.throw(
			_("Another leave request for DA {0} is being saved; try again in a moment.").format(da_id),
			frappe.ValidationError,
		)
	return key


def _unlock(key: str | None) -> None:
	if key:
		frappe.db.sql("select release_lock(%s)", (key,))


def cancel_leave_request(name: str, ctx: ScopeContext | None = None) -> dict:
	"""The DA withdraws a request that has not started yet."""
	ctx = ctx or require_roles(C.ROLE_DA)
	doc = frappe.get_doc(DOCTYPE, name)
	if not ctx.owns_da(doc.da_id):
		frappe.throw(_("You can only cancel your own leave requests."), frappe.PermissionError)
	if doc.status not in ("Pending", "Approved"):
		frappe.throw(
			_("Only a Pending or Approved (not yet started) leave can be cancelled."), frappe.ValidationError
		)
	doc.status = "Cancelled"
	doc.flags.ignore_permissions = True
	doc.save()
	audit.record("leave_cancelled", entity_type=DOCTYPE, entity_name=doc.name)
	return serialize(doc)


# -- list / get ----------------------------------------------------------------------


def _check_read(doc, ctx: ScopeContext) -> None:
	if ctx.unrestricted:
		return
	if ctx.has_any_role(C.ROLE_DA) and ctx.owns_da(doc.da_id):
		return
	if ctx.has_any_role(C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE) and ctx.covers(
		doc.region, doc.woreda, zone=doc.zone
	):
		return
	frappe.throw(_("Leave request {0} is outside your scope.").format(doc.name), frappe.PermissionError)


def list_leave_requests(
	ctx: ScopeContext | None = None,
	*,
	status: str | None = None,
	da_id: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int]:
	"""DA: own. Supervisor / Executive: their Woreda / Region (scope via the query hook)."""
	ctx = ctx or require_roles(*READ_ROLES)
	filters: dict = {}
	if status:
		filters["status"] = status
	if da_id:
		filters["da_id"] = da_id
	limit = max(1, min(int(limit), 500))
	start = max(0, int(start))
	rows = frappe.get_list(
		DOCTYPE,
		filters=filters,
		fields=FIELDS,
		order_by="from_date desc, creation desc",
		limit_page_length=limit,
		limit_start=start,
	)
	total = len(frappe.get_list(DOCTYPE, filters=filters, pluck="name", limit_page_length=0))
	return [serialize(r) for r in rows], total


def get_leave_request(name: str, ctx: ScopeContext | None = None) -> dict:
	ctx = ctx or require_roles(*READ_ROLES)
	doc = frappe.get_doc(DOCTYPE, name)
	_check_read(doc, ctx)
	return serialize(doc)


# -- decide --------------------------------------------------------------------------


def decide(name: str, *, decision: str, note: str | None = None, ctx: ScopeContext | None = None) -> dict:
	"""Supervisor of the DA's Woreda (or Admin) approves or rejects.

	An approved leave that starts today (or earlier) is activated at once;
	otherwise the daily scheduler activates it on its first day.
	"""
	ctx = ctx or require_roles(*DECIDE_ROLES)
	if decision not in DECISIONS:
		frappe.throw(_("decision must be one of {0}.").format(", ".join(DECISIONS)), frappe.ValidationError)
	note = (note or "").strip()
	if decision == "Reject" and len(note) < 5:
		frappe.throw(_("A note of at least 5 characters is required to reject."), frappe.ValidationError)

	doc = frappe.get_doc(DOCTYPE, name)
	if not ctx.unrestricted:
		# Zone is passed too: a Zone-only grant must not be widened to the Region
		# (scope_decision treats a Region grant as covering only when no narrower
		# grant exists).
		if not ctx.has_any_role(C.ROLE_SUPERVISOR) or not ctx.covers(doc.region, doc.woreda, zone=doc.zone):
			frappe.throw(_("Leave request {0} is outside your Woreda.").format(name), frappe.PermissionError)
	if ctx.owns_da(doc.da_id) or doc.requested_by == ctx.user:
		frappe.throw(_("You cannot decide your own leave request."), frappe.PermissionError)
	if doc.status != "Pending":
		frappe.throw(_("Leave request {0} is already {1}.").format(name, doc.status), frappe.ValidationError)

	doc.status = DECISIONS[decision]
	doc.approver = ctx.user
	doc.decided_at = now_datetime()
	doc.decision_note = note or None
	doc.flags.ignore_permissions = True
	doc.save()
	audit.record(
		"leave_decided",
		entity_type=DOCTYPE,
		entity_name=doc.name,
		details={"decision": decision, "note": note},
	)

	if doc.status == "Approved" and getdate(doc.from_date) <= getdate(today()):
		activate(doc)
	_notify(doc)
	doc.reload()
	return serialize(doc)


# -- lifecycle: start / end --------------------------------------------------------


def _push(doc, *, transition_type: str, new_active_status: str, effective_date, reference: str) -> bool:
	"""Push one lifecycle outcome to the DA Registry; record the result on the row."""
	outcome = LifecycleOutcome(
		da_id=doc.da_id,
		transition_type=transition_type,
		new_active_status=new_active_status,
		effective_date=str(effective_date),
		reason=f"{doc.leave_type} leave {doc.from_date} to {doc.to_date}: {doc.reason}"[:500],
		approver=doc.approver or "system",
		reference=reference,
	)
	try:
		result = get_client().push_lifecycle_outcome(outcome)
	except (DARegistryUnavailable, DARegistryContractPending, DARegistryError) as exc:
		return _push_failed(doc, transition_type, f"{exc.__class__.__name__}: {exc}")
	if not result.accepted:
		return _push_failed(doc, transition_type, result.message or "rejected")
	doc.db_set("registry_sync_state", "Accepted", update_modified=False)
	doc.db_set("registry_reference", result.external_reference, update_modified=False)
	doc.db_set("registry_error", None, update_modified=False)
	return True


def _push_failed(doc, transition_type: str, error: str) -> bool:
	"""Record a failed registry push on the row (the local state stands; the daily
	scheduler retries until the registry accepts, so the two systems converge)."""
	doc.db_set("registry_sync_state", "Failed", update_modified=False)
	doc.db_set("registry_error", error[:500], update_modified=False)
	audit.record(
		"leave_registry_sync_failed",
		entity_type=DOCTYPE,
		entity_name=doc.name,
		details={"transition": transition_type, "error": error[:200]},
	)
	frappe.logger("da_services").warning(
		"DA Leave Request %s: registry push (%s) failed, will retry daily: %s",
		doc.name,
		transition_type,
		error,
	)
	return False


def retry_registry_push(doc) -> bool:
	"""Re-send the lifecycle outcome a row is missing in the registry.

	Idempotent: the registry de-duplicates on the reference (the row name, or
	"<name>-return" for the reactivation), so a retry after a lost response is
	harmless. Rows that are not Active / Completed have nothing to push.
	"""
	if doc.status == "Active":
		return _push(
			doc,
			transition_type="Leave",
			new_active_status=C.ACTIVE_STATUS_ON_LEAVE,
			effective_date=doc.from_date,
			reference=doc.name,
		)
	if doc.status == "Completed":
		return _push(
			doc,
			transition_type="Reactivation",
			new_active_status=C.ACTIVE_STATUS_ACTIVE,
			effective_date=doc.to_date,
			reference=f"{doc.name}-return",
		)
	doc.db_set("registry_sync_state", "Not sent", update_modified=False)
	doc.db_set("registry_error", None, update_modified=False)
	return True


def activate(doc) -> None:
	"""Leave starts: DA becomes On-leave in the registry; coverage shows 'Agent on leave'."""
	if doc.status != "Approved":
		return
	doc.status = "Active"
	doc.activated_at = now_datetime()
	doc.flags.ignore_permissions = True
	doc.save()
	_push(
		doc,
		transition_type="Leave",
		new_active_status=C.ACTIVE_STATUS_ON_LEAVE,
		effective_date=doc.from_date,
		reference=doc.name,
	)
	audit.record("leave_started", entity_type=DOCTYPE, entity_name=doc.name)


def complete(doc) -> None:
	"""Leave ends: DA is reactivated in the registry."""
	if doc.status != "Active":
		return
	doc.status = "Completed"
	doc.completed_at = now_datetime()
	doc.flags.ignore_permissions = True
	doc.save()
	_push(
		doc,
		transition_type="Reactivation",
		new_active_status=C.ACTIVE_STATUS_ACTIVE,
		effective_date=doc.to_date,
		reference=f"{doc.name}-return",
	)
	audit.record("leave_completed", entity_type=DOCTYPE, entity_name=doc.name)
	try:
		notify_da(
			doc.da_id,
			kind="System",
			title=_("Welcome back: your {0} leave has ended").format(doc.leave_type.lower())[:140],
			href="/profile",
			event="leave_completed",
			reference_doctype=DOCTYPE,
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(title=f"DA Leave Request {doc.name}: notification failed")


def process_leave_schedule() -> dict:
	"""Daily: start approved leaves whose first day has come, end active leaves whose
	last day has passed, and retry registry pushes that failed.

	Safe to run more than once a day and safe to interrupt:
	- each row is its own unit of work (commit per row; one bad row is logged
	  and skipped, the rest still move);
	- the state checks in activate() / complete() make a repeat run a no-op for
	  rows already moved;
	- registry retries de-duplicate on the row's reference.
	Returns counts: started, ended, retried (registry pushes that succeeded on
	retry) and failed (rows that raised and were skipped this run).
	"""
	stats = {"started": 0, "ended": 0, "retried": 0, "failed": 0}

	def _each(names: list[str], step, counter: str, *, count_when_true: bool = False) -> None:
		for name in names:
			try:
				ok = step(frappe.get_doc(DOCTYPE, name))
				frappe.db.commit()
			except Exception:
				frappe.db.rollback()
				stats["failed"] += 1
				frappe.log_error(
					title=f"DA Leave Request {name}: scheduler step {step.__name__} failed",
					message=frappe.get_traceback(),
				)
				continue
			if not count_when_true or ok:
				stats[counter] += 1

	_each(
		frappe.get_all(DOCTYPE, filters={"status": "Approved", "from_date": ("<=", today())}, pluck="name"),
		activate,
		"started",
	)
	_each(
		frappe.get_all(DOCTYPE, filters={"status": "Active", "to_date": ("<", today())}, pluck="name"),
		complete,
		"ended",
	)
	_each(
		frappe.get_all(
			DOCTYPE,
			filters={"registry_sync_state": "Failed", "status": ("in", ("Active", "Completed"))},
			pluck="name",
		),
		retry_registry_push,
		"retried",
		count_when_true=True,
	)
	return stats


def _notify(doc) -> None:
	"""Tell the DA the outcome; a notification problem never undoes the decision."""
	titles = {
		"Active": _("Your {0} leave was approved and has started"),
		"Approved": _("Your {0} leave was approved ({1} to {2})"),
		"Rejected": _("Your {0} leave was rejected"),
	}
	try:
		notify_da(
			doc.da_id,
			kind="Approval",
			title=titles.get(doc.status, _("Your {0} leave changed")).format(
				doc.leave_type.lower(), doc.from_date, doc.to_date
			)[:140],
			body=doc.decision_note,
			href="/profile",
			event="leave_decided",
			reference_doctype=DOCTYPE,
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(title=f"DA Leave Request {doc.name}: notification failed")
