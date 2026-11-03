"""The two steps of the SSO login that oan_auth_service answers in this process.

Calling its Python functions rather than its HTTP routes keeps this server from
calling itself, which needs a free worker and deadlocks on a single-threaded one.
The Keycloak form and the IAM callback are other servers' endpoints and stay HTTP.
"""

from __future__ import annotations

import frappe

from .errors import SSOLoginError


def start_login(key: str, return_url: str) -> tuple[str, str]:
	"""Start login option ``key`` and return ``(redirect_url, cookie_prefix)``."""
	from oan_auth_service.api import iam_tokens
	from oan_auth_service.api.v1.auth import _enabled_iam_service

	try:
		service = _enabled_iam_service(key)
	except frappe.ValidationError as exc:
		raise SSOLoginError("Unknown or disabled login key", kind="invalid_key") from exc
	if iam_tokens.origin_of(return_url) is None:
		raise SSOLoginError("sso_return_url must be an absolute http(s) URL", kind="upstream")
	try:
		redirect_url = iam_tokens.start_login(service, return_url)
	except iam_tokens.IAMUnavailableError as exc:
		raise SSOLoginError(f"IAM login is unreachable: {exc}", kind="unavailable") from exc
	except iam_tokens.IAMBadResponseError as exc:
		raise SSOLoginError(f"IAM login answered unusably: {exc}") from exc
	return redirect_url, service.cookie_prefix or ""


def whoami(access_token: str, revocation_check=None) -> dict:
	"""``data`` of ``GET /auth/me`` for ``access_token``, checked the way the middleware checks it.

	Sets the request user for the call and puts it back to Guest afterwards.
	"""
	from oan_auth_service.api.middleware import _authenticate_token
	from oan_auth_service.api.v1.auth import get_me

	previous = frappe.session.user
	try:
		_authenticate_token(access_token, {"revocation_check": revocation_check})
		body = get_me()
	except frappe.AuthenticationError as exc:
		raise SSOLoginError("This account is not allowed to use this application", kind="rejected") from exc
	except frappe.ServiceUnavailableError as exc:
		raise SSOLoginError("Identity service unavailable", kind="unavailable") from exc
	except frappe.ValidationError as exc:
		raise SSOLoginError(f"Whoami was refused: {exc}", kind="rejected") from exc
	finally:
		frappe.set_user(previous)
	data = body.get("data") if isinstance(body, dict) else None
	if not isinstance(data, dict):
		raise SSOLoginError("Whoami returned no user")
	return data
