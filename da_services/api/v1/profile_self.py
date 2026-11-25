"""v1 DA profile and profile change requests (FSD 3.7; docs/api-da-screens.md 2.18).

GET  /api/v1/da-services/profile/my_profile                     the signed-in DA's own profile
GET  /api/v1/da-services/profile/agents/<da_id>                 Supervisor / Executive / Administrator
GET  /api/v1/da-services/profile/documents/<document_id>         ?da_id= (none: own record) -> {url}
POST /api/v1/da-services/profile/qualifications                 {action, record_id, values, reason, da_id}
POST /api/v1/da-services/profile/family-members                 {action, member_id, values, reason, da_id}
GET  /api/v1/da-services/profile/certificates                   ?da_id=
POST /api/v1/da-services/profile/certificates                   multipart: file, record_id, title, issuer,
                                                                issued_on, expires_on, replaces, reason, da_id
POST /api/v1/da-services/profile/certificates/remove            {record_id, document_id, reason, da_id}
POST /api/v1/da-services/profile/photo                          multipart: file, reason, da_id
POST /api/v1/da-services/profile/photo/remove                   {reason, da_id}
GET  /api/v1/da-services/profile/change-requests/<id>/file      the file a certificate / photo request carries
POST /api/v1/da-services/profile/change-requests                {changes {field: value}, reason, client_op_id, da_id}
GET  /api/v1/da-services/profile/change-requests                ?status=a,b&woreda=a,b&section=&da_id=&mine=1&no_supervisor_found=1&limit=&start=
GET  /api/v1/da-services/profile/change-requests/<id>
POST /api/v1/da-services/profile/change-requests/<id>/cancel
POST /api/v1/da-services/profile/change-requests/<id>/decide    {decision: Approve | Reject | Request changes, note}
POST /api/v1/da-services/profile/change-requests/bulk-decide    {ids: [...], decision, note}  (at most 20)
POST /api/v1/da-services/profile/change-requests/<id>/sync-local Administrator (failed local update of a transfer)
POST /api/v1/da-services/profile/change-requests/<id>/retry     Administrator
POST /api/v1/da-services/profile/change-requests/<id>/close     Administrator {note}

A Supervisor asks for a change on a DA of their Woreda (``da_id``). Every section can be changed: Contact, Qualifications,
Identity and Location (services/profile_fields.py). A change reaches the DA Registry only
after the Woreda Supervisor (level 1) and then an OAN Administrator (level 2) approve it,
and it is sent in the Administrator's request with the Administrator's token.

Every change request in an answer says what the caller may do with it (``can_decide``,
``allowed_decisions``, ``can_cancel``, ``can_retry``, ``can_close``, ``can_sync_local``).
Refusals a screen treats differently have their own ``code``: ``ALREADY_DECIDED`` (reload
the request), ``OPEN_REQUEST_EXISTS``, ``NOTHING_TO_CHANGE``, and ``VALIDATION_ERROR``
with ``details {field: message}`` when one field's value is not acceptable.
"""

from __future__ import annotations

import functools

import frappe
from frappe.utils.response import build_response
from oan_auth_service.api.utils import error_response, handle_api_errors, success_response

from da_services.api.router import route
from da_services.api.v1.common import request_fields, truthy
from da_services.services import constants as C
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


@route(
	"/profile/my_profile",
	methods=("GET",),
	summary="The signed-in DA's own profile from the DA Registry, with open change requests",
)
@frappe.whitelist()
@handle_api_errors
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
def document(document_id: str, **kwargs):
	q = request_fields(kwargs, "da_id")
	return success_response(data=svc.document_link(document_id, da_id=q.get("da_id")))


def _upload(field: str = "file") -> tuple[str, bytes]:
	"""The multipart file of the request; at most a byte over the limit is read."""
	request = getattr(frappe.local, "request", None)
	upload = request.files.get(field) if request is not None and request.files else None
	if upload is None:
		frappe.throw(frappe._("file is required (multipart/form-data)."), frappe.ValidationError)
	return upload.filename or "", upload.read(profile_lists.MAX_FILE_BYTES + 1)


