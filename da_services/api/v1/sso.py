"""v1 SSO API: one call that runs the whole Keycloak login through OAN IAM."""

from __future__ import annotations

import frappe
from frappe import _
from oan_auth_service.api.router import prefixed
from oan_auth_service.api.utils import error_response, handle_api_errors, success_response

from da_services.api.middleware import check_da_assignment
from da_services.integrations.sso import SSOLoginError, sso_login

sso_route = prefixed("/api/v1/da-services")

# kind -> (HTTP status, error code)
_ERRORS = {
	"invalid_key": (400, "INVALID_LOGIN_KEY"),
	"invalid_credentials": (401, "INVALID_CREDENTIALS"),
	"rejected": (403, "ACCOUNT_NOT_ALLOWED"),
	"unavailable": (503, "SERVICE_UNAVAILABLE"),
	"upstream": (502, "BAD_GATEWAY"),
}


@sso_route(
	"/sso/login",
	methods=("POST",),
	allow_guest=True,
	summary="Log in through an IAM (Keycloak) with username, password and login key",
)
@frappe.whitelist(allow_guest=True)
@handle_api_errors
def login():
	"""Body: ``username``, ``password``, ``key``. Returns access_token, csrf_token and the whoami user."""
	username = (frappe.form_dict.get("username") or "").strip()
	password = frappe.form_dict.get("password") or ""
	key = (frappe.form_dict.get("key") or "").strip()
	missing = [n for n, v in (("username", username), ("password", password), ("key", key)) if not v]
	if missing:
		frappe.response["http_status_code"] = 400
		return error_response(
			str(_("username, password and key are required")),
			"VALIDATION_ERROR",
			details={name: str(_("This field is required")) for name in missing},
		)

	conf = frappe.conf
	try:
		result = sso_login(
			username=username,
			password=password,
			key=key,
			return_url=conf.get("sso_return_url") or frappe.utils.get_url(),
			timeout=int(conf.get("sso_timeout_seconds") or 20),
			revocation_check=check_da_assignment,
		)
	except SSOLoginError as exc:
		status, code = _ERRORS.get(exc.kind, _ERRORS["upstream"])
		if exc.kind in ("unavailable", "upstream"):
			# The message can name internal hosts; log it, return a stable one.
			frappe.log_error(title="SSO login failed", message=f"{exc.kind}: {exc}")
			message = _("Login service is unavailable. Try again shortly.")
		else:
			message = _(str(exc))
		frappe.response["http_status_code"] = status
		return error_response(str(message), code)

	return success_response(
		data={
			"access_token": result.access_token,
			"csrf_token": result.csrf_token,
			"user": result.user,
		}
	)
