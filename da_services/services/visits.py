"""DA Visit use-cases behind the dashboard, planner and outcome form (data-model 5.3).

Scope (FSD Appendix D): a DA plans, starts and submits visits for their own DA-ID; a
Supervisor may plan for a DA in their Woreda (recorded in `assigned_by`) and read the
Woreda's visits; an Executive reads their Region; OAN Administrator is unrestricted;
Communications Officer has no access.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, time, timedelta
from io import BytesIO

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate, now_datetime

from da_services.da_services.doctype.da_visit.da_visit import (
	COMPLETE,
	IN_PROGRESS,
	OPEN_STATES,
	PLANNED_STATES,
)
from da_services.services import constants as C
from da_services.services import visit_specializations as spec_svc
from da_services.services.da_lookup import get_da_reference
from da_services.utils.permissions import ScopeContext, require_roles

WRITE_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_ADMIN)
READ_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)

# Open visits block another visit that covers the same specialization.
# Completed, missed, and cancelled visits do not.
BLOCKING_STATUSES = list(OPEN_STATES)

FIELDS = [
	"name",
	"da_id",
	"farmer_id",
	"visit_type",
	"physical_status",
	"purpose",
	"scheduled_at",
	"duration_min",
	"priority",
	"priority_reason",
	"source",
	"assigned_by",
	"farmer_confirmation",
	"confirmation_note",
	"plot_ref",
	"location_lat",
	"location_lng",
	"rescheduled_from",
	"arrived_at",
	"started_at",
	"completed_at",
	"outcome",
	"next_action_type",
	"next_action_date",
	"follow_up_task",
	"follow_up_visit",
	"region",
	"woreda",
	"kebele",
	"client_op_id",
	"modified",
]
_DT = ("scheduled_at", "arrived_at", "started_at", "completed_at", "modified")


def _derived(row: dict) -> dict:
	status = row.get("physical_status")
	sched = row.get("scheduled_at")
	if status == COMPLETE:
		ui, planner = "Completed", "completed"
	elif status == "Missed":
		ui, planner = "Missed", "scheduled"
	elif status in OPEN_STATES:
		ui = "Confirmed" if row.get("farmer_confirmation") == "Confirmed" else "Planned"
		planner = "overdue" if sched and get_datetime(sched) < now_datetime() else "scheduled"
	else:
		ui, planner = status, "scheduled"
	return {"ui_status": ui, "planner_status": planner}


def serialize(doc) -> dict:
	out = {f: doc.get(f) for f in FIELDS}
	for f in _DT:
		if out.get(f) is not None and hasattr(out[f], "isoformat"):
			out[f] = out[f].isoformat()
	out.update(
		_derived(
			{
				"physical_status": doc.get("physical_status"),
				"scheduled_at": doc.get("scheduled_at"),
				"farmer_confirmation": doc.get("farmer_confirmation"),
			}
		)
	)
	if hasattr(doc, "get_doc_before_save"):  # a Document, not a list row
		out["advisory_qa"] = [
			{"question": r.question, "answer": r.answer} for r in (doc.get("advisory_qa") or [])
		]
		out["attachments"] = [
			{
				"file": r.file,
				"kind": r.kind,
				"captured_at": r.captured_at.isoformat() if r.captured_at else None,
			}
			for r in (doc.get("attachments") or [])
		]
		# DA-211 Phase 3: Include specializations in response
		out["specializations"] = [r.specialization for r in (doc.get("specializations") or [])]
	return out


def _check_specialization_conflict(farmer_id: str, new_specializations: list[str]) -> dict | None:
	"""Check if farmer has active visit with overlapping specializations (DA-211 Phase 5).

	Args:
		farmer_id: Farmer identifier
		new_specializations: Specializations for the new visit being scheduled

	Returns:
		Conflict dict if overlap found, None if no conflict
		Conflict dict: {
			"blocked": True,
			"reason": "...",
			"blocked_specializations": [...],
			"conflicting_visit_id": "..."
		}
	"""
	if not farmer_id or not new_specializations:
		return None

	# Lock the farmer's visit records to prevent race conditions
	# Use SELECT FOR UPDATE to ensure no concurrent scheduling for same farmer
	active_visits = frappe.db.sql(
		"""
		SELECT
			v.name,
			v.visit_type,
			v.physical_status,
			v.scheduled_at
		FROM `tabDA Visit` v
		WHERE
			v.farmer_id = %(farmer_id)s
			AND v.physical_status IN %(blocking_statuses)s
		FOR UPDATE
		""",
		{"farmer_id": farmer_id, "blocking_statuses": BLOCKING_STATUSES},
		as_dict=True,
	)

	if not active_visits:
		return None

	# Check each active visit for specialization overlap
	for visit in active_visits:
		# Get visit specializations
		visit_specs = frappe.get_all(
			"DA Visit Specialization",
			filters={"parent": visit["name"]},
			pluck="specialization",
		)

		if not visit_specs:
			continue

		# Check for overlap
		overlap = set(new_specializations) & set(visit_specs)

		if overlap:
			return {
				"blocked": True,
				"reason": f"Farmer already has an active {visit['visit_type']} visit covering: {', '.join(sorted(overlap))}",
				"blocked_specializations": sorted(list(overlap)),
				"conflicting_visit_id": visit["name"],
				"conflicting_visit_status": visit["physical_status"],
			}

	return None


def _resolve_da_for_write(da_id: str | None, ctx: ScopeContext) -> tuple[str, str | None]:
	"""(da_id, assigned_by). A DA acts on their own DA-ID; an officer names a DA in scope."""
	if ctx.has_any_role(C.ROLE_DA) and not ctx.unrestricted:
		own = sorted(ctx.da_ids)
		if not own:
			frappe.throw(_("Your account is not bound to a DA-ID."), frappe.PermissionError)
		return own[0], None
	if not da_id:
		frappe.throw(_("da_id is required."), frappe.ValidationError)
	get_da_reference(da_id, ctx)  # raises PermissionError when out of scope, audited
	return da_id, ctx.user


def plan_visit(
	*,
	farmer_id: str,
	scheduled_at,
	visit_type: str = "Crop Survey",
	purpose: str | None = None,
	duration_min: int = 45,
	priority: str = "Medium",
	priority_reason: str | None = None,
	plot_ref: str | None = None,
	location: dict | None = None,
	notify_farmer_sms: bool = False,
	advisory_qa: list[dict] | None = None,
	da_id: str | None = None,
	client_op_id: str | None = None,
	ctx: ScopeContext | None = None,
) -> dict:
	ctx = ctx or require_roles(*WRITE_ROLES)
	if not farmer_id:
		frappe.throw(_("farmer_id is required."), frappe.ValidationError)
	if not scheduled_at:
		frappe.throw(_("scheduled_at is required."), frappe.ValidationError)
	da_id, assigned_by = _resolve_da_for_write(da_id, ctx)

	if client_op_id:
		existing = frappe.db.get_value("DA Visit", {"client_op_id": client_op_id}, "name")
		if existing:
			return serialize(frappe.get_doc("DA Visit", existing))

	# DA-211 Phase 5: Check for specialization conflicts before creating visit
	new_specializations = spec_svc.derive_visit_specializations(visit_type or "Crop Survey", da_id)
	conflict = _check_specialization_conflict(farmer_id, new_specializations)

	if conflict:
		frappe.throw(
			_(conflict["reason"]),
			frappe.ValidationError,
			title=_("Visit Scheduling Conflict"),
		)

	location = location or {}
	doc = frappe.get_doc(
		{
			"doctype": "DA Visit",
			"da_id": da_id,
			"farmer_id": farmer_id,
			"visit_type": visit_type or "Crop Survey",
			"purpose": purpose,
			"scheduled_at": get_datetime(scheduled_at),
			"duration_min": int(duration_min or 45),
			"priority": priority or "Medium",
			"priority_reason": priority_reason,
			"plot_ref": plot_ref,
			"location_lat": location.get("lat"),
			"location_lng": location.get("lng"),
			"notify_farmer_sms": 1 if notify_farmer_sms else 0,
			"source": "Planned",
			"assigned_by": assigned_by,
			"client_op_id": client_op_id,
		}
	)
	if visit_type == "Advisory":
		for row in advisory_qa or []:
			if (row.get("question") or "").strip():
				doc.append(
					"advisory_qa",
					{"question": row["question"].strip(), "answer": (row.get("answer") or "").strip()},
				)

	# DA-211 Phase 3: Derive and set visit specializations
	specializations = spec_svc.derive_visit_specializations(visit_type or "Crop Survey", da_id)
	spec_svc.set_visit_specializations(doc, specializations)

	doc.insert(ignore_permissions=True)
	return serialize(doc)


def _load_in_scope(name: str, ctx: ScopeContext, write: bool = False):
	doc = frappe.get_doc("DA Visit", name)
	if ctx.unrestricted:
		return doc
	if ctx.has_any_role(C.ROLE_DA):
		if ctx.owns_da(doc.da_id):
			return doc
	elif ctx.has_any_role(C.ROLE_SUPERVISOR) and ctx.covers(doc.region, doc.woreda):
		return doc
	elif not write and ctx.has_any_role(C.ROLE_EXECUTIVE) and ctx.covers(doc.region, doc.woreda):
		return doc
	frappe.throw(_("Visit {0} is outside your scope.").format(name), frappe.PermissionError)


def get_visit(name: str, ctx: ScopeContext | None = None) -> dict:
	ctx = ctx or require_roles(*READ_ROLES)
	return serialize(_load_in_scope(name, ctx))


def start_visit(
	name: str,
	*,
	arrived_at=None,
	gps: dict | None = None,
	farmer_present: bool = True,
	client_op_id: str | None = None,
	ctx=None,
) -> dict:
	ctx = ctx or require_roles(*WRITE_ROLES)
	doc = _load_in_scope(name, ctx, write=True)
	if client_op_id and doc.physical_status == IN_PROGRESS:
		return serialize(doc)  # device replay of a check-in that already landed
	doc.flags.ignore_permissions = True
	doc.start_visit(arrived_at=arrived_at, gps=gps, farmer_present=farmer_present)
	return serialize(doc)


def submit_outcome(name: str, *, ctx=None, client_op_id: str | None = None, **outcome) -> dict:
	ctx = ctx or require_roles(*WRITE_ROLES)
	doc = _load_in_scope(name, ctx, write=True)
	if client_op_id and doc.physical_status == COMPLETE and doc.outcome_client_op_id == client_op_id:
		return serialize(doc)  # same device operation again: same answer, no second follow-up
	doc.flags.ignore_permissions = True
	doc.submit_outcome(**outcome)
	if client_op_id:
		doc.db_set("outcome_client_op_id", client_op_id, update_modified=False)
	doc.reload()
	return serialize(doc)


# Evidence a visit may carry: photos of the farm and scanned documents. Anything else is
# refused by extension *and* by content, so a renamed executable never reaches storage.
EVIDENCE_TYPES: dict[str, str] = {
	"jpg": "image",
	"jpeg": "image",
	"png": "image",
	"webp": "image",
	"pdf": "pdf",
}
DEFAULT_EVIDENCE_MAX_MB = 10


def _evidence_max_bytes() -> int:
	return int(frappe.conf.get("da_evidence_max_mb") or DEFAULT_EVIDENCE_MAX_MB) * 1024 * 1024


def validate_evidence(content: bytes, filename: str) -> str:
	"""Refuse evidence that is too large, of a type we do not take, or whose bytes do not
	match the extension. Returns the file type ("image" / "pdf")."""
	if not content:
		frappe.throw(_("file is required."), frappe.ValidationError)
	if len(content) > _evidence_max_bytes():
		frappe.throw(
			_("File is larger than {0} MB.").format(_evidence_max_bytes() // (1024 * 1024)),
			frappe.ValidationError,
		)
	ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
	kind = EVIDENCE_TYPES.get(ext)
	if not kind:
		frappe.throw(
			_("File type .{0} is not accepted; use {1}.").format(
				ext or "?", ", ".join(sorted(EVIDENCE_TYPES))
			),
			frappe.ValidationError,
		)
	if kind == "pdf":
		try:
			from pypdf import PdfReader

			if not content.startswith(b"%PDF-"):
				raise ValueError("missing PDF header")
			PdfReader(BytesIO(content))
		except Exception:
			frappe.throw(_("The file is not a readable PDF."), frappe.ValidationError)
		return kind
	try:
		from PIL import Image

		with Image.open(BytesIO(content)) as img:
			img.verify()
	except Exception:
		frappe.throw(_("The file is not a readable image."), frappe.ValidationError)
	return kind


def _attachment_dict(row) -> dict:
	return {
		"name": row.name,
		"file": row.file,
		"kind": row.kind,
		"content_hash": row.content_hash,
		"captured_at": row.captured_at.isoformat() if row.captured_at else None,
		"gps": {"lat": row.gps_lat, "lng": row.gps_lng},
		"sync_state": row.sync_state,
	}


def add_attachment(
	name: str,
	*,
	content: bytes,
	filename: str,
	kind: str = "Crop",
	gps: dict | None = None,
	captured_at=None,
	content_hash: str | None = None,
	ctx=None,
) -> dict:
	"""Store one piece of geo-tagged evidence on a visit (HLD 9.2: duplicates by content hash).

	The same bytes sent twice (a retry after a lost response) return the existing row.
	A `content_hash` the device computed is checked against the bytes received, so a
	corrupted upload is refused rather than stored.
	"""
	ctx = ctx or require_roles(*WRITE_ROLES)
	validate_evidence(content, filename)
	# Row lock for the rest of the transaction: two uploads of the same bytes at the same
	# moment then run one after the other, and the second sees the first's row.
	if not frappe.db.get_value("DA Visit", name, "name", for_update=True):
		frappe.throw(_("Visit {0} not found.").format(name), frappe.DoesNotExistError)
	doc = _load_in_scope(name, ctx, write=True)
	if doc.physical_status not in (*OPEN_STATES, COMPLETE):
		frappe.throw(_("Visit {0} is closed; evidence cannot be added.").format(name), frappe.ValidationError)
	digest = hashlib.sha256(content).hexdigest()
	if content_hash and content_hash.strip().lower() != digest:
		frappe.throw(_("content_hash does not match the uploaded file."), frappe.ValidationError)
	for row in doc.get("attachments") or []:
		if row.content_hash == digest:
			return _attachment_dict(row)
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename or f"{name}-{digest[:8]}",
			"content": content,
			"attached_to_doctype": "DA Visit",
			"attached_to_name": doc.name,
			"is_private": 1,
		}
	).insert(ignore_permissions=True)
	gps = gps or {}
	row = doc.append(
		"attachments",
		{
			"file": file_doc.file_url,
			"kind": kind or "Crop",
			"content_hash": digest,
			"captured_at": get_datetime(captured_at) if captured_at else now_datetime(),
			"gps_lat": gps.get("lat"),
			"gps_lng": gps.get("lng"),
			"sync_state": "Accepted",
		},
	)
	doc.flags.ignore_permissions = True
	doc.save()
	return _attachment_dict(row)


def resolve_da_for_read(da_id: str | None, ctx: ScopeContext) -> str:
	"""The DA-ID a report is about: a DA's own, or one an officer names inside their scope."""
	if ctx.has_any_role(C.ROLE_DA) and not ctx.unrestricted:
		own = sorted(ctx.da_ids)
		if not own:
			frappe.throw(_("Your account is not bound to a DA-ID."), frappe.PermissionError)
		return own[0]
	if not da_id:
		frappe.throw(_("da_id is required."), frappe.ValidationError)
	get_da_reference(da_id, ctx)
	return da_id


