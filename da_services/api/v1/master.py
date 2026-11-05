"""v1 Master Data API: one call for country / region / zone / woreda / kebele lists.

GET  /api/v1/da-services/get_master_data?level=&parent=&q=

The Master Data URL is ``master_data_domain`` of DA Services Settings. The caller's own
``Authorization`` bearer and ``X-CSRF-Token`` are forwarded to it.
"""

from __future__ import annotations

import frappe
from frappe import _
from oan_auth_service.api.utils import error_response, handle_api_errors, success_response

from da_services.api.router import route
from da_services.api.v1.common import request_fields
from da_services.integrations.master import LEVELS, MasterDataClient, MasterDataError
from da_services.integrations.master.client import base_url_from_domain
from da_services.services import constants as C
from da_services.utils.permissions import require_roles

# kind -> (HTTP status, error code)
_ERRORS = {
	"config": (500, "MASTER_DATA_NOT_CONFIGURED"),
	"unauthorized": (401, "AUTHENTICATION_ERROR"),
	"unavailable": (503, "SERVICE_UNAVAILABLE"),
	"rejected": (502, "BAD_GATEWAY"),
}


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
	"""Query: ``level`` (country, region, zone, woreda, kebele), optional ``parent`` (code of the
	level above) and ``q`` (search text)."""
	require_roles(*C.DA_SERVICES_ROLES, *C.UNRESTRICTED_ROLES)
	b = request_fields(kwargs, "level", "parent", "q")
	level = (b.get("level") or "").strip().lower()
	if level not in LEVELS:
		frappe.throw(_("level must be one of: {0}.").format(", ".join(LEVELS)), frappe.ValidationError)
	parent = (b.get("parent") or "").strip() or None
	if parent and level == "country":
		frappe.throw(_("A country has no parent."), frappe.ValidationError)
	search = (b.get("q") or "").strip()[:80]
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
		rows = client.get_master_data(level, parent, search)
	except MasterDataError as exc:
		status, code = _ERRORS.get(exc.kind, _ERRORS["rejected"])
		# The message can name internal hosts; log it, return a stable one.
		frappe.log_error(title="Master Data request failed", message=f"{exc.kind}: {exc}")
		frappe.response["http_status_code"] = status
		return error_response(str(_(_MESSAGES.get(exc.kind, _MESSAGES["rejected"]))), code)

	return success_response(data=rows, meta={"level": level, "parent": parent, "total": len(rows)})


_MESSAGES = {
	"config": "Master Data is not configured.",
	"unauthorized": "Master Data refused your token. Sign in again.",
	"unavailable": "Master Data is unavailable. Try again shortly.",
	"rejected": "Master Data could not complete the request.",
}
