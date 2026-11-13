"""Scripted Keycloak login through oan_auth_service IAM, with no browser.

Steps:
  1. Start the login transaction (in-process, see ``auth_service.start_login``).
  2. GET the Keycloak authorization URL and read the login form action.
  3. POST username and password to the form; Keycloak answers 302 to the IAM callback.
  4. GET the callback; the ``X-Access-Token`` and ``X-CSRF-Token`` cookies are set.
  5. Who am I (in-process, see ``auth_service.whoami``).

Steps 2-4 are HTTP calls to Keycloak and the IAM API. Steps 1 and 5 call
oan_auth_service in this process, so this server never calls itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

import requests

from . import auth_service
from .errors import SSOLoginError

_FORM_ID = re.compile(
	r"<form\b[^>]*\bid=[\"']kc-form-login[\"'][^>]*action=[\"']([^\"']+)[\"']",
	re.IGNORECASE,
)
_FORM_ACTION = re.compile(r"action=[\"']([^\"']+)[\"']", re.IGNORECASE)
_COOKIE_VALUE = re.compile(r"^[^=]+=([^;]*)")


@dataclass
class SSOLoginResult:
	access_token: str
	csrf_token: str
	user: dict


def _json(response) -> dict:
	try:
		body = response.json()
	except ValueError:
		return {}
	return body if isinstance(body, dict) else {}


def _data(body: dict) -> dict:
	inner = body.get("data", body)
	return inner if isinstance(inner, dict) else {}


def _set_cookie_headers(response) -> list[str]:
	raw_headers = getattr(getattr(response, "raw", None), "headers", None)
	if raw_headers is not None and hasattr(raw_headers, "getlist"):
		values = list(raw_headers.getlist("Set-Cookie"))
		if values:
			return values
	value = response.headers.get("Set-Cookie")
	return [value] if value else []


def _cookie(cookie_headers: list[str], name: str, prefix: str = "") -> str | None:
	"""Last non-empty value of ``{prefix}{name}``, falling back to bare ``name``."""
	names = ([prefix + name] if prefix else []) + [name]
	found = None
	for header in cookie_headers:
		header = (header or "").strip()
		for candidate in names:
			if header.lower().startswith(candidate.lower() + "="):
				match = _COOKIE_VALUE.match(header)
				value = match.group(1).strip().strip('"') if match else ""
				if value:
					found = value
	return found


def _login_form_action(html: str, page_url: str) -> str:
	match = _FORM_ID.search(html or "") or _FORM_ACTION.search(html or "")
	if not match:
		raise SSOLoginError("Keycloak login form had no action URL")
	return urljoin(page_url, match.group(1).replace("&amp;", "&"))


def _request(session, method: str, url: str, what: str, **kwargs):
	try:
		return session.request(method, url, **kwargs)
	except requests.RequestException as exc:
		raise SSOLoginError(f"{what} is unreachable: {exc!r}", kind="unavailable") from exc


def sso_login(
	*,
	username: str,
	password: str,
	key: str,
	return_url: str,
	timeout: int = 20,
	revocation_check=None,
) -> SSOLoginResult:
	session = requests.Session()

	# 1. Start the login transaction.
	redirect_url, prefix = auth_service.start_login(key, return_url)

	# 2. Fetch the Keycloak login form.
	form_page = _request(session, "GET", redirect_url, "Keycloak login page", timeout=timeout)
	if form_page.status_code >= 400:
		raise SSOLoginError(f"Keycloak login page failed: HTTP {form_page.status_code}", kind="unavailable")
	action = _login_form_action(form_page.text, form_page.url or redirect_url)

	# 3. Post the credentials. A 200 means Keycloak re-rendered the form: wrong credentials.
	signed_in = _request(
		session,
		"POST",
		action,
		"Keycloak sign-in",
		data={"username": username, "password": password, "credentialId": ""},
		headers={"Content-Type": "application/x-www-form-urlencoded"},
		timeout=timeout,
		allow_redirects=False,
	)
	if signed_in.status_code >= 500:
		raise SSOLoginError(f"Keycloak sign-in failed: HTTP {signed_in.status_code}", kind="unavailable")
	if signed_in.status_code not in (302, 303):
		raise SSOLoginError("Invalid username or password", kind="invalid_credentials")
	callback = signed_in.headers.get("Location") or ""
	if not callback:
		raise SSOLoginError("Keycloak sign-in returned no callback location")

	# 4. Complete the IAM callback and read the cookies it sets.
	exchanged = _request(session, "GET", callback, "IAM callback", timeout=timeout, allow_redirects=False)
	if exchanged.status_code not in (200, 302, 303, 307):
		raise SSOLoginError(f"IAM callback failed: HTTP {exchanged.status_code}", kind="unavailable")
	cookies = _set_cookie_headers(exchanged)
	access_token = _cookie(cookies, "X-Access-Token", prefix)
	csrf_token = _cookie(cookies, "X-CSRF-Token", prefix)
	if not access_token:
		raise SSOLoginError("IAM callback did not set an access token")
	if not csrf_token:
		raise SSOLoginError("IAM callback did not set a CSRF token")

	# 5. Who am I.
	user = auth_service.whoami(access_token, revocation_check=revocation_check)
	return SSOLoginResult(access_token=access_token, csrf_token=csrf_token, user=user)