def _week_range(week: str | None) -> tuple[date, date, str]:
	"""Monday..Sunday of an ISO week ("2026-W42"); the current week when not given."""
	if week:
		m = re.fullmatch(r"(\d{4})-W(\d{1,2})", str(week).strip().upper())
		if not m:
			frappe.throw(_("week must look like 2026-W42."), frappe.ValidationError)
		try:
			# rejects week 0, 54, and 53 in a 52-week year
			start = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
		except ValueError:
			frappe.throw(_("{0} is not a valid ISO week.").format(week), frappe.ValidationError)
	else:
		iso = getdate().isocalendar()
		start = date.fromisocalendar(iso[0], iso[1], 1)
	iso = start.isocalendar()
	return start, start + timedelta(days=6), f"{iso[0]}-W{iso[1]:02d}"


def activity_report(
	ctx: ScopeContext | None = None, *, week: str | None = None, da_id: str | None = None
) -> dict:
	"""Weekly activity report (docs/api-da-screens.md 2.9) from DA Visit and DA Task.

	`issues_raised` stays 0 until grievances are linked to visits (DA Grievance Link).
	"""
	ctx = ctx or require_roles(*READ_ROLES)
	da_id = resolve_da_for_read(da_id, ctx)
	start, end, label = _week_range(week)
	rng = ("between", [datetime.combine(start, time.min), datetime.combine(end, time.max)])
	done = frappe.get_all(
		"DA Visit",
		filters={"da_id": da_id, "physical_status": COMPLETE, "completed_at": rng},
		fields=[
			"name",
			"farmer_id",
			"kebele",
			"visit_type",
			"purpose",
			"outcome",
			"advice_given",
			"follow_up_task",
			"completed_at",
		],
		order_by="completed_at desc",
	)
	planned = frappe.db.count(
		"DA Visit", {"da_id": da_id, "scheduled_at": rng, "physical_status": ("in", list(PLANNED_STATES))}
	)
	return {
		"da_id": da_id,
		"week": label,
		"from": str(start),
		"to": str(end),
		"visits_completed": len(done),
		"visits_planned": planned,
		"farmers_reached": len({r.farmer_id for r in done if r.farmer_id}),
		"advisories_issued": sum(
			1 for r in done if r.visit_type == "Advisory" or (r.advice_given or "").strip()
		),
		"issues_raised": 0,
		"follow_ups": sum(1 for r in done if r.follow_up_task),
		"outcomes": [
			{
				"visit_id": r.name,
				"farmer_id": r.farmer_id,
				"kebele": r.kebele,
				"purpose": r.purpose or r.visit_type,
				"summary": r.outcome,
				"completed_at": r.completed_at.isoformat() if r.completed_at else None,
			}
			for r in done
		],
	}


