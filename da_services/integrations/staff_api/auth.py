"""IAM login, Keycloak client-credentials, CSRF, and token cache.

This is the Farmer Registry login sequence, shared with Crop Sown and
Livestock. Staff IAM user login wins when it is configured. The callback
``X-CSRF-Token`` cookie is sent back as the header. Otherwise a
client-credentials token is used with a generated matching pair. A static
API key already on the headers is left in place, and a CSRF pair is still
sent because every staff call is a POST.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from urllib.parse import urljoin

import requests

_TOKEN_SKEW_SECONDS = 30
_CALLBACK_COOKIE_MAX_AGE = 2100
_STAFF_TOKEN_CACHE: dict[tuple[str, str], dict] = {}
_STAFF_TOKEN_LOCK = threading.Lock()
_CC_TOKEN_CACHE: dict[tuple[str, str], dict] = {}
_CC_TOKEN_LOCK = threading.Lock()

_LOGIN_FORM_RE = re.compile(r'<form\b[^>]*\bid=["\']kc-form-login["\'][^>]*>', re.IGNORECASE)
_FORM_ACTION_RE = re.compile(r'\baction=["\']([^"\']+)["\']', re.IGNORECASE)
_MAX_AGE_RE = re.compile(r"Max-Age=(\d+)", re.IGNORECASE)


class StaffAuthError(Exception):
	"""Auth failure before a caller maps it onto its own registry error."""

	def __init__(self, message: str, *, status_code: int | None = None, unavailable: bool = False):
		super().__init__(message)
		self.status_code = status_code
		self.unavailable = unavailable


def _log(title: str, message: str) -> None:
	try:
		import frappe
	except ImportError:
		return
	frappe.log_error(title=title, message=message)


def _set_cookie_chunks(response: requests.Response) -> list[str]:
	raw_headers = getattr(getattr(response, "raw", None), "headers", None)
	if raw_headers is not None and hasattr(raw_headers, "getlist"):
		return [chunk for chunk in raw_headers.getlist("Set-Cookie") if chunk]
	single = response.headers.get("Set-Cookie")
	return [single] if single else []


def _cookie_value(response: requests.Response, name: str) -> str | None:
	"""Last Set-Cookie value for name. Also checks the parsed cookie jar."""
	pattern = re.compile(rf"^{re.escape(name)}=([^;]*)", re.IGNORECASE)
	found = []
	for chunk in _set_cookie_chunks(response):
		match = pattern.match(chunk)
		if match:
			found.append(match.group(1).strip().strip('"'))
	if found and found[-1]:
		return found[-1]
	cookie = response.cookies.get(name)
	if cookie:
		value = str(cookie).strip().strip('"')
		return value or None
	return None


def _cookie_max_age(response: requests.Response, name: str) -> int | None:
	pattern = re.compile(rf"^{re.escape(name)}=", re.IGNORECASE)
	for chunk in _set_cookie_chunks(response):
		if pattern.match(chunk):
			match = _MAX_AGE_RE.search(chunk)
			if match:
				return int(match.group(1))
	return None


def _token_still_valid(entry: dict, *, require_csrf: bool = False) -> bool:
	if not entry.get("token") or entry.get("expires_at", 0) <= time.time() + _TOKEN_SKEW_SECONDS:
		return False
	if require_csrf and not entry.get("csrf"):
		return False
	return True


def _jwt_expires_at(token: str) -> float | None:
	"""Read exp. The Staff API checks the signature."""
	try:
		payload = token.split(".")[1]
		payload += "=" * (-len(payload) % 4)
		claims = json.loads(base64.urlsafe_b64decode(payload))
		return float(claims["exp"])
	except IndexError, KeyError, ValueError, json.JSONDecodeError:
		return None


def _keycloak_login_action(html: str, realm_url: str, client_error: type[Exception]) -> str:
	"""Pull the credential form action out of the Keycloak login page."""
	form = _LOGIN_FORM_RE.search(html or "")
	if not form:
		raise client_error("Keycloak did not return the staff login form")
	action = _FORM_ACTION_RE.search(form.group(0))
	url = action.group(1).replace("&amp;", "&") if action else ""
	expected = realm_url.rstrip("/") + "/login-actions/"
	if not url.startswith(expected):
		raise client_error("Keycloak login form does not post to the configured staff realm")
	return url


def _require_registry_role(token: str, client_id: str, client_error: type[Exception]) -> None:
	"""The collection requires resource_access roles for the selected portal client."""
	try:
		payload = token.split(".")[1]
		payload += "=" * (-len(payload) % 4)
		claims = json.loads(base64.urlsafe_b64decode(payload))
	except (IndexError, ValueError, json.JSONDecodeError) as exc:
		raise client_error("Staff access token was not a JWT", status_code=401) from exc
	roles = ((claims.get("resource_access") or {}).get(client_id) or {}).get("roles")
	if not isinstance(roles, list) or not roles:
		raise client_error(f"Staff user has no {client_id} role", status_code=403)


def staff_session(
	keycloak: dict | None,
	timeout: float,
	*,
	client_error: type[Exception],
	server_error: type[Exception],
	log_title: str,
	registry_client_id: str | None = None,
) -> tuple[str | None, str | None]:
	"""Return (access token, csrf) from the IAM callback, cached per worker.

	Returns (None, None) when staff login is not configured.
	"""
	kc = keycloak or {}
	iam = str(kc.get("iam_base_url") or "").rstrip("/")
	realm = str(kc.get("kc_realm_url") or "").rstrip("/")
	username = kc.get("username")
	password = kc.get("password")
	if not (iam and realm and username and password):
		return None, None

	cache_key = (iam, str(username))
	cached = _STAFF_TOKEN_CACHE.get(cache_key) or {}
	if _token_still_valid(cached, require_csrf=True):
		if registry_client_id:
			_require_registry_role(cached["token"], registry_client_id, client_error)
		return cached["token"], cached["csrf"]

	with _STAFF_TOKEN_LOCK:
		cached = _STAFF_TOKEN_CACHE.get(cache_key) or {}
		if _token_still_valid(cached, require_csrf=True):
			if registry_client_id:
				_require_registry_role(cached["token"], registry_client_id, client_error)
			return cached["token"], cached["csrf"]

		token, csrf, expires_at = _login_via_iam(
			iam,
			realm,
			str(username),
			str(password),
			str(kc.get("login_provider_id") or "1"),
			timeout,
			client_error=client_error,
			server_error=server_error,
			log_title=log_title,
		)
		if registry_client_id:
			_require_registry_role(token, registry_client_id, client_error)
		_STAFF_TOKEN_CACHE[cache_key] = {"token": token, "csrf": csrf, "expires_at": expires_at}
		return token, csrf


def _login_via_iam(
	iam: str,
	realm: str,
	username: str,
	password: str,
	provider_id: str,
	timeout: float,
	*,
	client_error: type[Exception],
	server_error: type[Exception],
	log_title: str,
) -> tuple[str, str, float]:
	"""Run the staff login and return (X-Access-Token, X-CSRF-Token, expires_at).

	01 POST {iam}/auth/start_authentication_transaction -> authorization URL
	02 GET  that URL -> Keycloak login form action
	03 POST the form (no redirect) -> location is the IAM callback URL
	04 GET  the callback (no redirect) -> Set-Cookie X-Access-Token and X-CSRF-Token
	"""
	session = requests.Session()
	try:
		return _exchange_staff_login(
			session,
			iam,
			realm,
			username,
			password,
			provider_id,
			timeout,
			client_error=client_error,
			server_error=server_error,
			log_title=log_title,
		)
	finally:
		session.close()


def _exchange_staff_login(
	session: requests.Session,
	iam: str,
	realm: str,
	username: str,
	password: str,
	provider_id: str,
	timeout: float,
	*,
	client_error: type[Exception],
	server_error: type[Exception],
	log_title: str,
) -> tuple[str, str, float]:
	try:
		started = session.post(
			f"{iam}/auth/start_authentication_transaction",
			params={"id": provider_id, "redirect_uri": "/en"},
			timeout=timeout,
		)
	except requests.RequestException as exc:
		_log(log_title, f"IAM login start failed: {exc!s}")
		raise server_error("Could not reach IAM to start staff login") from exc

	if started.status_code != 200:
		raise client_error(
			f"IAM did not start staff login (HTTP {started.status_code})",
			status_code=started.status_code,
		)
	try:
		body = started.json()
	except ValueError as exc:
		raise server_error("IAM login response was not JSON") from exc
	redirect_url = body.get("redirectUrl") if isinstance(body, dict) else ""

	if not str(redirect_url).startswith(realm + "/protocol/openid-connect/auth?"):
		raise client_error("IAM authorization URL is not on the configured staff realm")

	try:
		form_page = session.get(redirect_url + "&prompt=login", timeout=timeout)
	except requests.RequestException as exc:
		_log(log_title, f"Keycloak login page failed: {exc!s}")
		raise server_error("Could not open the Keycloak staff login page") from exc

	if form_page.status_code != 200:
		raise client_error(
			f"Keycloak login page failed (HTTP {form_page.status_code})",
			status_code=form_page.status_code,
		)

	try:
		signed_in = session.post(
			_keycloak_login_action(form_page.text, realm, client_error),
			data={"username": username, "password": password, "credentialId": ""},
			timeout=timeout,
			allow_redirects=False,
		)
	except requests.RequestException as exc:
		_log(log_title, f"Keycloak sign-in failed: {exc!s}")
		raise server_error("Could not submit the staff username and password") from exc

	if signed_in.status_code not in (302, 303):
		raise client_error(
			f"Keycloak did not accept the staff user (HTTP {signed_in.status_code})",
			status_code=signed_in.status_code,
		)

	callback = urljoin(signed_in.url, signed_in.headers.get("Location") or "")
	if not callback.startswith(iam + "/auth/callback?"):
		raise client_error("Keycloak did not redirect back to the IAM callback")

	try:
		exchanged = session.get(callback, timeout=timeout, allow_redirects=False)
	except requests.RequestException as exc:
		_log(log_title, f"IAM callback failed: {exc!s}")
		raise server_error("Could not complete the IAM callback") from exc

	if exchanged.status_code not in (302, 303, 307):
		raise client_error(
			f"IAM callback did not complete (HTTP {exchanged.status_code})",
			status_code=exchanged.status_code,
		)

	token = _cookie_value(exchanged, "X-Access-Token")
	csrf = _cookie_value(exchanged, "X-CSRF-Token")
	if not token:
		raise server_error("IAM callback did not set an X-Access-Token cookie")
	if not csrf:
		raise server_error("IAM callback did not set an X-CSRF-Token cookie")

	expires_at = _jwt_expires_at(token)
	if expires_at is None:
		max_age = _cookie_max_age(exchanged, "X-Access-Token") or _cookie_max_age(exchanged, "X-CSRF-Token")
		expires_at = time.time() + (max_age if max_age is not None else _CALLBACK_COOKIE_MAX_AGE)
	return token, csrf, expires_at


def client_credentials_token(
	keycloak: dict | None,
	timeout: float,
	*,
	client_error: type[Exception],
	server_error: type[Exception],
	log_title: str,
) -> str | None:
	"""Return a cached client-credentials token, or None when that grant is not configured."""
	kc = keycloak or {}
	token_url = kc.get("token_url")
	client_id = kc.get("client_id")
	client_secret = kc.get("client_secret")
	if not (token_url and client_id and client_secret):
		return None

	cache_key = (str(token_url), str(client_id))
	secret_mark = hashlib.sha256(str(client_secret).encode()).hexdigest()
	cached = _CC_TOKEN_CACHE.get(cache_key) or {}
	if cached.get("secret_mark") == secret_mark and _token_still_valid(cached):
		return cached["token"]

	with _CC_TOKEN_LOCK:
		cached = _CC_TOKEN_CACHE.get(cache_key) or {}
		if cached.get("secret_mark") == secret_mark and _token_still_valid(cached):
			return cached["token"]

		try:
			resp = requests.post(
				token_url,
				data={
					"grant_type": "client_credentials",
					"client_id": client_id,
					"client_secret": client_secret,
				},
				timeout=timeout,
			)
		except requests.RequestException as exc:
			_log(log_title, f"Keycloak unreachable at {token_url}: {exc!s}")
			raise server_error("Could not reach Keycloak for a registry token") from exc

		if resp.status_code >= 400:
			_log(
				log_title,
				f"Keycloak token request failed: HTTP {resp.status_code} {token_url}",
			)
			exc_type = client_error if resp.status_code < 500 else server_error
			raise exc_type(
				f"Keycloak refused the token request (HTTP {resp.status_code})",
				status_code=resp.status_code,
			)

		try:
			data = resp.json()
		except ValueError as exc:
			raise server_error("Keycloak token response was not JSON") from exc
		token = data.get("access_token") if isinstance(data, dict) else None
		if not token:
			raise server_error("Keycloak response had no access_token")

		try:
			expires_in = int(data.get("expires_in", 300))
		except TypeError, ValueError:
			expires_in = 300
		_CC_TOKEN_CACHE[cache_key] = {
			"token": token,
			"secret_mark": secret_mark,
			"expires_at": time.time() + max(expires_in, 0),
		}
		return token


def auth_and_csrf_headers(
	*,
	keycloak: dict | None,
	timeout: float,
	headers: dict,
	client_error: type[Exception],
	server_error: type[Exception],
	log_title: str,
	registry_client_id: str | None = None,
) -> tuple[dict, dict]:
	"""Headers and cookies for one staff POST.

	Staff-user IAM login supplies the callback token and CSRF cookie.
	Otherwise a client-credentials token is used with a generated CSRF pair.
	A static API key already on ``headers`` is the last fallback.
	"""
	out = dict(headers)
	token, csrf = staff_session(
		keycloak,
		timeout,
		client_error=client_error,
		server_error=server_error,
		log_title=log_title,
		registry_client_id=registry_client_id,
	)
	if not token:
		token = client_credentials_token(
			keycloak,
			timeout,
			client_error=client_error,
			server_error=server_error,
			log_title=log_title,
		)
		csrf = None
	if token:
		out["Authorization"] = f"Bearer {token}"

	if not csrf:
		csrf = secrets.token_urlsafe(24)
	out["X-CSRF-Token"] = csrf
	return out, {"X-CSRF-Token": csrf}


def apply_bearer_and_csrf(headers: dict, token: str | None) -> tuple[dict, dict]:
	"""Set the bearer when we have one, and a matching CSRF header and cookie.

	``token`` None leaves an Authorization header that is already on ``headers``
	unchanged. Crop Sown uses this for an injected test token.
	"""
	if token:
		headers["Authorization"] = f"Bearer {token}"
	csrf = secrets.token_urlsafe(24)
	headers["X-CSRF-Token"] = csrf
	return headers, {"X-CSRF-Token": csrf}


def keycloak_from_conf(own: dict | None, shared: dict | None) -> dict:
	"""Prefer a block that can run the staff user login, then client credentials."""
	shared = shared or {}
	own = own or {}
	if _has_user_login(own):
		return own
	if _has_user_login(shared):
		return shared
	if own.get("token_url") and own.get("client_id") and own.get("client_secret"):
		return own
	return shared


def _has_user_login(keycloak: dict) -> bool:
	return all(
		keycloak.get(key)
		for key in ("username", "password", "iam_base_url", "kc_realm_url", "login_provider_id")
	)
