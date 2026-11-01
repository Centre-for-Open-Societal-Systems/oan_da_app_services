"""Shared staff auth: the Postman IAM login sequence, CSRF, and Livestock auth.

No test in this module contacts IAM, Keycloak, or a staff host.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from da_services.integrations.farmer_registry.client import (
	FarmerRegistryClientError,
	FarmerRegistryServerError,
)
from da_services.integrations.livestock_registry.auth import livestock_auth_headers
from da_services.integrations.staff_api.auth import _STAFF_TOKEN_CACHE, auth_and_csrf_headers


class _HeaderMap:
	def __init__(self, location: str = ""):
		self._location = location

	def get(self, name, default=None):
		if name == "Location":
			return self._location
		return default


class _RawHeaders:
	def __init__(self, cookies: list[str]):
		self._cookies = cookies

	def getlist(self, name):
		if name == "Set-Cookie":
			return list(self._cookies)
		return []


class _Response:
	def __init__(self, status, body=None, text="", url="", location="", cookies=None):
		self.status_code = status
		self._body = body or {}
		self.text = text
		self.url = url
		self.headers = _HeaderMap(location)
		self.cookies = {}
		self.raw = type("Raw", (), {"headers": _RawHeaders(cookies or [])})()

	def json(self):
		return self._body


class _Session:
	def __init__(self, realm: str, iam: str):
		self.realm = realm
		self.iam = iam
		self.calls: list[tuple[str, str]] = []

	def post(self, url, **kwargs):
		self.calls.append(("POST", url))
		if url.endswith("/auth/start_authentication_transaction"):
			if kwargs.get("params") != {"id": "1", "redirect_uri": "/en"}:
				raise AssertionError(kwargs.get("params"))
			return _Response(200, {"redirectUrl": f"{self.realm}/protocol/openid-connect/auth?client=1"})
		if "/login-actions/" in url:
			if kwargs.get("data", {}).get("username") != "agent":
				raise AssertionError("username")
			if kwargs.get("allow_redirects") is not False:
				raise AssertionError("redirect")
			return _Response(302, url=url, location=f"{self.iam}/auth/callback?code=1")
		raise AssertionError(url)

	def get(self, url, **kwargs):
		self.calls.append(("GET", url))
		if "prompt=login" in url:
			form = f'<form id="kc-form-login" action="{self.realm}/login-actions/authenticate?session=1">'
			return _Response(200, text=form, url=url)
		if url.startswith(f"{self.iam}/auth/callback?"):
			if kwargs.get("allow_redirects") is not False:
				raise AssertionError("callback redirect")
			return _Response(
				302,
				cookies=[
					'X-Access-Token=""; Path=/',
					"X-Access-Token=aaa.bbb.ccc; Path=/; Max-Age=120",
					"X-CSRF-Token=iam-csrf; Path=/",
				],
			)
		raise AssertionError(url)

	def close(self):
		return None


class TestPostmanStaffLogin(unittest.TestCase):
	def test_iam_login_follows_the_collection_and_reuses_the_csrf_cookie(self):
		realm = "https://keycloak.test/realms/staff"
		iam = "https://iam.test"
		session = _Session(realm, iam)
		_STAFF_TOKEN_CACHE.clear()
		keycloak = {
			"iam_base_url": iam,
			"kc_realm_url": realm,
			"username": "agent",
			"password": "secret",
			"login_provider_id": "1",
		}
		with patch("da_services.integrations.staff_api.auth.requests.Session", return_value=session):
			headers, cookies = auth_and_csrf_headers(
				keycloak=keycloak,
				timeout=5,
				headers={"Accept": "application/json"},
				client_error=FarmerRegistryClientError,
				server_error=FarmerRegistryServerError,
				log_title="Staff Auth Test",
			)

		self.assertEqual(
			session.calls,
			[
				("POST", f"{iam}/auth/start_authentication_transaction"),
				("GET", f"{realm}/protocol/openid-connect/auth?client=1&prompt=login"),
				("POST", f"{realm}/login-actions/authenticate?session=1"),
				("GET", f"{iam}/auth/callback?code=1"),
			],
		)
		self.assertEqual(headers["Authorization"], "Bearer aaa.bbb.ccc")
		self.assertEqual(headers["X-CSRF-Token"], "iam-csrf")
		self.assertEqual(cookies["X-CSRF-Token"], headers["X-CSRF-Token"])
		self.assertFalse(any("staff-" in url for _method, url in session.calls))


class TestLivestockAuth(unittest.TestCase):
	def test_livestock_auth_does_not_call_the_livestock_host(self):
		def session(keycloak, timeout, **_kwargs):
			return "livestock-user", "livestock-csrf"

		with patch("da_services.integrations.staff_api.auth.staff_session", session):
			with patch("da_services.integrations.staff_api.auth.requests.post") as post:
				headers, cookies = livestock_auth_headers()

		post.assert_not_called()
		self.assertEqual(headers["Authorization"], "Bearer livestock-user")
		self.assertEqual(headers["X-CSRF-Token"], cookies["X-CSRF-Token"])
		self.assertEqual(cookies["X-CSRF-Token"], "livestock-csrf")


if __name__ == "__main__":
	unittest.main()
