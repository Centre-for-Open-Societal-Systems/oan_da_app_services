"""v1 Credit page reads through DA Services (read-only wrapper for A2C).

GET /api/v1/da-services/credit/applications/summary
GET /api/v1/da-services/credit/applications?status=&page=
GET /api/v1/da-services/credit/applications/{application_id}/documents

The A2C service owns the Credit data and its response schema. DA Services
validates the caller's input, forwards the caller's own Bearer token for this
one request, and returns the upstream JSON unchanged under the standard
success envelope. Nothing is stored, cached or logged from the response.

Only the documented query values are forwarded: ``status`` and ``page``.
``application_id`` is validated as one URL path segment. There is no Farmer-ID
filter and no invented status enum.

Authorization is delegated to A2C using the caller's forwarded Bearer token.
"""

from __future__ import annotations

import frappe
from frappe import _
from oan_auth_service.api.utils import error_response, handle_api_errors, success_response

from da_services.api.router import route
from da_services.integrations.a2c import (
	A2CConfigurationError,
	A2CError,
	A2CNotFound,
	A2CUnavailable,
	A2CValidationError,
)
from da_services.services import credit as svc


def _query(*names: str) -> dict:
	return {name: (frappe.local.form_dict or {}).get(name) for name in names}


def _failure(status: int, message: str, code: str):
	frappe.response["http_status_code"] = status
	return error_response(message, code)


def _a2c_failure(exc: A2CError):
	"""Map a client failure to a stable envelope without echoing upstream detail.

	401/403 from A2C is not proof that the caller's DA Services session is
	invalid: the forwarded token may be valid here but refused there. It is
	therefore a Bad Gateway for this API, not a DA sign-out.
	"""
	if isinstance(exc, A2CValidationError):
		return _failure(400, str(exc), "VALIDATION_ERROR")
	if isinstance(exc, A2CNotFound):
		return _failure(404, _("A2C application not found."), "NOT_FOUND")
	if isinstance(exc, (A2CConfigurationError, A2CUnavailable)):
		return _failure(
			503,
			_("A2C is unavailable. Try again shortly."),
			"SERVICE_UNAVAILABLE",
		)
	# Rejected (401/403/other 4xx) and malformed upstream responses.
	return _failure(502, _("A2C could not complete the request."), "BAD_GATEWAY")


@route(
	"/credit/applications/summary",
	methods=("GET",),
	summary="Loan application summary for the Credit page",
)
@frappe.whitelist()
@handle_api_errors
def summary(**_kwargs):
	"""Read ``GET /v1/loan-applications/summary`` and return it unchanged."""
	try:
		data = svc.get_loan_application_summary()
	except A2CError as exc:
		return _a2c_failure(exc)
	return success_response(data=data)


@route(
	"/credit/applications",
	methods=("GET",),
	summary="List loan applications for the Credit page",
)
@frappe.whitelist()
@handle_api_errors
def list_applications(**_kwargs):
	"""Read ``GET /v1/loan-applications?status=&page=`` and return it unchanged.

	Query: ``status`` (optional, forwarded as sent) and ``page`` (optional
	positive integer, default 1). No other parameter is forwarded.
	"""
	q = _query("status", "page")
	try:
		data = svc.list_loan_applications(status=q["status"], page=q["page"])
	except A2CError as exc:
		return _a2c_failure(exc)
	return success_response(data=data)


@route(
	"/credit/applications/<application_id>/documents",
	methods=("GET",),
	summary="Documents of one loan application",
)
@frappe.whitelist()
@handle_api_errors
def documents(application_id: str, **_kwargs):
	"""Read ``GET /v1/loan-applications/{application_id}/documents`` unchanged."""
	try:
		data = svc.get_loan_application_documents(application_id)
	except A2CError as exc:
		return _a2c_failure(exc)
	return success_response(data=data)
