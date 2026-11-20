"""DA Visit use-cases behind the dashboard, planner and outcome form (data-model 5.3).

Visibility and create/write access are Frappe DocType Role permissions + User
Permissions only (no custom Visit permission hooks). These services apply request
filters and product rules only.
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
from da_services.services import visit_specializations as spec_svc

# Open visits block another visit that covers the same specialization.
BLOCKING_STATUSES = list(OPEN_STATES)

# Max farmers in one POST /visits list payload (batch plan).
MAX_BATCH_VISITS = 5

FIELDS = [
	"name",
	"da_id",
	"farmer_id",
	"farmer_name",
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

	Visit = frappe.qb.DocType("DA Visit")
	Spec = frappe.qb.DocType("DA Visit Specialization")
	open_for_farmer = (Visit.farmer_id == farmer_id) & (
		Visit.physical_status.isin(BLOCKING_STATUSES)
	)

	# Lock parent rows alone: Postgres rejects FOR UPDATE on the nullable side of a LEFT JOIN.
	frappe.qb.from_(Visit).select(Visit.name).where(open_for_farmer).for_update().run()

	rows = (
		frappe.qb.from_(Visit)
		.left_join(Spec)
		.on((Spec.parent == Visit.name) & (Spec.parenttype == "DA Visit"))
		.select(Visit.name, Visit.visit_type, Visit.physical_status, Spec.specialization)
		.where(open_for_farmer)
		.run(as_dict=True)
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


def plan_visit(
	*,
	farmer_id: str,
	farmer_name: str,
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
) -> dict:
	"""Plan one visit. ``user`` is required from the FE; ``da_id`` is ``fetch_from`` that User.

	No role / scope / permission helpers here — Frappe DocType Role permissions + User Permissions own access.
	``da_id`` on the request is ignored for the row (fetch_from); it is only accepted for
	backward-compatible callers and unused. Specialization uses ``User.da_registry_id``.
	``farmer_name`` is an FE snapshot (required DocType field); no registry lookup.
	"""
	if not farmer_id:
		frappe.throw(_("farmer_id is required."), frappe.ValidationError)
	farmer_name = (farmer_name or "").strip()
	if not farmer_name:
		frappe.throw(_("farmer_name is required."), frappe.ValidationError)
	if not scheduled_at:
		frappe.throw(_("scheduled_at is required."), frappe.ValidationError)
	user = (user or "").strip()
	if not user:
		frappe.throw(_("user is required."), frappe.ValidationError)
	# ``da_id`` kept for API/batch envelope compatibility; row uses User.da_registry_id.

	if client_op_id:
		existing = frappe.db.get_value("DA Visit", {"client_op_id": client_op_id}, "name")
		if existing:
			return serialize(frappe.get_doc("DA Visit", existing))

	# Product-rule data only. Row ``da_id`` is filled by DocType fetch_from.
	registry_da = frappe.db.get_value("User", user, "da_registry_id")
	if not registry_da:
		frappe.throw(_("user has no DA Registry ID."), frappe.ValidationError)

	new_specializations = spec_svc.derive_visit_specializations(visit_type or "Crop Survey", registry_da)
	conflict = _check_specialization_conflict(farmer_id, new_specializations)
	if conflict:
		frappe.throw(
			_(conflict["reason"]),
			frappe.ValidationError,
			title=_("Visit Scheduling Conflict"),
		)

	location = location or {}
	session = frappe.session.user
	doc = frappe.get_doc(
		{
			"doctype": "DA Visit",
			"user": user,
			# Same value as fetch_from; set so DocType create permission sees da_id.
			"da_id": registry_da,
			"farmer_id": farmer_id,
			"farmer_name": farmer_name,
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
			"assigned_by": session if session and session not in (user, "Guest") else None,
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
	doc.insert()
	return serialize(doc)


def plan_visits_batch(
	*,
	visits: list,
	user: str | None = None,
	da_id: str | None = None,
) -> dict:
	"""Plan up to MAX_BATCH_VISITS visits sequentially in one request.

	Partial success: each item is independent. Failures echo the received item
	plus the exact error so the client can correct and resubmit.
	"""
	if not visits:
		frappe.throw(_("visits must not be empty."), frappe.ValidationError)
	if len(visits) > MAX_BATCH_VISITS:
		frappe.throw(
			_("At most {0} visits can be planned at once.").format(MAX_BATCH_VISITS),
			frappe.ValidationError,
		)
	if any(not isinstance(item, dict) for item in visits):
		frappe.throw(_("visits items must be objects."), frappe.ValidationError)

	seen_farmers: set[str] = set()
	for item in visits:
		fid = (item.get("farmer_id") or "").strip()
		if not fid:
			continue
		if fid in seen_farmers:
			frappe.throw(_("Duplicate farmer_id in visits."), frappe.ValidationError)
		seen_farmers.add(fid)

	envelope_user = (user or "").strip() or None
	created: list[dict] = []
	failed: list[dict] = []
	for item in visits:
		request_echo = dict(item)
		try:
			scheduled_at = item.get("scheduled_at")
			if scheduled_at:
				scheduled_at = get_datetime(scheduled_at)
			elif item.get("date"):
				scheduled_at = get_datetime(f"{item['date']} {item.get('time') or '09:00'}")
			else:
				scheduled_at = None

			created.append(
				plan_visit(
					farmer_id=(item.get("farmer_id") or "").strip(),
					farmer_name=(item.get("farmer_name") or "").strip(),
					scheduled_at=scheduled_at,
					visit_type=item.get("visit_type") or "Crop Survey",
					purpose=item.get("purpose"),
					duration_min=int(item.get("duration_min") or 45),
					priority=item.get("priority") or "Medium",
					priority_reason=item.get("priority_reason"),
					plot_ref=item.get("plot_ref"),
					location=item.get("location") or None,
					notify_farmer_sms=bool(item.get("notify_farmer_sms")),
					advisory_qa=item.get("advisory_qa") or None,
					user=(item.get("user") or envelope_user or "").strip(),
					da_id=item.get("da_id") or da_id,
					client_op_id=item.get("client_op_id") or None,
				)
			)
		except (frappe.ValidationError, frappe.PermissionError) as exc:
			frappe.clear_messages()
			message = str(exc)
			code = "conflict" if "already has an active" in message else "validation"
			failed.append({"status": "failed", "error": message, "code": code, "request": request_echo})
		except Exception:
			frappe.clear_messages()
			frappe.log_error(title="plan_visits_batch item failed", message=frappe.get_traceback())
			failed.append(
				{
					"status": "failed",
					"error": _("Could not plan this visit."),
					"code": "error",
					"request": request_echo,
				}
			)

	return {
		"created": created,
		"failed": failed,
		"summary": {"total": len(visits), "created": len(created), "failed": len(failed)},
	}


def get_visit(name: str) -> dict:
	return serialize(frappe.get_doc("DA Visit", name))


def start_visit(
	name: str,
	*,
	arrived_at=None,
	gps: dict | None = None,
	farmer_present: bool = True,
	client_op_id: str | None = None,
) -> dict:
	doc = frappe.get_doc("DA Visit", name)
	if client_op_id and doc.physical_status == IN_PROGRESS:
		return serialize(doc)
	doc.start_visit(arrived_at=arrived_at, gps=gps, farmer_present=farmer_present)
	return serialize(doc)


def submit_outcome(name: str, *, client_op_id: str | None = None, **outcome) -> dict:
	doc = frappe.get_doc("DA Visit", name)
	if client_op_id and doc.physical_status == COMPLETE and doc.outcome_client_op_id == client_op_id:
		return serialize(doc)
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
) -> dict:
	"""Store one piece of geo-tagged evidence on a visit (duplicates by content hash)."""
	validate_evidence(content, filename)
	doc = frappe.get_doc("DA Visit", name)
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
	doc.save()
	return _attachment_dict(row)


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


def activity_report(*, week: str | None = None, da_id: str | None = None) -> dict:
	"""Weekly activity report from DA Visit rows (one scoped get_list)."""
	da_id = (da_id or "").strip() or frappe.db.get_value("User", frappe.session.user, "da_registry_id")
	if not da_id:
		frappe.throw(_("da_id is required."), frappe.ValidationError)
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
			"farmer_name",
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
				"farmer_name": r.farmer_name,
				"kebele": r.kebele,
				"purpose": r.purpose or r.visit_type,
				"summary": r.outcome,
				"completed_at": r.completed_at.isoformat() if r.completed_at else None,
			}
			for r in done
		],
	}


def reschedule_visit(name: str, *, scheduled_at, reason: str | None = None) -> dict:
	doc = frappe.get_doc("DA Visit", name)
	return serialize(doc.reschedule(scheduled_at, reason))


def cancel_visit(name: str, *, reason: str | None = None) -> dict:
	doc = frappe.get_doc("DA Visit", name)
	doc.cancel_visit(reason)
	return serialize(doc)


def list_visits(
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
	"""Visits visible via Role DocPerm + User Permissions. Optional query args narrow that set."""
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


def list_farmer_advisory(farmer_id: str, *, limit: int = 50, start: int = 0) -> list[dict]:
	"""Every Advisory question and answer of a farmer: one row per question, newest first.

	Each row is ``question``, ``answer``, ``creation`` and ``modified`` of the Q&A row. All of
	the farmer's Advisory visits are read, whoever planned them: no DocPerm, User Permission
	or role filter. Visits with no questions give no row.
	"""
	return frappe.get_all(
		"DA Visit",
		filters=[
			["DA Visit", "farmer_id", "=", farmer_id],
			["DA Visit", "visit_type", "=", "Advisory"],
			["DA Visit Advisory QA", "question", "is", "set"],
		],
		fields=[
			"advisory_qa.question",
			"advisory_qa.answer",
			"advisory_qa.creation",
			"advisory_qa.modified",
		],
		order_by="`tabDA Visit Advisory QA`.creation desc, `tabDA Visit Advisory QA`.idx asc",
		limit_page_length=max(1, min(int(limit), 500)),
		limit_start=max(0, int(start)),
	)


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
			"farmer_name",
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
		label = r.farmer_name or r.farmer_id
		items.append(
			{
				"kind": "visit",
				"id": r.name,
				"time": get_datetime(r.scheduled_at).strftime("%H:%M") if r.scheduled_at else None,
				"title": f"Farm visit: {label}" + (f", {r.kebele}" if r.kebele else ""),
				"subtitle": r.purpose or r.visit_type,
				"status": "Done"
				if d_["ui_status"] == "Completed"
				else ("Overdue" if d_["planner_status"] == "overdue" else "Upcoming"),
				"href": f"/visits/{r.name}",
			}
		)
	return items