def reschedule_visit(name: str, *, scheduled_at, reason: str | None = None, ctx=None) -> dict:
	ctx = ctx or require_roles(*WRITE_ROLES)
	doc = _load_in_scope(name, ctx, write=True)
	doc.flags.ignore_permissions = True
	new = doc.reschedule(scheduled_at, reason)
	return serialize(new)


def cancel_visit(name: str, *, reason: str | None = None, ctx=None) -> dict:
	ctx = ctx or require_roles(*WRITE_ROLES)
	doc = _load_in_scope(name, ctx, write=True)
	doc.flags.ignore_permissions = True
	doc.cancel_visit(reason)
	return serialize(doc)


def list_visits(
	ctx: ScopeContext | None = None,
	*,
	from_date=None,
	to_date=None,
	date=None,
	status: str | None = None,
	farmer_id: str | None = None,
	da_id: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int]:
	"""Visits the caller may see (DA-211 Phase 6: Kebele-scoped for DAs).

	DAs see all visits in their assigned Kebele (departmental data), not just own visits.
	Supervisors/Executives see their Woreda/Region scope.
	`status` may be a physical_status or a UI chip (Planned / Confirmed / Completed / Missed).
	"""
	ctx = ctx or require_roles(*READ_ROLES)
	filters: dict = {}
	if date:
		d = getdate(date)
		filters["scheduled_at"] = ("between", [f"{d} 00:00:00", f"{d} 23:59:59"])
	elif from_date and to_date:
		filters["scheduled_at"] = (
			"between",
			[f"{getdate(from_date)} 00:00:00", f"{getdate(to_date)} 23:59:59"],
		)
	if farmer_id:
		filters["farmer_id"] = farmer_id

	# DA-211 Phase 6: Kebele-based visit visibility for DAs
	if not ctx.unrestricted and ctx.has_any_role(C.ROLE_DA):
		# DAs see visits in their assigned Kebele (not just own visits)
		da_ids_list = list(ctx.da_ids) if ctx.da_ids else []
		if da_ids_list:
			# Get DA's assigned Kebeles
			from da_services.services.farmers import _get_da_assigned_kebeles

			assigned_kebeles = _get_da_assigned_kebeles(da_ids_list)
			if assigned_kebeles:
				filters["kebele"] = ("in", assigned_kebeles)
			else:
				# DA has no Kebele assignment - empty list
				return [], 0
		else:
			return [], 0
	elif da_id:
		# Explicit DA filter (for Supervisors/Executives filtering by DA)
		filters["da_id"] = da_id

	if status == "Completed":
		filters["physical_status"] = COMPLETE
	elif status == "Planned":
		filters["physical_status"] = ("in", list(OPEN_STATES))
	elif status:
		filters["physical_status"] = status

	limit = max(1, min(int(limit), 500))
	start = max(0, int(start))
	rows = frappe.get_list(
		"DA Visit",
		filters=filters,
		fields=[*FIELDS, "farmer_confirmation"],
		order_by="scheduled_at asc",
		limit_page_length=limit,
		limit_start=start,
	)
	total = len(frappe.get_list("DA Visit", filters=filters, pluck="name", limit_page_length=0))
	return [serialize(frappe._dict(r)) for r in rows], total


