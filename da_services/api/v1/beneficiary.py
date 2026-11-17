"""v1: Beneficiary Segment list and Farmer Beneficiary get / create.

GET /api/v1/da-services/beneficiary-segments?search=&limit=100&start=0&get_count=

Returns the active (``is_active = 1``) Beneficiary Segment rows, newest first. ``search`` matches title or short
description. ``get_count=true`` adds each segment's Farmer Beneficiary count.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.api.v1.common import request_fields, truthy

DOCTYPE = "Beneficiary Segment"
FIELDS = ["name", "title", "short_desc", "long_desc"]
DEFAULT_LIMIT = 100
MAX_LIMIT = 500


def _int_param(name: str, value, default: int) -> int:
	"""Query-string integer: '' / None -> default; anything non-numeric is a 400."""
	if value is None or value == "":
		return default
	text = str(value).strip()
	if not text.lstrip("-").isdigit():
		frappe.throw(_("{0} must be an integer.").format(name), frappe.ValidationError)
	return cint(text)


@route("/beneficiary-segments", methods=("GET",), summary="List beneficiary segments")
@frappe.whitelist()
@handle_api_errors
def list_beneficiary_segments(search=None, limit=None, start=None, get_count=None):
	"""Query: optional ``search`` (search in title / short description), ``limit`` (1-500,
	default 100), ``start`` (offset, default 0) and ``get_count`` (true / 1 adds
	``farmer_beneficiary_count`` to each row: the Farmer Beneficiaries the caller may read,
	or null when the caller has no read permission on Farmer Beneficiary).

	Returns:
	    {
	        "success": true,
	        "data": [{"name", "title", "short_desc", "long_desc", "farmer_beneficiary_count"?}],
	        "meta": {"total", "limit", "start", "search", "get_count"}
	    }
	"""

	search = (search or frappe.form_dict.get("search") or "").strip()[:80]
	get_count = truthy(get_count if get_count is not None else frappe.form_dict.get("get_count"))
	limit = _int_param("limit", limit if limit is not None else frappe.form_dict.get("limit"), DEFAULT_LIMIT)
	start = _int_param("start", start if start is not None else frappe.form_dict.get("start"), 0)
	if limit < 1 or limit > MAX_LIMIT:
		frappe.throw(_("Limit must be between 1 and {0}.").format(MAX_LIMIT), frappe.ValidationError)
	if start < 0:
		frappe.throw(_("Start must be >= 0."), frappe.ValidationError)

	or_filters = None
	if search:
		like = f"%{search}%"
		or_filters = [["title", "like", like], ["short_desc", "like", like]]

	filters = {"is_active": 1}
	rows = frappe.get_all(
		DOCTYPE,
		fields=FIELDS,
		filters=filters,
		or_filters=or_filters,
		order_by="creation desc",
		offset=start,
		limit=limit,
	)
	total = (
		len(frappe.get_all(DOCTYPE, filters=filters, or_filters=or_filters, pluck="name"))
		if search
		else frappe.db.count(DOCTYPE, filters)
	)

	data = [dict(r) for r in rows]
	if get_count:
		# A role without read on Farmer Beneficiary still gets the segments, with null counts.
		can_count = frappe.has_permission(FB_DOCTYPE, "read")
		counts = _farmer_beneficiary_counts([r["name"] for r in data]) if can_count else {}
		for r in data:
			r["farmer_beneficiary_count"] = counts.get(r["name"], 0) if can_count else None

	return success_response(
		data=data,
		meta={"total": total, "limit": limit, "start": start, "search": search, "get_count": get_count},
	)


def _farmer_beneficiary_counts(segments: list[str]) -> dict[str, int]:
	"""Farmer Beneficiary rows per segment that the caller may read.

	frappe.get_list applies role permissions and User Permissions (Region / Zone / Woreda /
	Kebele), so each user counts only the beneficiaries in their own area.
	"""
	if not segments:
		return {}
	rows = frappe.get_list(
		FB_DOCTYPE,
		fields=["beneficiary_segment", {"COUNT": "*", "as": "count"}],
		filters={"beneficiary_segment": ["in", segments]},
		group_by="beneficiary_segment",
	)
	return {r.beneficiary_segment: r.count for r in rows}


# ---------------------------------------------------------------------------
# Farmer Beneficiary: which farmer belongs to which Beneficiary Segment
#
# GET  /api/v1/da-services/farmer-beneficiaries?farmer_id=&beneficiary_segment=&region=&zone=&woreda=&kebele=&limit=&start=
# POST /api/v1/da-services/farmer-beneficiaries
#      {"farmer_ids": [...], "region", "zone", "woreda", "kebele", "beneficiary_segment"}
#
# Frappe's DocType permissions decide who may read and create.
# ---------------------------------------------------------------------------

FB_DOCTYPE = "Farmer Beneficiary"
FB_FIELDS = [
	"name",
	"user",
	"farmer_id",
	"region",
	"zone",
	"woreda",
	"kebele",
	"beneficiary_segment",
	"creation",
	"modified",
]
FB_FILTERS = ("farmer_id", "beneficiary_segment", "region", "zone", "woreda", "kebele", "user")
# Shared by every row of a bulk create.
FB_SHARED = ("region", "zone", "woreda", "kebele", "beneficiary_segment")
MAX_BULK = 500


@route("/farmer-beneficiaries", methods=("GET",), summary="List farmer beneficiaries")
@frappe.whitelist()
@handle_api_errors
def list_farmer_beneficiaries(**kwargs):
	"""Query: optional exact-match filters ``farmer_id``, ``beneficiary_segment``, ``region``,
	``zone``, ``woreda``, ``kebele``, ``user``; ``limit`` (1-500, default 100) and ``start``.

	Returns:
	    {
	        "success": true,
	        "data": [{"name", "user", "farmer_id", "region", "zone", "woreda", "kebele",
	                  "beneficiary_segment", "creation", "modified"}],
	        "meta": {"total", "limit", "start", "filters"}
	    }
	"""
	q = request_fields(kwargs, *FB_FILTERS, "limit", "start")
	limit = _int_param("limit", q.get("limit"), DEFAULT_LIMIT)
	start = _int_param("start", q.get("start"), 0)
	if limit < 1 or limit > MAX_LIMIT:
		frappe.throw(_("Limit must be between 1 and {0}.").format(MAX_LIMIT), frappe.ValidationError)
	if start < 0:
		frappe.throw(_("Start must be >= 0."), frappe.ValidationError)

	filters = {k: str(q[k]).strip() for k in FB_FILTERS if q.get(k) not in (None, "")}

	rows = frappe.get_list(
		FB_DOCTYPE,
		fields=FB_FIELDS,
		filters=filters,
		order_by="creation desc",
		offset=start,
		limit=limit,
	)
	total = len(frappe.get_list(FB_DOCTYPE, filters=filters, pluck="name"))

	return success_response(
		data=[dict(r) for r in rows],
		meta={"total": total, "limit": limit, "start": start, "filters": filters},
	)


@route("/farmer-beneficiaries", methods=("POST",), status=201, summary="Create farmer beneficiaries in bulk")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def create_farmer_beneficiary(**kwargs):
	"""Body: {"farmer_ids": ["FR-1", "FR-2", ...], "region", "zone", "woreda", "kebele",
	"beneficiary_segment"}, all required. One Farmer Beneficiary is created per farmer id, all
	with the same segment and location; ``user`` is the caller.

	- ``farmer_ids`` is a list (or a JSON list string) of 1-500 ids; blanks and repeats are dropped.
	- The segment must be active.
	- A farmer already in the segment is skipped and listed in ``skipped``.
	- Any other failure (validation, permission, bad link) creates nothing.

	Returns:
	    {
	        "success": true,
	        "data": {"created": [{"name", "user", "farmer_id", ...}], "skipped": ["FR-1", ...]},
	        "meta": {"requested", "created", "skipped"}
	    }
	"""
	b = request_fields(kwargs, "farmer_ids", *FB_SHARED, json_keys=("farmer_ids",))
	values = {k: str(b.get(k) or "").strip() for k in FB_SHARED}
	farmer_ids = _farmer_ids(b.get("farmer_ids"))
	missing = (["farmer_ids"] if not farmer_ids else []) + [k for k, v in values.items() if not v]
	if missing:
		frappe.throw(_("Missing required fields: {0}.").format(", ".join(missing)), frappe.ValidationError)
	if len(farmer_ids) > MAX_BULK:
		frappe.throw(
			_("At most {0} farmer_ids can be sent at once.").format(MAX_BULK), frappe.ValidationError
		)

	segment = values["beneficiary_segment"]
	if not frappe.db.get_value(DOCTYPE, {"name": segment, "is_active": 1}):
		frappe.throw(
			_("Beneficiary Segment {0} does not exist or is not active.").format(segment),
			frappe.ValidationError,
		)

	existing = set(
		frappe.get_all(
			FB_DOCTYPE,
			filters={"beneficiary_segment": segment, "farmer_id": ["in", farmer_ids]},
			pluck="farmer_id",
		)
	)
	created = []
	for farmer_id in farmer_ids:
		if farmer_id in existing:
			continue
		doc = frappe.get_doc(
			{"doctype": FB_DOCTYPE, "user": frappe.session.user, "farmer_id": farmer_id, **values}
		)
		doc.insert()  # DocType permissions and link validation apply
		created.append({f: doc.get(f) for f in FB_FIELDS})

	skipped = [f for f in farmer_ids if f in existing]
	return success_response(
		data={"created": created, "skipped": skipped},
		message=_("{0} farmer beneficiaries created, {1} already in the segment.").format(
			len(created), len(skipped)
		),
		meta={"requested": len(farmer_ids), "created": len(created), "skipped": len(skipped)},
	)


def _farmer_ids(raw) -> list[str]:
	"""``farmer_ids`` as a list of unique, non-blank ids in the order sent."""
	if raw in (None, ""):
		return []
	if not isinstance(raw, list | tuple):
		frappe.throw(_("farmer_ids must be a list of farmer ids."), frappe.ValidationError)
	ids = []
	for item in raw:
		if not isinstance(item, str | int):
			frappe.throw(_("farmer_ids must be a list of farmer ids."), frappe.ValidationError)
		farmer_id = str(item).strip()
		if farmer_id and farmer_id not in ids:
			ids.append(farmer_id)
	return ids
