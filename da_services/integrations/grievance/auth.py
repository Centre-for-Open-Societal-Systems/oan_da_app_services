"""Development Agent login, following the Postman folder K1-K5.

One session carries the Keycloak cookies from the login form (K2) into the
credential post (K3). The callback (K4) sets the access-token cookie named by
K1's ``cookie_prefix``. Later grievance calls use that value as a bearer token.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from urllib.parse import urljoin

import requests

from .mapper import ASSISTED_ROLE, unwrap_data

_FORM_ID = re.compile(
	r"<form\b[^>]*\bid=[\"']kc-form-login[\"'][^>]*action=[\"']([^\"']+)[\"']",
	re.IGNORECASE,
)
_FORM_ACTION = re.compile(r"action=[\"']([^\"']+)[\"']", re.IGNORECASE)
_COOKIE_VALUE = re.compile(r"^[^=]+=([^;]*)")


class AgentLoginError(Exception):
	def __init__(self, message: str, *, unavailable: bool = False):
		super().__init__(message)
		self.unavailable = unavailable


def login_form_action(html: str, page_url: str) -> str:
	"""Action URL of the Keycloak login form."""
	match = _FORM_ID.search(html or "") or _FORM_ACTION.search(html or "")
	if not match:
		raise AgentLoginError("Keycloak login form had no action URL")
	action = match.group(1).replace("&amp;", "&")
	return urljoin(page_url, action)


def access_token_from_cookies(cookie_headers: list[str], prefix: str) -> str | None:
	"""Last access-token cookie. Agent realm cookies are ``{prefix}X-Access-Token``."""
	names = []
	if prefix:
		names.append(f"{prefix}X-Access-Token")
	names.append("X-Access-Token")
	found = None
	for header in cookie_headers:
		header = (header or "").strip()
		for name in names:
			if header.lower().startswith(name.lower() + "="):
				value = _COOKIE_VALUE.match(header)
				token = value.group(1).strip().strip('"') if value else ""
				if token:
					found = token
	return found


def set_cookie_headers(response) -> list[str]:
	raw_headers = getattr(getattr(response, "raw", None), "headers", None)
	if raw_headers is not None and hasattr(raw_headers, "getlist"):
		values = list(raw_headers.getlist("Set-Cookie"))
		if values:
			return values
	direct = getattr(response, "set_cookies", None)
	if direct:
		return list(direct)
	value = response.headers.get("Set-Cookie") if getattr(response, "headers", None) else None
	return [value] if value else []


def token_expiry(token: str) -> float:
	"""Unix expiry from the JWT ``exp`` claim. Unverified: it only sizes the cache."""
	try:
		payload = token.split(".")[1]
		padded = payload + "=" * (-len(payload) % 4)
		claims = json.loads(base64.urlsafe_b64decode(padded))
		exp = claims.get("exp")
		if isinstance(exp, int | float) and exp:
			return float(exp)
	except IndexError, ValueError, json.JSONDecodeError:
		pass
	return time.time() + 300


def _json(response) -> dict:
	try:
		body = response.json()
	except ValueError:
		return {}
	return body if isinstance(body, dict) else {}


def assert_assisted_role(session, *, base_url: str, timeout: int, token: str) -> None:
	"""The bearer must be a Grievance Assisted Submitter. Anything else cannot file a case."""
	try:
		who = session.get(
			f"{base_url}/v1/auth/me",
			headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
			timeout=timeout,
		)
	except requests.RequestException as exc:
		raise AgentLoginError("Grievance whoami is unreachable", unavailable=True) from exc
	if who.status_code >= 500:
		raise AgentLoginError(f"Whoami failed: HTTP {who.status_code}", unavailable=True)
	if who.status_code >= 400 or ASSISTED_ROLE not in (who.text or ""):
		raise AgentLoginError("Development Agent token is missing the Grievance Assisted Submitter role")


def login_local(
	session,
	*,
	base_url: str,
	timeout: int,
	username: str,
	password: str,
) -> str:
	"""Password login for a grievance site whose only provider is ``local``."""
	base_url = base_url.rstrip("/")
	try:
		signed_in = session.post(
			f"{base_url}/v1/auth/login",
			json={"usr": username, "pwd": password},
			headers={"Accept": "application/json", "Content-Type": "application/json"},
			timeout=timeout,
		)
	except requests.RequestException as exc:
		raise AgentLoginError("Grievance login is unreachable", unavailable=True) from exc
	if signed_in.status_code >= 500:
		raise AgentLoginError(f"Grievance login failed: HTTP {signed_in.status_code}", unavailable=True)
	if signed_in.status_code >= 400:
		raise AgentLoginError(f"Grievance login was refused: HTTP {signed_in.status_code}")
	data = unwrap_data(_json(signed_in))
	token = data.get("access_token") if isinstance(data, dict) else None
	if not isinstance(token, str) or not token:
		raise AgentLoginError("Grievance login did not return an access token")
	assert_assisted_role(session, base_url=base_url, timeout=timeout, token=token)
	return token


def login_agent(
	session,
	*,
	base_url: str,
	timeout: int,
	login_key: str,
	return_url: str,
	username: str,
	password: str,
) -> str:
	"""Run K1-K5 and return the Development Agent access token."""
	base_url = base_url.rstrip("/")
	try:
		started = session.post(
			f"{base_url}/v1/auth/iam/login",
			json={"key": login_key, "provider": login_key, "return_url": return_url},
			headers={"Accept": "application/json", "Content-Type": "application/json"},
			timeout=timeout,
		)
	except requests.RequestException as exc:
		raise AgentLoginError("Grievance IAM login is unreachable", unavailable=True) from exc

	if started.status_code >= 500:
		raise AgentLoginError(f"IAM login failed: HTTP {started.status_code}", unavailable=True)
	if started.status_code >= 400:
		raise AgentLoginError(f"IAM login refused: HTTP {started.status_code}")

	started_data = unwrap_data(_json(started))
	if not isinstance(started_data, dict) or not started_data.get("redirect_url"):
		raise AgentLoginError("IAM login did not return redirect_url")
	redirect_url = started_data["redirect_url"]
	prefix = started_data.get("cookie_prefix") or ""

	try:
		form_page = session.get(redirect_url, timeout=timeout, allow_redirects=True)
	except requests.RequestException as exc:
		raise AgentLoginError("Keycloak login page is unreachable", unavailable=True) from exc
	if form_page.status_code >= 400:
		raise AgentLoginError(f"Keycloak login page failed: HTTP {form_page.status_code}", unavailable=True)
	action = login_form_action(form_page.text or "", form_page.url or redirect_url)

	try:
		signed_in = session.post(
			action,
			data={"username": username, "password": password, "credentialId": ""},
			headers={"Content-Type": "application/x-www-form-urlencoded"},
			timeout=timeout,
			allow_redirects=False,
		)
	except requests.RequestException as exc:
		raise AgentLoginError("Keycloak sign-in is unreachable", unavailable=True) from exc
	if signed_in.status_code not in (302, 303):
		raise AgentLoginError(f"Keycloak sign-in did not redirect: HTTP {signed_in.status_code}")
	callback = signed_in.headers.get("Location") or ""
	if not callback:
		raise AgentLoginError("Keycloak sign-in returned no callback location")

	try:
		exchanged = session.get(callback, timeout=timeout, allow_redirects=False)
	except requests.RequestException as exc:
		raise AgentLoginError("IAM callback is unreachable", unavailable=True) from exc
	if exchanged.status_code not in (200, 302, 303, 307):
		raise AgentLoginError(f"IAM callback failed: HTTP {exchanged.status_code}", unavailable=True)

	token = access_token_from_cookies(set_cookie_headers(exchanged), prefix)
	if not token:
		raise AgentLoginError("IAM callback did not set an access token")
	assert_assisted_role(session, base_url=base_url, timeout=timeout, token=token)
	return token


class AgentTokenProvider:
	"""Cached bearer token for the configured Development Agent."""

	def __init__(
		self,
		*,
		base_url: str,
		timeout: int = 20,
		login_key: str = "agent",
		return_url: str = "http://localhost:3000",
		username: str = "",
		password: str = "",
		static_token: str = "",
	):
		self.base_url = base_url
		self.timeout = timeout
		self.login_key = login_key
		self.return_url = return_url
		self.username = username
		self.password = password
		self.static_token = static_token
		self._cache: dict = {}
		self._lock = threading.Lock()

	def get_token(self) -> str:
		if self._fresh():
			return self._cache["token"]
		with self._lock:
			if self._fresh():
				return self._cache["token"]
			if self.username and self.password and self.login_key == "local":
				token = login_local(
					requests.Session(),
					base_url=self.base_url,
					timeout=self.timeout,
					username=self.username,
					password=self.password,
				)
			elif self.username and self.password:
				token = login_agent(
					requests.Session(),
					base_url=self.base_url,
					timeout=self.timeout,
					login_key=self.login_key,
					return_url=self.return_url,
					username=self.username,
					password=self.password,
				)
			elif self.static_token:
				token = self.static_token
			else:
				raise AgentLoginError("grievance_iam username/password or grievance_api_token is required")
			self._cache = {"token": token, "expires_at": token_expiry(token)}
			return token

	def _fresh(self) -> bool:
		return bool(self._cache.get("token")) and self._cache.get("expires_at", 0) > time.time() + 30
