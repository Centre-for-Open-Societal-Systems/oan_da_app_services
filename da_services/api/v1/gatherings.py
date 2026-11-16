"""v1 gatherings: plan, list, detail, cancel.

POST /api/v1/da-services/gatherings                 plan a gathering (201)
GET  /api/v1/da-services/gatherings?date|from&to&status&da_id&region&zone&woreda&kebele&limit&start
GET  /api/v1/da-services/gatherings/<gathering_id>  one gathering
POST /api/v1/da-services/gatherings/<gathering_id>/cancel  {reason}

Plan requires FE ``user`` (User Link) and ``farmers[]`` with ``farmer_id``,
``farmer_name``, and ``phone`` (no DA Farmer Reference lookup). ``da_id`` is
legacy/envelope only; the row uses ``User.da_registry_id``. Location client
inputs are venue (``location.name``), ``landmark``, lat/lng only — address +
region/zone/woreda/kebele are stamped read-only from the DA's Effective Kebele
Assignment. ``notify_farmers_sms`` records intent and writes SMS log rows; the
gateway is not wired yet.

Datetimes: site-local time (System Settings), ISO 8601 without offset.

Access and row visibility come from DA Gathering DocType Role permissions + User
Permissions (``frappe.get_list`` / ``insert`` / ``get_doc`` / ``save``). Handlers
do not use ``require_roles``, ``ScopeContext``, custom Gathering permission hooks,
or service-side permission helpers.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import get_datetime
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.services import gatherings as gatherings_service

PLAN_FIELDS = (
	"title",
	"purpose",
	"scheduled_at",
	"date",
	"time",
	"location",
	"farmers",
	"notify_farmers_sms",
	"user",
	"da_id",
	"client_op_id",
)


def _request_payload(kwargs: dict | None = None) -> dict:
	"""JSON/form body from form_dict (Frappe already parsed it); kwargs for unit tests."""
	form_data = dict(frappe.form_dict or {})
	if form_data:
		return form_data
	request = getattr(frappe.local, "request", None)
	json_data = dict((request.get_json(silent=True) if request is not None else None) or {})
	if json_data:
		return json_data
	return dict(kwargs or {})


def _request_fields(kwargs: dict, *field_names: str) -> dict:
	"""Request fields by name from the parsed body, else the call's kwargs."""
	payload = _request_payload(kwargs)
	fields = {name: payload.get(name, kwargs.get(name)) for name in field_names}
	for key in ("location", "farmers"):
		if isinstance(fields.get(key), str):
			try:
				fields[key] = json.loads(fields[key])
			except ValueError:
				frappe.throw(_("{0} must be JSON.").format(key), frappe.ValidationError)
	return fields


def _parse_scheduled_at(fields: dict):
	"""`scheduled_at`, or the form's `date` + `time`."""
	if fields.get("scheduled_at"):
		return get_datetime(fields["scheduled_at"])
	if fields.get("date"):
		return get_datetime(f"{fields['date']} {fields.get('time') or '09:00'}")
	return None


def _truthy(value) -> bool:
	if value is None:
		return True
	return str(value).lower() in ("1", "true", "yes", "on")


@route("/gatherings", methods=("POST",), status=201, summary="Plan a gathering")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def plan(**kwargs):
	fields = _request_fields(kwargs, *PLAN_FIELDS)
	gathering = gatherings_service.plan_gathering(
		title=(fields.get("title") or "").strip(),
		purpose=fields.get("purpose"),
		scheduled_at=_parse_scheduled_at(fields),
		location=fields.get("location") or None,
		farmers=fields.get("farmers"),
		notify_farmers_sms=_truthy(fields.get("notify_farmers_sms")),
		user=(fields.get("user") or "").strip(),
		da_id=fields.get("da_id"),
		client_op_id=fields.get("client_op_id") or None,
	)
	return success_response(data=gathering, message="Gathering planned")


@route("/gatherings", methods=("GET",), summary="Gatherings the caller may see")
@frappe.whitelist()
@handle_api_errors
def list_gatherings(**kwargs):
	query = _request_fields(
		kwargs,
		"date",
		"from",
		"to",
		"status",
		"da_id",
		"region",
		"zone",
		"woreda",
		"kebele",
		"limit",
		"start",
	)
	limit = int(query.get("limit") or 50)
	start = int(query.get("start") or 0)
	rows, total = gatherings_service.list_gatherings(
		date=query.get("date"),
		from_date=query.get("from"),
		to_date=query.get("to"),
		status=query.get("status"),
		da_id=query.get("da_id"),
		region=query.get("region"),
		zone=query.get("zone"),
		woreda=query.get("woreda"),
		kebele=query.get("kebele"),
		limit=limit,
		start=start,
	)
	return success_response(data=rows, meta={"total": total, "limit": limit, "start": start})


@route("/gatherings/<gathering_id>", methods=("GET",), summary="One gathering")
@frappe.whitelist()
@handle_api_errors
def get_gathering(gathering_id: str, **kwargs):
	gathering = gatherings_service.get_gathering(gathering_id)
	return success_response(data=gathering)


@route("/gatherings/<gathering_id>/cancel", methods=("POST",), summary="Cancel a planned gathering")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def cancel(gathering_id: str, **kwargs):
	fields = _request_fields(kwargs, "reason")
	gathering = gatherings_service.cancel_gathering(gathering_id, reason=fields.get("reason"))
	return success_response(data=gathering, message="Gathering cancelled")
