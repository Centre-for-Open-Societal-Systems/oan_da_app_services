"""DA Gathering use-cases: plan, list, get, cancel.

Visibility and create/write access are Frappe DocType Role permissions + User
Permissions only. Service holds product rules Frappe cannot express (SMS log
rows, invitee snapshot validation) plus thin response shaping.

Invitee kebele membership is not re-checked here — FE picks from the scoped
farmer list; ``farmer_id`` / ``farmer_name`` / ``phone`` are required from FE
(no DA Farmer Reference lookup).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import format_datetime, get_datetime, getdate, now_datetime

from da_services.da_services.doctype.da_gathering.da_gathering import PLANNED

FIELDS = [
	"name",
	"user",
	"da_id",
	"title",
	"purpose",
	"scheduled_at",
	"status",
	"location_name",
	"address",
	"landmark",
	"location_lat",
	"location_lng",
	"notify_farmers_sms",
	"assigned_by",
	"region",
	"zone",
	"woreda",
	"kebele",
	"cancel_reason",
	"cancelled_at",
	"client_op_id",
	"modified",
]
DATETIME_FIELDS = ("scheduled_at", "cancelled_at", "modified")


def _nested_location(row) -> dict:
	"""API location object from flat DocType / get_list fields."""
	return {
		"name": row.get("location_name"),
		"address": row.get("address"),
		"landmark": row.get("landmark"),
		"lat": row.get("location_lat"),
		"lng": row.get("location_lng"),
		"region": row.get("region"),
		"zone": row.get("zone"),
		"woreda": row.get("woreda"),
		"kebele": row.get("kebele"),
	}


def serialize(gathering: Document) -> dict:
	"""API response shape (nested location + children). Not a Frappe concern."""
	payload = {field_name: gathering.get(field_name) for field_name in FIELDS}
	for field_name in DATETIME_FIELDS:
		value = payload.get(field_name)
		if value is not None and hasattr(value, "isoformat"):
			payload[field_name] = value.isoformat()
	creation = gathering.get("creation")
	payload["creation"] = (
		creation.isoformat() if creation is not None and hasattr(creation, "isoformat") else creation
	)
	payload["created_by_user"] = gathering.get("created_by_user")
	payload["modified_by"] = gathering.get("modified_by")
	payload["location"] = _nested_location(gathering)
	payload["attendees"] = [
		{
			"farmer_id": attendee.farmer_id,
			"farmer_name": attendee.farmer_name,
			"phone": attendee.phone,
			"sms_status": attendee.sms_status,
		}
		for attendee in (gathering.get("attendees") or [])
	]
	payload["sms_logs"] = [
		{
			"farmer_id": sms_log.farmer_id,
			"phone": sms_log.phone,
			"status": sms_log.status,
			"message_body": sms_log.message_body,
			"provider_ref": sms_log.provider_ref,
			"error": sms_log.error,
			"queued_at": sms_log.queued_at.isoformat() if sms_log.queued_at else None,
			"sent_at": sms_log.sent_at.isoformat() if sms_log.sent_at else None,
		}
		for sms_log in (gathering.get("sms_logs") or [])
	]
	payload["farmer_ids"] = [attendee["farmer_id"] for attendee in payload["attendees"]]
	return payload


def deliver_gathering_sms(gathering_name: str) -> None:
	"""After-commit SMS job. Gateway not wired; Queued/Skipped rows stay as logged."""
	if not gathering_name or not frappe.db.exists("DA Gathering", gathering_name):
		return


def _normalize_farmers(farmers) -> list[dict]:
	"""Require FE ``farmers[]`` with farmer_id, farmer_name, phone (no cache lookup)."""
	if not isinstance(farmers, (list, tuple)):
		frappe.throw(_("farmers must be a list."), frappe.ValidationError)
	normalized: list[dict] = []
	seen_farmer_ids: set[str] = set()
	for index, raw in enumerate(farmers):
		if not isinstance(raw, dict):
			frappe.throw(
				_("farmers[{0}] must be an object.").format(index),
				frappe.ValidationError,
			)
		farmer_id = (raw.get("farmer_id") or "").strip()
		farmer_name = (raw.get("farmer_name") or "").strip()
		phone = (raw.get("phone") or "").strip()
		if not farmer_id:
			frappe.throw(_("farmers[{0}].farmer_id is required.").format(index), frappe.ValidationError)
		if not farmer_name:
			frappe.throw(_("farmers[{0}].farmer_name is required.").format(index), frappe.ValidationError)
		if not phone:
			frappe.throw(_("farmers[{0}].phone is required.").format(index), frappe.ValidationError)
		if farmer_id in seen_farmer_ids:
			frappe.throw(_("Duplicate farmer_id {0}.").format(farmer_id), frappe.ValidationError)
		seen_farmer_ids.add(farmer_id)
		normalized.append({"farmer_id": farmer_id, "farmer_name": farmer_name, "phone": phone})
	return normalized


def plan_gathering(
	*,
	title: str,
	scheduled_at,
	farmers,
	user: str,
	location: dict | None = None,
	purpose: str | None = None,
	notify_farmers_sms: bool = True,
	da_id: str | None = None,
	client_op_id: str | None = None,
) -> dict:
	"""Plan a gathering.

	Location client inputs: venue (``location.name``), landmark, lat, lng only.
	Address + region/zone/woreda/kebele are stamped from the DA's Effective Kebele Assignment.
	``farmers`` must include ``farmer_id``, ``farmer_name``, and ``phone`` from the FE.
	``da_id`` body field is legacy/envelope only; row uses ``User.da_registry_id``.
	"""
	user = (user or "").strip()
	if not user:
		frappe.throw(_("user is required."), frappe.ValidationError)
	title = (title or "").strip()
	location = location or {}
	invitees = _normalize_farmers(farmers)

	if client_op_id:
		existing_name = frappe.db.get_value("DA Gathering", {"client_op_id": client_op_id}, "name")
		if existing_name:
			return serialize(frappe.get_doc("DA Gathering", existing_name))

	da_registry_id = frappe.db.get_value("User", user, "da_registry_id")
	if not da_registry_id:
		frappe.throw(_("user has no DA Registry ID."), frappe.ValidationError)

	session_user = frappe.session.user
	gathering = frappe.get_doc(
		{
			"doctype": "DA Gathering",
			"user": user,
			"da_id": da_registry_id,
			"title": title,
			"purpose": (purpose or "").strip() or None,
			"scheduled_at": get_datetime(scheduled_at) if scheduled_at else None,
			"status": PLANNED,
			"location_name": (location.get("name") or location.get("location_name") or "").strip(),
			"landmark": (location.get("landmark") or "").strip() or None,
			"location_lat": location.get("lat"),
			"location_lng": location.get("lng"),
			"notify_farmers_sms": 1 if notify_farmers_sms else 0,
			"assigned_by": session_user if session_user and session_user not in (user, "Guest") else None,
			"client_op_id": client_op_id,
		}
	)
	# Needed before insert so SMS text includes composed address; before_validate stamps again.
	gathering.stamp_scope_and_address()

	for invitee in invitees:
		gathering.append(
			"attendees",
			{
				"farmer_id": invitee["farmer_id"],
				"farmer_name": invitee["farmer_name"],
				"phone": invitee["phone"],
				"sms_status": "Pending",
			},
		)

	if notify_farmers_sms:
		scheduled_datetime = get_datetime(gathering.scheduled_at) if gathering.scheduled_at else None
		schedule_date = format_datetime(scheduled_datetime, "yyyy-MM-dd") if scheduled_datetime else ""
		schedule_time = format_datetime(scheduled_datetime, "HH:mm") if scheduled_datetime else ""
		sms_message = (
			f"DA Gathering: {gathering.title} on {schedule_date} at {schedule_time}. "
			f"Place: {gathering.address}."
		)
		if (gathering.landmark or "").strip():
			sms_message += f" Landmark: {gathering.landmark.strip()}."
		sms_message += " — Your DA"
		queued_at = now_datetime()
		for attendee in gathering.attendees:
			attendee.sms_status = "Queued"
			gathering.append(
				"sms_logs",
				{
					"farmer_id": attendee.farmer_id,
					"phone": attendee.phone,
					"status": "Queued",
					"message_body": sms_message,
					"queued_at": queued_at,
				},
			)

	gathering.insert()

	if notify_farmers_sms:
		frappe.enqueue(
			"da_services.services.gatherings.deliver_gathering_sms",
			gathering_name=gathering.name,
			queue="short",
			enqueue_after_commit=True,
		)

	return serialize(gathering)


def get_gathering(name: str) -> dict:
	return serialize(frappe.get_doc("DA Gathering", name))


def cancel_gathering(name: str, *, reason: str | None = None) -> dict:
	gathering = frappe.get_doc("DA Gathering", name)
	gathering.cancel_gathering(reason)
	return serialize(gathering)


def list_gatherings(
	*,
	from_date=None,
	to_date=None,
	date=None,
	status: str | None = None,
	da_id: str | None = None,
	region: str | None = None,
	zone: str | None = None,
	woreda: str | None = None,
	kebele: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int]:
	"""Gatherings visible via Role DocPerm + User Permissions. Optional filters narrow that set."""
	filters: dict = {}
	if date:
		filter_date = getdate(date)
		filters["scheduled_at"] = ("between", [f"{filter_date} 00:00:00", f"{filter_date} 23:59:59"])
	elif from_date and to_date:
		filters["scheduled_at"] = (
			"between",
			[f"{getdate(from_date)} 00:00:00", f"{getdate(to_date)} 23:59:59"],
		)
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
	if status:
		filters["status"] = status

	limit = max(1, min(int(limit), 500))
	start = max(0, int(start))
	rows = frappe.get_list(
		"DA Gathering",
		filters=filters,
		fields=FIELDS,
		order_by="scheduled_at asc",
		limit=limit,
		limit_start=start,
	)
	for row in rows:
		for field_name in DATETIME_FIELDS:
			value = row.get(field_name)
			if value is not None and hasattr(value, "isoformat"):
				row[field_name] = value.isoformat()
		row["location"] = _nested_location(row)
	return rows, len(rows)
