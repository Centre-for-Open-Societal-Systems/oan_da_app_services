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
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.api.v1.common import request_fields
from da_services.services import surveys as svc
from da_services.utils.permissions import require_roles


@route(
	"/surveys/templates", methods=("POST",), status=201, summary="Supervisor/Admin: publish a survey template"
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def create_template(**kwargs):
	"""Body: TemplateInput — name, type, open, close, languages[], targetFarmers, questions[]."""
	ctx = require_roles(*svc.CREATE_ROLES)
	b = request_fields(
		kwargs,
		"name",
		"type",
		"survey_type",
		"open",
		"close",
		"languages",
		"targetFarmers",
		"target_farmers",
		"questions",
		"region",
		"zone",
		"woreda",
		"client_op_id",
		json_keys=("languages", "questions"),
	)
	out = svc.create_template(ctx=ctx, **b)
	return success_response(data=out, message="Survey template published")


@route("/surveys/templates", methods=("GET",), summary="List survey templates in scope")
@frappe.whitelist()
@handle_api_errors
def list_templates(**kwargs):
	ctx = require_roles(*svc.RESULTS_ROLES)
	q = request_fields(kwargs, "status", "type", "q", "limit", "start")
	limit = int(q.get("limit") or 50)
	start = int(q.get("start") or 0)
	rows, total = svc.list_templates(
		ctx, status=q.get("status"), type=q.get("type"), q=q.get("q"), limit=limit, start=start
	)
	return success_response(data=rows, meta={"total": total, "limit": limit, "start": start})


@route("/surveys/templates/<survey_id>", methods=("GET",), summary="One survey template")
@frappe.whitelist()
@handle_api_errors
def get_template(survey_id: str):
	ctx = require_roles(*svc.RESULTS_ROLES)
	return success_response(data=svc.get_template(survey_id, ctx))


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
	)
	b = request_fields(kwargs, *keys, json_keys=("languages", "questions"))
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
	q = request_fields(kwargs, "status", "type", "language", "q", "limit", "start")
	limit = int(q.get("limit") or 50)
	start = int(q.get("start") or 0)
	rows, total = svc.list_surveys(
		ctx,
		status=q.get("status"),
		type=q.get("type"),
		language=q.get("language"),
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
	q = request_fields(kwargs, "farmer_id", "limit", "start")
	limit = int(q.get("limit") or 50)
	start = int(q.get("start") or 0)
	rows, total = svc.list_responses(survey_id, ctx, farmer_id=q.get("farmer_id"), limit=limit, start=start)
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
	"""XLSX download. Uses ``type=download`` so ``handle_api_errors`` leaves it raw."""
	ctx = require_roles(*svc.RESULTS_ROLES)
	filename, content = svc.export_responses(survey_id, ctx)
	frappe.response["type"] = "download"
	frappe.response["filename"] = filename
	frappe.response["filecontent"] = content
	frappe.response["content_type"] = XLSX_MIME
	return None


@route("/surveys/<survey_id>/results", methods=("GET",), summary="Basic survey result aggregates")
@frappe.whitelist()
@handle_api_errors
def survey_results(survey_id: str):
	ctx = require_roles(*svc.RESULTS_ROLES)
	return success_response(data=svc.survey_results(survey_id, ctx))