def _created(out: dict) -> dict:
	frappe.response["http_status_code"] = 201
	message = (
		"Change request sent to an OAN Administrator"
		if out["initiated_by_role"] == C.ROLE_SUPERVISOR
		else "Change request sent to your Woreda supervisor"
	)
	return success_response(data=out, message=message)


@route(
	"/profile/qualifications",
	methods=("POST",),
	status=201,
	summary="Add, change or remove an education record",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def qualification_change(**kwargs):
	require_permission("DA Profile Change Request", "create")
	b = request_fields(
		kwargs, "action", "record_id", "values", "reason", "client_op_id", "da_id", json_keys=("values",)
	)
	return _created(
		svc.submit_record_change(
			action=b.get("action"),
			record_id=b.get("record_id"),
			values=b.get("values") or {},
			reason=b.get("reason"),
			client_op_id=b.get("client_op_id"),
			da_id=b.get("da_id"),
		)
	)


@route(
	"/profile/family-members", methods=("POST",), status=201, summary="Add, change or remove a family member"
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def family_change(**kwargs):
	require_permission("DA Profile Change Request", "create")
	b = request_fields(
		kwargs, "action", "member_id", "values", "reason", "client_op_id", "da_id", json_keys=("values",)
	)
	return _created(
		svc.submit_family_change(
			action=b.get("action"),
			member_id=b.get("member_id"),
			values=b.get("values") or {},
			reason=b.get("reason"),
			client_op_id=b.get("client_op_id"),
			da_id=b.get("da_id"),
		)
	)


@route("/profile/certificates", methods=("GET",), summary="A DA's certificates: verified, pending, rejected")
@frappe.whitelist()
@handle_api_errors
def certificates(**kwargs):
	require_permission("DA Certificate", "read")
	q = request_fields(kwargs, "da_id")
	return success_response(data=svc.list_certificates(q.get("da_id")))


@route(
	"/profile/certificates", methods=("POST",), status=201, summary="Upload a certificate for verification"
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def certificate_upload(**kwargs):
	require_permission("DA Profile Change Request", "create")
	filename, content = _upload()
	b = request_fields(
		kwargs,
		"record_id",
		"title",
		"issuer",
		"issued_on",
		"expires_on",
		"replaces",
		"reason",
		"client_op_id",
		"da_id",
	)
	return _created(
		svc.submit_certificate(
			record_id=b.get("record_id"),
			filename=filename,
			content=content,
			title=b.get("title"),
			issuer=b.get("issuer"),
			issued_on=b.get("issued_on"),
			expires_on=b.get("expires_on"),
			replaces=b.get("replaces"),
			reason=b.get("reason"),
			client_op_id=b.get("client_op_id"),
			da_id=b.get("da_id"),
		)
	)


@route(
	"/profile/certificates/remove", methods=("POST",), status=201, summary="Take a certificate off a record"
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def certificate_remove(**kwargs):
	require_permission("DA Profile Change Request", "create")
	b = request_fields(kwargs, "record_id", "document_id", "reason", "client_op_id", "da_id")
	return _created(
		svc.submit_certificate_removal(
			record_id=b.get("record_id"),
			document_id=b.get("document_id"),
			reason=b.get("reason"),
			client_op_id=b.get("client_op_id"),
			da_id=b.get("da_id"),
		)
	)


@route("/profile/photo", methods=("POST",), status=201, summary="Ask for a new profile photo")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def photo_upload(**kwargs):
	require_permission("DA Profile Change Request", "create")
	filename, content = _upload()
	b = request_fields(kwargs, "reason", "client_op_id", "da_id")
	return _created(
		svc.submit_photo(
			filename=filename,
			content=content,
			reason=b.get("reason"),
			client_op_id=b.get("client_op_id"),
			da_id=b.get("da_id"),
		)
	)


@route("/profile/photo/remove", methods=("POST",), status=201, summary="Ask to remove the profile photo")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def photo_remove(**kwargs):
	require_permission("DA Profile Change Request", "create")
	b = request_fields(kwargs, "reason", "client_op_id", "da_id")
	return _created(
		svc.submit_photo(
			remove=True,
			reason=b.get("reason"),
			client_op_id=b.get("client_op_id"),
			da_id=b.get("da_id"),
		)
	)


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


@route("/profile/change-requests", methods=("POST",), status=201, summary="Request a profile change")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def submit(**kwargs):
	require_permission("DA Profile Change Request", "create")
	b = request_fields(kwargs, "changes", "reason", "client_op_id", "da_id", json_keys=("changes",))
	out = svc.submit_change_request(
		changes=b.get("changes") or {},
		reason=b.get("reason"),
		client_op_id=b.get("client_op_id"),
		da_id=b.get("da_id"),
	)
	message = (
		"Change request sent to an OAN Administrator"
		if out["initiated_by_role"] == C.ROLE_SUPERVISOR
		else "Change request sent to your Woreda supervisor"
	)
	return success_response(data=out, message=message)


@route("/profile/change-requests", methods=("GET",), summary="Profile change requests in my scope")
@frappe.whitelist()
@handle_api_errors
def list_requests(**kwargs):
	require_permission("DA Profile Change Request", "read")
	q = request_fields(
		kwargs, "status", "da_id", "woreda", "section", "mine", "no_supervisor_found", "limit", "start"
	)
	limit = int(q.get("limit") or 50)
	start = int(q.get("start") or 0)
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


@route("/profile/change-requests/<request_id>", methods=("GET",), summary="One profile change request")
@frappe.whitelist()
@handle_api_errors
def get_request(request_id: str):
	require_permission("DA Profile Change Request", "read")
	return success_response(data=svc.get_change_request(request_id))


@route("/profile/change-requests/<request_id>/cancel", methods=("POST",), summary="Cancel a waiting request")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def cancel(request_id: str):
	require_permission("DA Profile Change Request", "create")
	return success_response(data=svc.cancel_change_request(request_id), message="Change request cancelled")


@route(
	"/profile/change-requests/<request_id>/decide",
	methods=("POST",),
	summary="Level 1 (Woreda Supervisor) or level 2 (OAN Administrator) decision",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def decide(request_id: str, **kwargs):
	b = request_fields(kwargs, "decision", "note")
	out = svc.decide(request_id, decision=(b.get("decision") or "").strip(), note=b.get("note"))
	return success_response(data=out, message=svc.decision_message(out))


@route(
	"/profile/change-requests/bulk-decide",
	methods=("POST",),
	summary="The same decision on several change requests (Approvals screen)",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def bulk_decide(**kwargs):
	b = request_fields(kwargs, "ids", "decision", "note", json_keys=("ids",))
	out = svc.decide_many(b.get("ids") or [], decision=(b.get("decision") or "").strip(), note=b.get("note"))
	return success_response(data=out, message=f"{out['decided']} decided, {out['failed']} not decided")


@route(
	"/profile/change-requests/<request_id>/retry",
	methods=("POST",),
	summary="Administrator: send a request the DA Registry did not apply again",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def retry(request_id: str):
	require_permission("DA Profile Change Request", "write")
	out = svc.retry_registry_push(request_id)
	return success_response(data=out, message=svc.decision_message(out))


@route(
	"/profile/change-requests/<request_id>/sync-local",
	methods=("POST",),
	summary="Administrator: repeat the DA Services update after an applied location change",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def sync_local(request_id: str):
	require_permission("DA Profile Change Request", "write")
	out = svc.sync_local_after_transfer(request_id)
	done = out.get("local_sync_state") == "Done"
	return success_response(
		data=out, message="DA Services scope updated" if done else "The local update failed again"
	)


@route(
	"/profile/change-requests/<request_id>/close",
	methods=("POST",),
	summary="Administrator: close a request the DA Registry did not apply",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
@coded_refusals
def close(request_id: str, **kwargs):
	require_permission("DA Profile Change Request", "write")
	b = request_fields(kwargs, "note")
	out = svc.close_change_request(request_id, note=b.get("note"))
	return success_response(data=out, message=svc.decision_message(out))
