"""v1 surveys: templates, assigned tasks, responses, results (docs/api-da-screens.md §2.16).

POST /api/v1/da-services/surveys/templates
GET  /api/v1/da-services/surveys/templates
GET  /api/v1/da-services/surveys/templates/<id>
PATCH /api/v1/da-services/surveys/templates/<id>
GET  /api/v1/da-services/surveys
GET  /api/v1/da-services/surveys/<id>
POST /api/v1/da-services/surveys/<id>/responses
GET  /api/v1/da-services/surveys/<id>/responses
GET  /api/v1/da-services/surveys/<id>/responses/export
GET  /api/v1/da-services/surveys/<id>/results
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils.response import build_response
from oan_auth_service.api.utils import (
	error_response,
	handle_api_errors,
	success_response,
)

from da_services.api.router import route
from da_services.api.v1.common import request_fields
from da_services.services import surveys as svc
from da_services.services.surveys import SurveyValidationError
from da_services.utils.permissions import require_roles


@route(
	"/surveys/templates", methods=("POST",), status=201, summary="Supervisor/Admin: publish a survey template"
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def create_template(**kwargs):
	"""Create and immediately publish a survey template.

	Contract body: template_name, survey_type, opens_on, closes_on, farmers_per_da,
	woreda, assignment{type}, questions[{question_no, question, answer_type,
	rating_scale, options, required}]. The legacy field names stay accepted as
	aliases so existing clients are unaffected.
	"""
	ctx = require_roles(*svc.CREATE_ROLES)
	b = request_fields(
		kwargs,
		"name",
		"template_name",
		"type",
		"survey_type",
		"open",
		"opens_on",
		"close",
		"closes_on",
		"languages",
		"targetFarmers",
		"target_farmers",
		"farmers_per_da",
		"questions",
		"region",
		"zone",
		"woreda",
		"assignment",
		"assignmentMode",
		"assignment_mode",
		"assignedDaIds",
		"assigned_da_ids",
		"client_op_id",
		json_keys=(
			"languages",
			"questions",
			"assignment",
			"assignedDaIds",
			"assigned_da_ids",
		),
	)
	try:
		out = svc.create_template(ctx=ctx, **b)
	except SurveyValidationError as e:
		frappe.local.message_log = []
		frappe.response["http_status_code"] = 400
		return error_response(
			_("Validation failed"),
			code="VALIDATION_ERROR",
			details={err["field"]: err["message"] for err in e.errors},
		)
	# The contract's create response carries only the publish confirmation; the
	# full template is available through the details API. "Closing soon" is still
	# open for responses, so it reports as "open" here.
	data = {
		"id": out["id"],
		"template_name": out["name"],
		"status": "closed" if out.get("status") == "Closed" else "open",
		"published_at": out.get("published_at"),
	}
	return success_response(data=data, message="Survey template created and published successfully")


@route("/surveys/templates", methods=("GET",), summary="List survey templates in scope")
@frappe.whitelist()
@handle_api_errors
def list_templates(**kwargs):
	ctx = require_roles(*svc.RESULTS_ROLES)
	q = request_fields(
		kwargs,
		"page",
		"page_size",
		"status",
		"type",
		"search",
		"sort_by",
		"sort_order",
		"woreda",
		"q",
		"limit",
		"start",
	)
	rows, total = svc.list_templates(
		ctx,
		page=_int_param(q.get("page"), 1),
		page_size=_int_param(q.get("page_size"), svc.DEFAULT_PAGE_SIZE),
		status=q.get("status"),
		type=q.get("type"),
		search=q.get("search"),
		sort_by=q.get("sort_by") or "creation",
		sort_order=q.get("sort_order") or "desc",
		woreda=q.get("woreda"),
		q=q.get("q"),
		limit=_int_param(q.get("limit"), None) if q.get("limit") is not None else None,
		start=_int_param(q.get("start"), None) if q.get("start") is not None else None,
	)
	# Mirror the service's paging arithmetic for the pagination block: legacy
	# limit/start wins when supplied, otherwise page/page_size with 1-based pages.
	if q.get("limit") is not None or q.get("start") is not None:
		size = max(1, min(_int_param(q.get("limit"), svc.DEFAULT_PAGE_SIZE), 200))
		first = max(0, _int_param(q.get("start"), 0))
	else:
		size = max(1, min(_int_param(q.get("page_size"), svc.DEFAULT_PAGE_SIZE), 200))
		first = (max(1, _int_param(q.get("page"), 1)) - 1) * size
	current_page = first // size + 1
	total_pages = max(1, -(-total // size))
	data = {
		"templates": rows,
		"pagination": {
			"current_page": current_page,
			"page_size": size,
			"total_records": total,
			"total_pages": total_pages,
			"has_next": current_page < total_pages,
			"has_previous": current_page > 1,
		},
	}
	return success_response(data=data, meta={"total": total})


def _int_param(value, default):
	try:
		return int(value)
	except TypeError, ValueError:
		return default


@route("/surveys/templates/<survey_id>", methods=("GET",), summary="One survey template")
@frappe.whitelist()
@handle_api_errors
def get_template(survey_id: str):
	"""Full saved template configuration (the View action in the listing screen)."""
	ctx = require_roles(*svc.RESULTS_ROLES)
	return success_response(
		data=svc.get_template_details(survey_id, ctx),
		message="Survey template retrieved successfully",
	)


@route(
	"/surveys/templates/<survey_id>", methods=("PATCH",), summary="Supervisor/Admin: update a survey template"
)
@frappe.whitelist(methods=["PATCH", "POST"])
@handle_api_errors
def patch_template(survey_id: str, **kwargs):
	ctx = require_roles(*svc.CREATE_ROLES)
	keys = (
		"name",
		"status",
		"open",
		"close",
		"window_open",
		"window_close",
		"languages",
		"targetFarmers",
		"target_farmers",
		"questions",
		"assignmentMode",
		"assignment_mode",
		"assignedDaIds",
		"assigned_da_ids",
	)
	b = request_fields(
		kwargs,
		*keys,
		json_keys=("languages", "questions", "assignedDaIds", "assigned_da_ids"),
	)
	request = getattr(frappe.local, "request", None)
	data = (request.get_json(silent=True) if request is not None else None) or {}
	body = {k: b[k] for k in keys if k in data or k in kwargs}
	out = svc.patch_template(survey_id, body, ctx=ctx)
	return success_response(data=out, message="Survey template updated")


@route("/surveys", methods=("GET",), summary="Assigned surveys for the caller")
@frappe.whitelist()
@handle_api_errors
def list_surveys(**kwargs):
	ctx = require_roles(*svc.LIST_ROLES)
	q = request_fields(kwargs, "status", "type", "language", "woreda", "q", "limit", "start")
	limit = int(q.get("limit") or 50)
	start = int(q.get("start") or 0)
	rows, total = svc.list_surveys(
		ctx,
		status=q.get("status"),
		type=q.get("type"),
		language=q.get("language"),
		woreda=q.get("woreda"),
		q=q.get("q"),
		limit=limit,
		start=start,
	)
	return success_response(data=rows, meta={"total": total, "limit": limit, "start": start})


@route("/surveys/<survey_id>", methods=("GET",), summary="One assigned survey with parameters")
@frappe.whitelist()
@handle_api_errors
def get_survey(survey_id: str):
	ctx = require_roles(*svc.LIST_ROLES)
	return success_response(data=svc.get_survey(survey_id, ctx))


@route(
	"/surveys/<survey_id>/responses",
	methods=("POST",),
	status=201,
	summary="Capture a survey response",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def submit_response(survey_id: str, **kwargs):
	"""Body: farmer_id, consent, answers, started_at?, completed_at?, sync_state?, client_op_id?, da_id?."""
	ctx = require_roles(*svc.CAPTURE_ROLES)
	b = request_fields(
		kwargs,
		"farmer_id",
		"consent",
		"answers",
		"started_at",
		"completed_at",
		"sync_state",
		"client_op_id",
		"da_id",
		json_keys=("answers",),
	)
	out = svc.submit_response(survey_id, ctx=ctx, **b)
	return success_response(data=out, message="Survey response saved")


@route("/surveys/<survey_id>/responses", methods=("GET",), summary="List responses for a survey")
@frappe.whitelist()
@handle_api_errors
def list_responses(survey_id: str, **kwargs):
	ctx = require_roles(*svc.LIST_ROLES)
	q = request_fields(
		kwargs,
		"farmer_id",
		"da_id",
		"region",
		"zone",
		"woreda",
		"kebele",
		"limit",
		"start",
	)
	limit = int(q.get("limit") or 50)
	start = int(q.get("start") or 0)
	rows, total = svc.list_responses(
		survey_id,
		ctx,
		farmer_id=q.get("farmer_id"),
		da_id=q.get("da_id"),
		region=q.get("region"),
		zone=q.get("zone"),
		woreda=q.get("woreda"),
		kebele=q.get("kebele"),
		limit=limit,
		start=start,
	)
	return success_response(data=rows, meta={"total": total, "limit": limit, "start": start})


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@route(
	"/surveys/<survey_id>/responses/export",
	methods=("GET",),
	summary="Supervisor/Executive/Admin: download responses as XLSX",
)
@frappe.whitelist()
@handle_api_errors
def export_responses(survey_id: str):
	"""XLSX download as a raw Werkzeug response, not a JSON envelope.

	The custom router only leaves a ``Response`` untouched; returning ``None`` after
	setting ``frappe.response`` would be serialised as JSON. ``build_response`` is
	Frappe's own download builder, so the mimetype, filename and bytes stay in step
	with the rest of the framework.
	"""
	ctx = require_roles(*svc.RESULTS_ROLES)
	filename, content = svc.export_responses(survey_id, ctx)
	frappe.response["type"] = "download"
	frappe.response["filename"] = filename
	frappe.response["filecontent"] = content
	frappe.response["content_type"] = XLSX_MIME
	return build_response("download")


@route("/surveys/<survey_id>/results", methods=("GET",), summary="Basic survey result aggregates")
@frappe.whitelist()
@handle_api_errors
def survey_results(survey_id: str):
	ctx = require_roles(*svc.RESULTS_ROLES)
	return success_response(data=svc.survey_results(survey_id, ctx))
