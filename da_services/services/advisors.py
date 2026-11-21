"""Advisor directory: MoA subject-matter experts DAs reference for specialist advice.

A reference list, not an HR record. The Administrator maintains it; every DA Services
role that can read DA data can read it, and every reader (Development Agents included)
sees the whole directory.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

from da_services.da_services.doctype.da_advisor.da_advisor import EXPERTISE_AREAS, STATUSES
from da_services.services import constants as C

DOCTYPE = "DA Advisor"
MAX_LIMIT = 500

FIELDS = (
	"name",
	"full_name",
	"expertise_area",
	"moa_unit",
	"region",
	"woreda",
	"mobile_number",
	"email",
	"status",
	"availability",
	"modified",
)


def int_param(name: str, value, default: int) -> int:
	"""Query-string integer: '' / None -> default; anything non-numeric is a 400."""
	if value is None or value == "":
		return default
	text = str(value).strip()
	if not text.lstrip("-").isdigit():
		frappe.throw(_("{0} must be an integer.").format(name), frappe.ValidationError)
	return cint(text)


def serialize(row) -> dict:
	"""The advisor as the portal shows it (keys match the Add Advisor form)."""
	modified = row.get("modified")
	return {
		"id": row.get("name"),
		"name": row.get("full_name"),
		"expertise": row.get("expertise_area"),
		"unit": row.get("moa_unit"),
		"region": row.get("region"),
		"woreda": row.get("woreda"),
		"phone": row.get("mobile_number"),
		"email": row.get("email"),
		"status": row.get("status"),
		"availability": row.get("availability") or "",
		# A just-inserted document carries the timestamp as text, with microseconds.
		"updated_at": modified.strftime("%Y-%m-%d %H:%M:%S")
		if hasattr(modified, "strftime")
		else str(modified or "")[:19] or None,
	}


def create_advisor(
	*,
	name=None,
	expertise=None,
	unit=None,
	region=None,
	woreda=None,
	phone=None,
	email=None,
	status=None,
	availability=None,
) -> dict:
	"""Add an advisor. Field rules (name length, phone, email) are on the DocType."""
	missing = [
		label
		for label, value in (
			("name", name),
			("expertise", expertise),
			("unit", unit),
			("region", region),
			("woreda", woreda),
			("phone", phone),
			("email", email),
		)
		if not str(value or "").strip()
	]
	if missing:
		frappe.throw(_("Missing required fields: {0}.").format(", ".join(missing)), frappe.ValidationError)

	status = (status or "Active").strip()
	availability = (availability or "").strip()
	if not availability:
		# Same default the form shows when the Admin leaves it blank.
		availability = "Mon-Fri, 08:30-17:00" if status == "Active" else "Not taking requests"

	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"full_name": name,
			"expertise_area": expertise,
			"moa_unit": unit,
			"region": region,
			"woreda": woreda,
			"mobile_number": phone,
			"email": email,
			"status": status,
			"availability": availability,
		}
	)
	# The permission check is done by the API layer (require_permission); the insert itself is a system write.
	doc.insert(ignore_permissions=True)
	return serialize(doc.as_dict())


def list_advisors(
	*,
	q=None,
	expertise=None,
	region=None,
	woreda=None,
	status=None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int]:
	"""Advisors, newest change first. Filters are exact; ``q`` searches name, id, unit and location."""
	if limit < 1 or limit > MAX_LIMIT:
		frappe.throw(_("Limit must be between 1 and {0}.").format(MAX_LIMIT), frappe.ValidationError)
	if start < 0:
		frappe.throw(_("Start must be >= 0."), frappe.ValidationError)
	if expertise and expertise not in EXPERTISE_AREAS:
		frappe.throw(
			_("Expertise area must be one of {0}.").format(", ".join(EXPERTISE_AREAS)), frappe.ValidationError
		)
	if status and status not in STATUSES:
		frappe.throw(_("Status must be one of {0}.").format(", ".join(STATUSES)), frappe.ValidationError)

	filters = {}
	if expertise:
		filters["expertise_area"] = expertise
	if region:
		filters["region"] = region
	if woreda:
		filters["woreda"] = woreda
	if status:
		filters["status"] = status

	or_filters = None
	text = (q or "").strip()
	if text:
		like = f"%{text}%"
		or_filters = [
			[DOCTYPE, field, "like", like]
			for field in ("full_name", "name", "expertise_area", "moa_unit", "region", "woreda", "email")
		]

	rows = frappe.get_all(
		DOCTYPE,
		filters=filters,
		or_filters=or_filters,
		fields=list(FIELDS),
		order_by="modified desc",
		offset=start,
		limit=limit,
	)
	total = len(frappe.get_all(DOCTYPE, filters=filters, or_filters=or_filters, pluck="name"))
	return [serialize(r) for r in rows], total


def get_advisor(advisor_id: str) -> dict:
	"""One advisor."""
	row = frappe.db.get_value(DOCTYPE, advisor_id, list(FIELDS), as_dict=True)
	if not row:
		frappe.throw(_("Advisor {0} not found.").format(advisor_id), frappe.DoesNotExistError)
	return serialize(row)