def dashboard_counts(da_id: str, from_date, to_date) -> dict:
	"""Farm visits tile: planned (everything meant to happen in the range) vs completed."""
	rng = ("between", [f"{getdate(from_date)} 00:00:00", f"{getdate(to_date)} 23:59:59"])
	return {
		"planned": frappe.db.count(
			"DA Visit", {"da_id": da_id, "scheduled_at": rng, "physical_status": ("in", list(PLANNED_STATES))}
		),
		"completed": frappe.db.count(
			"DA Visit", {"da_id": da_id, "scheduled_at": rng, "physical_status": COMPLETE}
		),
	}


def today_items(da_id: str, date=None) -> list[dict]:
	"""Visit rows for Today's Plan (merged with tasks in the dashboard service)."""
	d = getdate(date) if date else getdate()
	rows = frappe.get_all(
		"DA Visit",
		filters={
			"da_id": da_id,
			"scheduled_at": ("between", [f"{d} 00:00:00", f"{d} 23:59:59"]),
			"physical_status": ("in", [*OPEN_STATES, COMPLETE, "Missed"]),
		},
		fields=[
			"name",
			"farmer_id",
			"kebele",
			"visit_type",
			"purpose",
			"scheduled_at",
			"physical_status",
			"farmer_confirmation",
		],
		order_by="scheduled_at asc",
	)
	items = []
	for r in rows:
		d_ = _derived(r)
		items.append(
			{
				"kind": "visit",
				"id": r.name,
				"time": get_datetime(r.scheduled_at).strftime("%H:%M") if r.scheduled_at else None,
				"title": f"Farm visit: {r.farmer_id}" + (f", {r.kebele}" if r.kebele else ""),
				"subtitle": r.purpose or r.visit_type,
				"status": "Done"
				if d_["ui_status"] == "Completed"
				else ("Overdue" if d_["planner_status"] == "overdue" else "Upcoming"),
				"href": f"/visits/{r.name}",
			}
		)
	return items
