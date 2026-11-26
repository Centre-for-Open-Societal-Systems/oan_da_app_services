"""v1 DA profile and profile change requests (FSD 3.7; docs/api-da-screens.md 2.18).

GET  /api/v1/da-services/profile/my_profile                the signed-in user's own profile
GET  /api/v1/da-services/profile/agents/<da_id>            Supervisor / Executive / Administrator
GET  /api/v1/da-services/profile/documents/<document_id>   ?da_id= (none: own record) -> {url}
GET  /api/v1/da-services/profile/certificates              ?da_id=
GET  /api/v1/da-services/profile/editable-fields           ?da_id= (none: own record): what may be asked
POST /api/v1/da-services/profile/change-requests           {section, action, values, record_id, document_id,
                                                           replaces, reason, client_op_id, da_id} (+ file)
GET  /api/v1/da-services/profile/change-requests           ?status=a,b&woreda=a,b&section=&da_id=&mine=1&no_supervisor_found=1&limit=&start=
GET  /api/v1/da-services/profile/change-requests/<id>      with its history
GET  /api/v1/da-services/profile/change-requests/<id>/file the file a certificate / photo request carries
POST /api/v1/da-services/profile/change-requests/action    {request_id | request_ids, action, note}
GET  /api/v1/da-services/profile/change-logs               ?da_id=&section=&limit=&start= : every step, newest first

A change request is about the caller's own record, or (``da_id``) sent by a Supervisor for a
DA below them in DA Hierarchy. ``section`` says what is asked: Contact, Qualifications,
Identity, Location (``values`` {field: value}); Education records, Family members
(``action`` add / update / delete, ``record_id``, ``values``); Certificates and Photo
(``action`` add with the file as multipart ``file``, or delete).

The steps of a request are the Frappe Workflow of DA Profile Change Request: a Supervisor
above the DA (level 1), then an OAN Administrator (level 2), whose approval sends the change
to the DA Registry with the Administrator's own token. Every change request in an answer
carries ``allowed_actions``: the steps this caller may take now, to send to ``.../action``.

Refusals a screen treats differently have their own ``code``: ``ALREADY_DECIDED`` (reload
the request), ``OPEN_REQUEST_EXISTS``, ``NOTHING_TO_CHANGE``, and ``VALIDATION_ERROR``
with ``details {field: message}`` when one field's value is not acceptable.
"""

from __future__ import annotations

import functools

import frappe
from frappe import _
from frappe.utils.response import build_response
from oan_auth_service.api.utils import error_response, handle_api_errors, success_response

from da_services.api.router import route
from da_services.api.v1.common import request_fields, truthy
from da_services.integrations.da_registry.client import DARegistryNotConfigured
from da_services.services import profile as svc
from da_services.services import profile_lists
from da_services.utils.permissions import require_permission


def coded_refusals(endpoint):
	"""Answer a refusal that has its own code with that code.

	The shared handler derives the code from the HTTP status, so every 400 would read
	VALIDATION_ERROR and a screen could not tell "somebody decided this before you" from
	"this value is not valid". Other errors pass through to the shared handler.
	"""

	@functools.wraps(endpoint)
	def wrapper(*args, **kwargs):
		try:
			return endpoint(*args, **kwargs)
		except frappe.ValidationError as exc:
			code = getattr(exc, "api_code", None)
			if not code:
				raise
			frappe.db.rollback()  # as the shared handler does: nothing of a refused request is kept
			frappe.clear_messages()
			frappe.response["http_status_code"] = 400
			field = getattr(exc, "field", None)
			return error_response(str(exc), code, details={field: str(exc)} if field else None)

	return wrapper


def registry_settings_required(endpoint):
	"""Say that DA Services Settings does not name the DA Registry yet, and which settings
	are empty.

	The shared handler answers every 5xx with "An unexpected error occurred", so a site
	with an empty "DA Registry Tab ID" could not be told apart from a crash. This answers
	500 ``DA_REGISTRY_NOT_CONFIGURED`` with ``details.missing_settings`` (the labels of the
	empty settings, nothing else), and writes the same to the Error Log.
	"""

	@functools.wraps(endpoint)
	def wrapper(*args, **kwargs):
		try:
			return endpoint(*args, **kwargs)
		except DARegistryNotConfigured as exc:
			frappe.db.rollback()
			frappe.clear_messages()
			frappe.log_error(title="DA Registry is not configured", message=str(exc))
			frappe.response["http_status_code"] = 500
			return error_response(
				_("DA Registry is not configured."),
				"DA_REGISTRY_NOT_CONFIGURED",
				details={"missing_settings": exc.missing},
			)

	return wrapper


@route(
	"/profile/my_profile",
	methods=("GET",),
	summary="The signed-in DA's own profile from the DA Registry, with open change requests",
)
@frappe.whitelist()
@handle_api_errors
@registry_settings_required
def my_profile():
	return success_response(data=svc.my_profile())


@route("/profile/agents/<da_id>", methods=("GET",), summary="A DA's profile and open change requests")
@frappe.whitelist()
@handle_api_errors
def agent_profile(da_id: str):
	return success_response(data=svc.agent_profile(da_id))


@route(
	"/profile/documents/<document_id>",
	methods=("GET",),
	summary="A short-lived link to one of a DA's registry documents (certificate, photo)",
)
@frappe.whitelist()
@handle_api_errors
@registry_settings_required
def document(document_id: str, **kwargs):
	q = request_fields(kwargs, "da_id")
	return success_response(data=svc.document_link(document_id, da_id=q.get("da_id")))


