"""SSO login chain. These tests do not call a live service; each step's response feeds the next."""

import unittest
from unittest.mock import MagicMock, patch

from da_services.integrations.sso import SSOLoginError, sso_login

_HTML = '<form id="kc-form-login" action="https://kc.example/login?a=1&amp;b=2"></form>'


def _resp(status=200, body=None, text="", headers=None, cookies=None, url=""):
	r = MagicMock()
	r.status_code = status
	r.json.return_value = body if body is not None else {}
	r.text = text
	r.url = url
	r.headers = headers or {}
	r.raw.headers.getlist.return_value = cookies or []
	return r


def _login(session, start=("https://kc.example/auth", ""), start_error=None):
	with (
		patch("da_services.integrations.sso.client.requests.Session", return_value=session),
		patch(
			"da_services.integrations.sso.client.auth_service.start_login",
			return_value=start,
			side_effect=start_error,
		),
		patch("da_services.integrations.sso.client.auth_service.whoami", return_value={"id": "u1"}),
	):
		return sso_login(username="u", password="p", key="agent", return_url="http://app.test")


class TestSSOLogin(unittest.TestCase):
	def _session(self, signin):
		s = MagicMock()
		s.request.side_effect = [
			_resp(text=_HTML, url="https://kc.example/auth"),
			signin,
			_resp(302, cookies=["X-CSRF-Token=csrf1; Path=/", "X-Access-Token=tok1; Path=/"]),
		]
		return s

	def test_full_chain(self):
		s = self._session(_resp(302, headers={"Location": "http://iam.test/cb"}))
		result = _login(s)
		self.assertEqual(
			(result.access_token, result.csrf_token, result.user), ("tok1", "csrf1", {"id": "u1"})
		)
		self.assertEqual(s.request.call_args_list[1].args[1], "https://kc.example/login?a=1&b=2")

	def test_cookie_prefix(self):
		s = MagicMock()
		s.request.side_effect = [
			_resp(text=_HTML, url="https://kc.example/auth"),
			_resp(302, headers={"Location": "http://iam.test/cb"}),
			_resp(302, cookies=["agent-X-CSRF-Token=c; Path=/", "agent-X-Access-Token=t; Path=/"]),
		]
		result = _login(s, start=("https://kc.example/auth", "agent-"))
		self.assertEqual((result.access_token, result.csrf_token), ("t", "c"))

	def test_wrong_password(self):
		with self.assertRaises(SSOLoginError) as ctx:
			_login(self._session(_resp(200)))
		self.assertEqual(ctx.exception.kind, "invalid_credentials")

	def test_unknown_key(self):
		with self.assertRaises(SSOLoginError) as ctx:
			_login(MagicMock(), start_error=SSOLoginError("x", kind="invalid_key"))
		self.assertEqual(ctx.exception.kind, "invalid_key")
