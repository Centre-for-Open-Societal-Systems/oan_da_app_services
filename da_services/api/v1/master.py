"""v1 Master Data API: one call for geography lists and code lists.

GET  /api/v1/da-services/get_master_data?data_type=&field_name=&parent=&search=&current_page=&page_size=

data_type=geo        field_name = country | region | zone | woreda | kebele
data_type=attribute  field_name = an attribute id, e.g. DOCUMENT_PURPOSE
Both answer with the same row shape.

The Master Data URL is ``master_data_domain`` of DA Services Settings. The caller's own
``Authorization`` bearer and ``X-CSRF-Token`` are forwarded to it.
"""

from __future__ import annotations

import re

import frappe
from frappe import _
from oan_auth_service.api.utils import error_response, handle_api_errors, success_response

from da_services.api.router import route
from da_services.api.v1.common import request_fields
from da_services.integrations.master import (
	DATA_TYPES,
	DEFAULT_PAGE_SIZE,
	LEVELS,
	MAX_PAGE_SIZE,
	MasterDataClient,
	MasterDataError,
)
from da_services.integrations.master.client import base_url_from_domain
from da_services.services import constants as C
from da_services.utils.permissions import require_roles

_ATTRIBUTE_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")

# kind -> (HTTP status, error code)
_ERRORS = {
	"config": (500, "MASTER_DATA_NOT_CONFIGURED"),
	"unauthorized": (401, "AUTHENTICATION_ERROR"),
	"unavailable": (503, "SERVICE_UNAVAILABLE"),
	"rejected": (502, "BAD_GATEWAY"),
}


def _positive_int(value, name: str, *, default: int, maximum: int | None = None) -> int:
	if value in (None, ""):
		return default
	try:
		number = int(value)
	except TypeError, ValueError:
		number = 0
	if number < 1 or (maximum and number > maximum):
		limit = f" and at most {maximum}" if maximum else ""
		frappe.throw(_("{0} must be a whole number of 1{1}.").format(name, limit), frappe.ValidationError)
	return number


def _forwarded_credentials() -> tuple[str, str]:
	"""The caller's bearer token and CSRF token, from the request headers."""
	authorization = (frappe.get_request_header("Authorization") or "").strip()
	scheme, _sep, token = authorization.partition(" ")
	csrf = (frappe.get_request_header("X-CSRF-Token") or "").strip()
	if scheme.lower() != "bearer" or not token.strip():
		frappe.throw(_("A Bearer Authorization header is required."), frappe.AuthenticationError)
	if not csrf:
		frappe.throw(_("An X-CSRF-Token header is required."), frappe.ValidationError)
	return token.strip(), csrf


@route("/get_master_data", methods=("GET",), summary="Country, region, zone, woreda or kebele list")
@frappe.whitelist()
@handle_api_errors
def get_master_data(**kwargs):
	"""Query: ``data_type`` (geo or attribute), ``field_name`` (a level for geo, an attribute id for
	attribute), optional ``parent`` (the parent's code or id), ``search`` (search text),
	``current_page`` (default 1) and ``page_size`` (default 100, at most 1000)."""
	require_roles(*C.DA_SERVICES_ROLES, *C.UNRESTRICTED_ROLES)
	b = request_fields(kwargs, "data_type", "field_name", "parent", "search", "current_page", "page_size")
	data_type = (b.get("data_type") or "").strip().lower()
	field_name = (b.get("field_name") or "").strip()
	parent = (b.get("parent") or "").strip() or None
	if data_type not in DATA_TYPES:
		frappe.throw(
			_("data_type must be one of: {0}.").format(", ".join(DATA_TYPES)), frappe.ValidationError
		)
	if data_type == "geo":
		field_name = field_name.lower()
		if field_name not in LEVELS:
			frappe.throw(
				_("field_name must be one of: {0} for geo.").format(", ".join(LEVELS)), frappe.ValidationError
			)
		if parent and field_name == "country":
			frappe.throw(_("A country has no parent."), frappe.ValidationError)
	elif not _ATTRIBUTE_ID.fullmatch(field_name):
		frappe.throw(_("field_name must be an attribute id for attribute."), frappe.ValidationError)
	search = (b.get("search") or "").strip()[:80]
	current_page = _positive_int(b.get("current_page"), "current_page", default=1)
	page_size = _positive_int(
		b.get("page_size"), "page_size", default=DEFAULT_PAGE_SIZE, maximum=MAX_PAGE_SIZE
	)
	token, csrf = _forwarded_credentials()

	try:
		client = MasterDataClient(
			base_url=base_url_from_domain(
				frappe.db.get_single_value("DA Services Settings", "master_data_domain")
			),
			token=token,
			csrf_token=csrf,
			sender_app=frappe.conf.get("da_registry_sender_app") or "da-services",
			timeout=int(frappe.conf.get("master_data_timeout_seconds") or 20),
		)
		rows, page = client.get_master_data(data_type, field_name, parent, search, current_page, page_size)
	except MasterDataError as exc:
		status, code = _ERRORS.get(exc.kind, _ERRORS["rejected"])
		# The message can name internal hosts; log it, return a stable one.
		frappe.log_error(title="Master Data request failed", message=f"{exc.kind}: {exc}")
		frappe.response["http_status_code"] = status
		return error_response(str(_(_MESSAGES.get(exc.kind, _MESSAGES["rejected"]))), code)

	return success_response(
		data=rows,
		meta={
			"data_type": data_type,
			"field_name": field_name,
			"parent": parent,
			"search": search,
			**page,
			"total": len(rows),
		},
	)


_MESSAGES = {
	"config": "Master Data is not configured.",
	"unauthorized": "Master Data refused your token. Sign in again.",
	"unavailable": "Master Data is unavailable. Try again shortly.",
	"rejected": "Master Data could not complete the request.",
}
