"""DA Visit use-cases behind the dashboard, planner and outcome form (data-model 5.3).

Visibility is enforced by Frappe User Permissions and the DA Visit hooks
(``da_visit_query_conditions`` / ``has_da_visit_permission``). These services apply
request filters and product rules only; they do not re-check Woreda/Kebele/role scope.
API handlers use ``require_roles`` as the coarse endpoint gate.
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
	MISSED,
	OPEN_STATES,
	PLANNED_STATES,
	RESCHEDULED,
)
from da_services.services import constants as C
from da_services.services import visit_specializations as spec_svc
from da_services.utils.permissions import ScopeContext, get_scope_context

WRITE_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_ADMIN)
READ_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)

# Open visits block another visit that covers the same specialization.
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
	elif status == MISSED:
		ui, planner = MISSED, "scheduled"
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
		creation = doc.get("creation")
		out["creation"] = (
			creation.isoformat() if creation is not None and hasattr(creation, "isoformat") else creation
		)
		out["created_by_user"] = doc.get("created_by_user")
		out["modified_by"] = doc.get("modified_by")
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
		out["specializations"] = [r.specialization for r in (doc.get("specializations") or [])]
	return out


def _check_specialization_conflict(farmer_id: str, new_specializations: list[str]) -> dict | None:
	"""Block scheduling when the farmer has an open visit covering the same specialization."""
	if not farmer_id or not new_specializations:
		return None

	rows = frappe.db.sql(
		"""
		SELECT
			v.name,
			v.visit_type,
			v.physical_status,
			s.specialization
		FROM `tabDA Visit` v
		LEFT JOIN `tabDA Visit Specialization` s
			ON s.parent = v.name AND s.parenttype = 'DA Visit'
		WHERE
			v.farmer_id = %(farmer_id)s
			AND v.physical_status IN %(blocking_statuses)s
		FOR UPDATE
		""",
		{"farmer_id": farmer_id, "blocking_statuses": BLOCKING_STATUSES},
		as_dict=True,
	)
	if not rows:
		return None

	by_visit: dict[str, dict] = {}
	for row in rows:
		bucket = by_visit.setdefault(
			row.name,
			{
				"name": row.name,
				"visit_type": row.visit_type,
				"physical_status": row.physical_status,
				"specs": set(),
			},
		)
		if row.specialization:
			bucket["specs"].add(row.specialization)

	wanted = set(new_specializations)
	for visit in by_visit.values():
		overlap = wanted & visit["specs"]
		if overlap:
			return {
				"blocked": True,
				"reason": (
					f"Farmer already has an active {visit['visit_type']} visit covering: "
					f"{', '.join(sorted(overlap))}"
				),
				"blocked_specializations": sorted(overlap),
				"conflicting_visit_id": visit["name"],
				"conflicting_visit_status": visit["physical_status"],
			}
	return None


def _resolve_da_for_write(da_id: str | None, ctx: ScopeContext) -> tuple[str, str | None]:
	"""(da_id, assigned_by). Bound DAs always act as their own DA-ID."""
	own = sorted(ctx.da_ids)
	if own and ctx.has_any_role(C.ROLE_DA) and not ctx.unrestricted:
		return own[0], None
	if not da_id:
		frappe.throw(_("da_id is required."), frappe.ValidationError)
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
	user: str | None = None,
	da_id: str | None = None,
	client_op_id: str | None = None,
	ctx: ScopeContext | None = None,
) -> dict:
	ctx = ctx or get_scope_context()
	if not farmer_id:
		frappe.throw(_("farmer_id is required."), frappe.ValidationError)
	if not scheduled_at:
		frappe.throw(_("scheduled_at is required."), frappe.ValidationError)
	user = (user or "").strip()
	if not user:
		frappe.throw(_("user is required."), frappe.ValidationError)
	da_id, assigned_by = _resolve_da_for_write(da_id, ctx)

	if client_op_id:
		existing = frappe.db.get_value("DA Visit", {"client_op_id": client_op_id}, "name")
		if existing:
			return serialize(frappe.get_doc("DA Visit", existing))

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
			"user": user,
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

	spec_svc.set_visit_specializations(doc, new_specializations)
	doc.insert(ignore_permissions=True)
	return serialize(doc)


def _load_visit(name: str, *, write: bool = False):
	"""Load a visit; visibility comes from ``has_da_visit_permission`` via check_permission."""
	doc = frappe.get_doc("DA Visit", name)
	doc.check_permission("write" if write else "read")
	return doc


def get_visit(name: str, ctx: ScopeContext | None = None) -> dict:
	return serialize(_load_visit(name, write=False))


def start_visit(
	name: str,
	*,
	arrived_at=None,
	gps: dict | None = None,
	farmer_present: bool = True,
	client_op_id: str | None = None,
	ctx=None,
) -> dict:
	doc = _load_visit(name, write=True)
	if client_op_id and doc.physical_status == IN_PROGRESS:
		return serialize(doc)
	doc.flags.ignore_permissions = True
	doc.start_visit(arrived_at=arrived_at, gps=gps, farmer_present=farmer_present)
	return serialize(doc)


def submit_outcome(name: str, *, ctx=None, client_op_id: str | None = None, **outcome) -> dict:
	doc = _load_visit(name, write=True)
	if client_op_id and doc.physical_status == COMPLETE and doc.outcome_client_op_id == client_op_id:
		return serialize(doc)
	doc.flags.ignore_permissions = True
	doc.submit_outcome(**outcome)
	if client_op_id:
		doc.db_set("outcome_client_op_id", client_op_id, update_modified=False)
	doc.reload()
	return serialize(doc)


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
	"""Refuse evidence that is too large, wrong type, or whose bytes do not match the extension."""
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
	"""Store one piece of geo-tagged evidence on a visit (duplicates by content hash)."""
	validate_evidence(content, filename)
	doc = _load_visit(name, write=True)
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
	"""DA-ID for a report: session binding when omitted, otherwise the named id."""
	own = sorted(ctx.da_ids)
	if not da_id:
		if not own:
			frappe.throw(_("da_id is required."), frappe.ValidationError)
		return own[0]
	return da_id


def _week_range(week: str | None) -> tuple[date, date, str]:
	"""Monday..Sunday of an ISO week ("2026-W42"); the current week when not given."""
	if week:
		m = re.fullmatch(r"(\d{4})-W(\d{1,2})", str(week).strip().upper())
		if not m:
			frappe.throw(_("week must look like 2026-W42."), frappe.ValidationError)
		try:
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
	"""Weekly activity report from DA Visit rows (one scoped get_list)."""
	ctx = ctx or get_scope_context()
	da_id = resolve_da_for_read(da_id, ctx)
	start, end, label = _week_range(week)
	start_dt = datetime.combine(start, time.min)
	end_dt = datetime.combine(end, time.max)
	rows = frappe.get_list(
		"DA Visit",
		filters={"da_id": da_id},
		or_filters=[
			["completed_at", "between", [start_dt, end_dt]],
			["scheduled_at", "between", [start_dt, end_dt]],
		],
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
			"scheduled_at",
			"physical_status",
		],
		order_by="completed_at desc",
		limit_page_length=0,
	)
	done = [
		r
		for r in rows
		if r.physical_status == COMPLETE
		and r.completed_at
		and start_dt <= get_datetime(r.completed_at) <= end_dt
	]
	planned = sum(
		1
		for r in rows
		if r.physical_status in PLANNED_STATES
		and r.scheduled_at
		and start_dt <= get_datetime(r.scheduled_at) <= end_dt
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
	doc = _load_visit(name, write=True)
	doc.flags.ignore_permissions = True
	return serialize(doc.reschedule(scheduled_at, reason))


def cancel_visit(name: str, *, reason: str | None = None, ctx=None) -> dict:
	doc = _load_visit(name, write=True)
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
	region: str | None = None,
	zone: str | None = None,
	woreda: str | None = None,
	kebele: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int]:
	"""Visits visible via permission hooks. Optional query args narrow the permitted set."""
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
	return rows, len(rows)


def dashboard_counts(da_id: str, from_date, to_date) -> dict:
	"""Farm visits tile: planned vs completed (one read, counted in Python)."""
	rng = ("between", [f"{getdate(from_date)} 00:00:00", f"{getdate(to_date)} 23:59:59"])
	rows = frappe.get_all(
		"DA Visit",
		filters={"da_id": da_id, "scheduled_at": rng},
		fields=["physical_status"],
		limit_page_length=0,
	)
	out = {"planned": 0, "completed": 0, "missed": 0, "rescheduled": 0, "in_progress": 0}
	for r in rows:
		status = r.physical_status
		if status in PLANNED_STATES:
			out["planned"] += 1
		if status == COMPLETE:
			out["completed"] += 1
		elif status == MISSED:
			out["missed"] += 1
		elif status == RESCHEDULED:
			out["rescheduled"] += 1
		elif status == IN_PROGRESS:
			out["in_progress"] += 1
	return out


def today_items(da_id: str, date=None) -> list[dict]:
	"""Visit rows for Today's Plan (merged with tasks in the dashboard service)."""
	d = getdate(date) if date else getdate()
	rows = frappe.get_all(
		"DA Visit",
		filters={
			"da_id": da_id,
			"scheduled_at": ("between", [f"{d} 00:00:00", f"{d} 23:59:59"]),
			"physical_status": ("in", [*OPEN_STATES, COMPLETE, MISSED]),
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