def _upload(field: str = "file") -> tuple[str | None, bytes | None]:
	"""(file name, content) of the request's multipart file, or (None, None) without one.
	At most a byte over the limit is read."""
	request = getattr(frappe.local, "request", None)
	upload = request.files.get(field) if request is not None and request.files else None
	if upload is None:
		return None, None
	return upload.filename or "", upload.read(profile_lists.MAX_FILE_BYTES + 1)


@route("/profile/certificates", methods=("GET",), summary="A DA's certificates: verified, pending, rejected")
@frappe.whitelist()
@handle_api_errors
def certificates(**kwargs):
	require_permission("DA Certificate", "read")
	q = request_fields(kwargs, "da_id")
	return success_response(data=svc.list_certificates(q.get("da_id")))


@route(
	"/profile/editable-fields",
	methods=("GET",),
	summary="What the caller may ask to change: own record, or a DA below them (da_id)",
)
@frappe.whitelist()
@handle_api_errors
@registry_settings_required
def editable_fields(**kwargs):
	q = request_fields(kwargs, "da_id")
	return success_response(data=svc.editable_fields(q.get("da_id")))


@route(
	"/profile/change-requests/<request_id>/file",
	methods=("GET",),
	summary="The file a certificate or photo request carries (to look at it before deciding)",
)
@frappe.whitelist()
@handle_api_errors
def request_file(request_id: str):
	"""A raw download, not a JSON envelope (see api/v1/surveys.py export_responses)."""
	require_permission("DA Profile Change Request", "read")
	filename, content, content_type = svc.request_file(request_id)
	frappe.response["type"] = "download"
	frappe.response["filename"] = filename
	frappe.response["filecontent"] = content
	frappe.response["content_type"] = content_type
	return build_response("download")


@route(
	"/profile/change-requests",
	methods=("POST",),
	status=201,
	summary="Request a profile change: fields of a section, a list row, a certificate or the photo",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def submit(**kwargs):
	require_permission("DA Profile Change Request", "create")
	b = request_fields(
		kwargs,
		"section",
		"action",
		"values",
		"record_id",
		"document_id",
		"replaces",
		"reason",
		"client_op_id",
		"da_id",
		json_keys=("values",),
	)
	filename, content = _upload()
	out = svc.submit(
		section=b.get("section"),
		action=b.get("action"),
		values=b.get("values") or {},
		record_id=b.get("record_id"),
		document_id=b.get("document_id"),
		replaces=b.get("replaces"),
		filename=filename,
		content=content,
		reason=b.get("reason"),
		client_op_id=b.get("client_op_id"),
		da_id=b.get("da_id"),
	)
	frappe.response["http_status_code"] = 201
	return success_response(data=out, message=svc.sent_message(out))


@route("/profile/change-requests", methods=("GET",), summary="Profile change requests in my scope")
@frappe.whitelist()
@handle_api_errors
def list_requests(**kwargs):
	require_permission("DA Profile Change Request", "read")
	q = request_fields(
		kwargs, "status", "da_id", "woreda", "section", "mine", "no_supervisor_found", "limit", "start"
	)
	try:
		limit, start = int(q.get("limit") or 50), int(q.get("start") or 0)
	except ValueError:
		frappe.throw(_("limit and start must be whole numbers."), frappe.ValidationError)
	rows, total = svc.list_change_requests(
		status=q.get("status"),
		da_id=q.get("da_id"),
		woreda=q.get("woreda"),
		section=q.get("section"),
		mine=truthy(q.get("mine")),
		no_supervisor_found=truthy(q.get("no_supervisor_found")),
		limit=limit,
		start=start,
	)
	return success_response(data=rows, meta={"total": total, "limit": limit, "start": start})


@route(
	"/profile/change-requests/action",
	methods=("POST",),
	summary="Take a step on one change request (request_id) or the same step on several (request_ids)",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def act(**kwargs):
	"""``request_id``: the step is taken or refused as a whole, and the request is answered.
	``request_ids`` (at most 20): each is tried on its own and ``results`` says how it went.
	"""
	b = request_fields(kwargs, "request_id", "request_ids", "action", "note", json_keys=("request_ids",))
	action, note = b.get("action"), b.get("note")
	if b.get("request_ids"):
		out = svc.act_many(b.get("request_ids"), action=action, note=note)
		return success_response(data=out, message=f"{out['done']} done, {out['failed']} not done")
	if not b.get("request_id"):
		frappe.throw(_("request_id or request_ids is required."), frappe.ValidationError)
	out = svc.act(str(b.get("request_id")), action=action, note=note)
	return success_response(data=out, message=svc.decision_message(out))


@route(
	"/profile/change-logs",
	methods=("GET",),
	summary="The log of profile changes: every step of the requests I may see, newest first",
)
@frappe.whitelist()
@handle_api_errors
def change_logs(**kwargs):
	require_permission("DA Profile Change Request", "read")
	q = request_fields(kwargs, "da_id", "section", "limit", "start")
	try:
		limit, start = int(q.get("limit") or 50), int(q.get("start") or 0)
	except ValueError:
		frappe.throw(_("limit and start must be whole numbers."), frappe.ValidationError)
	rows, total = svc.change_logs(da_id=q.get("da_id"), section=q.get("section"), limit=limit, start=start)
	return success_response(data=rows, meta={"total": total, "limit": limit, "start": start})


@route("/profile/change-requests/<request_id>", methods=("GET",), summary="One profile change request")
@frappe.whitelist()
@handle_api_errors
def get_request(request_id: str):
	require_permission("DA Profile Change Request", "read")
	return success_response(data=svc.get_change_request(request_id))
