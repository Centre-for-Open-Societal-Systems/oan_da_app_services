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
#      {"farmer_id", "region", "zone", "woreda", "kebele", "beneficiary_segment"}
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
FB_REQUIRED = ("farmer_id", "region", "zone", "woreda", "kebele", "beneficiary_segment")


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


@route("/farmer-beneficiaries", methods=("POST",), status=201, summary="Create a farmer beneficiary")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def create_farmer_beneficiary(**kwargs):
	"""Body: {"farmer_id", "region", "zone", "woreda", "kebele", "beneficiary_segment"}, all
	required. ``user`` is the caller. The segment must be active, and a farmer can be in a
	segment only once (409 otherwise)."""
	b = request_fields(kwargs, *FB_REQUIRED)
	values = {k: str(b.get(k) or "").strip() for k in FB_REQUIRED}
	missing = [k for k, v in values.items() if not v]
	if missing:
		frappe.throw(_("Missing required fields: {0}.").format(", ".join(missing)), frappe.ValidationError)

	if not frappe.db.get_value(DOCTYPE, {"name": values["beneficiary_segment"], "is_active": 1}):
		frappe.throw(
			_("Beneficiary Segment {0} does not exist or is not active.").format(
				values["beneficiary_segment"]
			),
			frappe.ValidationError,
		)
	if frappe.db.exists(
		FB_DOCTYPE,
		{"farmer_id": values["farmer_id"], "beneficiary_segment": values["beneficiary_segment"]},
	):
		frappe.throw(
			_("Farmer {0} is already in segment {1}.").format(
				values["farmer_id"], values["beneficiary_segment"]
			),
			frappe.DuplicateEntryError,
		)

	doc = frappe.get_doc({"doctype": FB_DOCTYPE, "user": frappe.session.user, **values})
	doc.insert()  # DocType permissions and link validation apply

	return success_response(
		data={f: doc.get(f) for f in FB_FIELDS},
		message="Farmer beneficiary created",
	)
