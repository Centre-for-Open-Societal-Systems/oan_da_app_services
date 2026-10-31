"""v1: bulk import of Development Agents into the DA Registry (OAN Administrator only).

GET  /api/v1/da-services/agent-imports/template      columns and an empty CSV of template v3
POST /api/v1/da-services/agent-imports/validate      multipart: file -> row errors, nothing sent
POST /api/v1/da-services/agent-imports               multipart: file -> validated, then queued
GET  /api/v1/da-services/agent-imports               ?limit=&start=  past imports, newest first
GET  /api/v1/da-services/agent-imports/exceptions    ?page=&page_size=  rows the registry refused

A file with any row error is refused whole (400, row errors in details) and never
reaches the registry. Rows the registry itself refuses later (duplicate Fayda ID,
unknown Kebele) show in the exceptions list.
"""

from __future__ import annotations

import frappe
from frappe import _
from oan_auth_service.api.utils import error_response, handle_api_errors, success_response

from da_services.api.router import route
from da_services.integrations.da_registry.client import DARegistryError, DARegistryUnavailable
from da_services.services import agent_import as svc
from da_services.services import constants as C
from da_services.utils.permissions import require_roles
from da_services.utils.ratelimit import rate_limited


def _upload() -> tuple[str, bytes]:
	request = getattr(frappe.local, "request", None)
	upload = request.files.get("file") if request is not None and request.files else None
	if upload is None:
		frappe.throw(_("file is required (multipart/form-data)."), frappe.ValidationError)
	return upload.filename or "", upload.read(svc.MAX_BYTES + 1)


def _registry_error(exc: DARegistryError):
	"""503 when the registry is down (retry later); 502 when it refused the call."""
	unavailable = isinstance(exc, DARegistryUnavailable)
	frappe.response["http_status_code"] = 503 if unavailable else 502
	return error_response(
		_("The DA Registry is unavailable. Try again shortly.")
		if unavailable
		else _("The DA Registry refused the request: {0}").format(exc),
		"SERVICE_UNAVAILABLE" if unavailable else "BAD_GATEWAY",
	)


@route("/agent-imports/template", methods=("GET",), summary="Agent import template v3 (columns and CSV)")
@frappe.whitelist()
@handle_api_errors
def template():
	require_roles(*C.UNRESTRICTED_ROLES)
	return success_response(
		data={
			"version": "v3",
			"file_name": "DA_Import_Template.csv",
			"columns": list(svc.TEMPLATE_COLUMNS),
			"required": list(svc.REQUIRED),
			"csv": svc.template_csv(),
			"max_rows": svc.MAX_ROWS,
			"max_bytes": svc.MAX_BYTES,
		}
	)


@route("/agent-imports/validate", methods=("POST",), summary="Check an agent import file without sending it")
@frappe.whitelist(methods=["POST"])
@rate_limited(limit=30, seconds=60)
@handle_api_errors
def validate():
	require_roles(*C.UNRESTRICTED_ROLES)
	filename, content = _upload()
	out = svc.validate_file(filename, content)
	return success_response(
		data={"valid": not out["errors"], **out},
		message=_("File is ready to import")
		if not out["errors"]
		else _("Fix the rows listed and upload again"),
	)


@route("/agent-imports", methods=("POST",), status=201, summary="Import agents in bulk into the DA Registry")
@frappe.whitelist(methods=["POST"])
@rate_limited(limit=10, seconds=60)
@handle_api_errors
def import_agents():
	ctx = require_roles(*C.UNRESTRICTED_ROLES)
	filename, content = _upload()
	try:
		out = svc.import_agents(filename, content, ctx=ctx)
	except DARegistryError as exc:
		return _registry_error(exc)
	if out["status"] == "Rejected":
		frappe.response["http_status_code"] = 400
		return error_response(
			_("{0} row error(s); nothing was imported.").format(len(out["errors"])),
			"VALIDATION_ERROR",
			details={"rows": out["rows"], "errors": out["errors"]},
		)
	return success_response(
		data=out, message=_("Queued: the registry routes each agent to its two-stage approval")
	)


@route("/agent-imports", methods=("GET",), summary="Past agent imports")
@frappe.whitelist()
@handle_api_errors
def list_imports(limit=20, start=0):
	ctx = require_roles(*C.UNRESTRICTED_ROLES)
	rows = svc.list_imports(limit=limit, start=start, ctx=ctx)
	return success_response(data=rows, meta={"count": len(rows), "limit": limit, "start": start})


@route("/agent-imports/exceptions", methods=("GET",), summary="Import rows the DA Registry refused")
@frappe.whitelist()
@handle_api_errors
def exceptions(page=1, page_size=50):
	ctx = require_roles(*C.UNRESTRICTED_ROLES)
	try:
		out = svc.import_exceptions(page=page, page_size=page_size, ctx=ctx)
	except DARegistryError as exc:
		return _registry_error(exc)
	return success_response(
		data=out["items"], meta={"total": out["total"], "page": out["page"], "page_size": out["page_size"]}
	)
