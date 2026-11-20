"""v1 surveys: templates, assigned surveys, responses, results.

POST  /api/v1/da-services/surveys/templates
GET   /api/v1/da-services/surveys/templates
GET   /api/v1/da-services/surveys/templates/<id>
PATCH /api/v1/da-services/surveys/templates/<id>
GET   /api/v1/da-services/surveys
GET   /api/v1/da-services/surveys/<id>
POST  /api/v1/da-services/surveys/<id>/responses
GET   /api/v1/da-services/surveys/<id>/responses
GET   /api/v1/da-services/surveys/<id>/responses/export
GET   /api/v1/da-services/surveys/<id>/results

Thin: parse the request, call the service, wrap the envelope. Access comes from the
DocType Role DocPerm and the user's User Permissions.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint
from frappe.utils.response import build_response
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.api.v1.common import request_fields
from da_services.services import surveys as svc

TEMPLATE_KEYS = (
	"name",
	"type",
	"open",
	"close",
	"languages",
	"targetFarmers",
	"questions",
	"woreda",
	"assignmentMode",
	"assignedDaIds",
)
JSON_KEYS = ("languages", "questions", "assignedDaIds")
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _page(q: dict) -> tuple[int, int]:
	return cint(q.get("limit")) or 50, cint(q.get("start"))


@route("/surveys/templates", methods=("POST",), status=201, summary="Publish a survey template")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def create_template(**kwargs):
	"""Body: name, type, open, close, languages[], targetFarmers, questions[], woreda?,
	assignmentMode?, assignedDaIds?, client_op_id?. Returns the new template id."""
	b = request_fields(kwargs, *TEMPLATE_KEYS, "client_op_id", json_keys=JSON_KEYS)
	survey_id = svc.create_template(
		name=b.get("name"),
		type=b.get("type"),
		open=b.get("open"),
		close=b.get("close"),
		languages=b.get("languages"),
		targetFarmers=b.get("targetFarmers"),
		questions=b.get("questions"),
		woreda=b.get("woreda"),
		assignmentMode=b.get("assignmentMode"),
		assignedDaIds=b.get("assignedDaIds"),
		client_op_id=b.get("client_op_id"),
	)
	return success_response(data={"id": survey_id}, message="Survey template published")


@route("/surveys/templates", methods=("GET",), summary="Survey templates")
@frappe.whitelist()
@handle_api_errors
def list_templates(**kwargs):
	q = request_fields(kwargs, "status", "type", "woreda", "q", "limit", "start")
	limit, start = _page(q)
	rows, total = svc.list_templates(
		status=q.get("status"),
		type=q.get("type"),
		woreda=q.get("woreda"),
		q=q.get("q"),
		limit=limit,
		start=start,
	)
	return success_response(data=rows, meta={"total": total, "limit": limit, "start": start})


@route("/surveys/templates/<survey_id>", methods=("GET",), summary="One survey template")
@frappe.whitelist()
@handle_api_errors
def get_template(survey_id: str):
	return success_response(data=svc.get_template(survey_id))


@route("/surveys/templates/<survey_id>", methods=("PATCH",), summary="Update a survey template")
@frappe.whitelist(methods=["PATCH", "POST"])
@handle_api_errors
def patch_template(survey_id: str, **kwargs):
	"""Body: only the keys to change (name, status, open, close, languages, targetFarmers,
	questions, assignmentMode, assignedDaIds). Returns the id saved to (a new version
	when questions change after responses were collected)."""
	b = request_fields(kwargs, *TEMPLATE_KEYS, "status", json_keys=JSON_KEYS)
	body = {k: v for k, v in b.items() if v is not None}
	return success_response(
		data={"id": svc.patch_template(survey_id, body)}, message="Survey template updated"
	)


@route("/surveys", methods=("GET",), summary="Assigned surveys for the caller")
@frappe.whitelist()
@handle_api_errors
def list_surveys(**kwargs):
	q = request_fields(kwargs, "status", "type", "language", "woreda", "q", "limit", "start")
	limit, start = _page(q)
	rows, total = svc.list_surveys(
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
	return success_response(data=svc.get_survey(survey_id))


@route("/surveys/<survey_id>/responses", methods=("POST",), status=201, summary="Save a survey response")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def submit_response(survey_id: str, **kwargs):
	"""Body: farmer_id, consent, answers, kebele?, started_at?, completed_at?, sync_state?,
	client_op_id?, da_id?. Response is message-only."""
	b = request_fields(
		kwargs,
		"farmer_id",
		"consent",
		"answers",
		"kebele",
		"started_at",
		"completed_at",
		"sync_state",
		"client_op_id",
		"da_id",
		json_keys=("answers",),
	)
	svc.create_response(
		survey_id,
		farmer_id=b.get("farmer_id"),
		consent=b.get("consent"),
		answers=b.get("answers"),
		kebele=b.get("kebele"),
		started_at=b.get("started_at"),
		completed_at=b.get("completed_at"),
		sync_state=b.get("sync_state"),
		client_op_id=b.get("client_op_id"),
		da_id=b.get("da_id"),
	)
	return success_response(message="Survey response saved")


@route("/surveys/<survey_id>/responses", methods=("GET",), summary="Responses for a survey")
@frappe.whitelist()
@handle_api_errors
def list_responses(survey_id: str, **kwargs):
	q = request_fields(kwargs, "farmer_id", "da_id", "region", "zone", "woreda", "kebele", "limit", "start")
	limit, start = _page(q)
	rows, total = svc.list_responses(
		survey_id,
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


@route("/surveys/<survey_id>/responses/export", methods=("GET",), summary="Download responses as XLSX")
@frappe.whitelist()
@handle_api_errors
def export_responses(survey_id: str):
	"""XLSX download as a raw response (the router leaves a ``Response`` untouched)."""
	filename, content = svc.export_responses(survey_id)
	frappe.response["type"] = "download"
	frappe.response["filename"] = filename
	frappe.response["filecontent"] = content
	frappe.response["content_type"] = XLSX_MIME
	return build_response("download")


@route("/surveys/<survey_id>/results", methods=("GET",), summary="Survey result aggregates")
@frappe.whitelist()
@handle_api_errors
def survey_results(survey_id: str):
	return success_response(data=svc.survey_results(survey_id))
