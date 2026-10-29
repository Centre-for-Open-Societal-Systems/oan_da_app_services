"""Crop Sown read client: mock fixtures, mapper, staff auth, and Staff reads.

HTTP calls are stubbed. Nothing in this module contacts the live registry.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

import frappe

from da_services.api.v1.crop_sown import crop_sown_payload
from da_services.integrations.crop_registry.client import CropRegistryClientError, CropRegistryServerError
from da_services.integrations.crop_registry.http import HTTPCropRegistryClient
from da_services.integrations.crop_registry.mapper import submission_to_crop
from da_services.integrations.crop_registry.mock import FIXTURE_SUBMISSION_ID, MockCropRegistryClient
from da_services.integrations.crop_registry.schemas import CropSection, CropSubmission
from da_services.integrations.staff_api.auth import (
	_CC_TOKEN_CACHE,
	_cookie_value,
	apply_bearer_and_csrf,
	client_credentials_token,
)


def _envelope(payload, *, status="SUCCESS", error=""):
	return {
		"response_header": {
			"response_status": status,
			"response_error_message": error,
			"response_error_code": "E" if error else None,
		},
		"response_body": {"response_payload": payload},
	}


class _Resp:
	def __init__(self, status_code, body=None, headers=None, text="", url="", set_cookies=None):
		self.status_code = status_code
		self._body = body if body is not None else {}
		self.headers = headers or {}
		self.text = text or (json.dumps(self._body) if not isinstance(self._body, str) else self._body)
		self.url = url or "https://staff-crop.test/x"
		self.set_cookies = set_cookies

	def json(self):
		if isinstance(self._body, Exception):
			raise self._body
		return self._body


class TestStaffAuth(unittest.TestCase):
	def test_client_credentials_and_matching_csrf(self):
		seen = {}

		def post(url, **kwargs):
			seen["url"] = url
			seen["data"] = kwargs["data"]
			return _Resp(200, {"access_token": "staff-token", "expires_in": 300})

		_CC_TOKEN_CACHE.clear()
		with patch("da_services.integrations.staff_api.auth.requests.post", post):
			token = client_credentials_token(
				{
					"token_url": "https://keycloak.test/token",
					"client_id": "da-services",
					"client_secret": "secret",
				},
				5,
				client_error=CropRegistryClientError,
				server_error=CropRegistryServerError,
				log_title="Crop Sown Registry Auth Error",
			)

		self.assertEqual(token, "staff-token")
		self.assertEqual(seen["url"], "https://keycloak.test/token")
		self.assertEqual(seen["data"]["grant_type"], "client_credentials")
		self.assertEqual(seen["data"]["client_id"], "da-services")
		headers, cookies = apply_bearer_and_csrf({}, token)
		self.assertEqual(headers["Authorization"], "Bearer staff-token")
		self.assertEqual(headers["X-CSRF-Token"], cookies["X-CSRF-Token"])

	def test_keycloak_rejection_is_not_retried_as_success(self):
		def post(url, **kwargs):
			return _Resp(401, text="denied")

		_CC_TOKEN_CACHE.clear()
		with patch("da_services.integrations.staff_api.auth.requests.post", post):
			with self.assertRaises(CropRegistryClientError) as caught:
				client_credentials_token(
					{
						"token_url": "https://keycloak.test/token",
						"client_id": "da-services",
						"client_secret": "secret",
					},
					5,
					client_error=CropRegistryClientError,
					server_error=CropRegistryServerError,
					log_title="Crop Sown Registry Auth Error",
				)
		self.assertEqual(caught.exception.status_code, 401)

	def test_crop_client_requests_a_keycloak_token_and_matching_csrf(self):
		seen = {}

		def post(url, **kwargs):
			seen["url"] = url
			seen["data"] = kwargs["data"]
			return _Resp(200, {"access_token": "staff-token", "expires_in": 300})

		client = HTTPCropRegistryClient(
			staff_url="https://staff-crop.test",
			keycloak={
				"token_url": "https://keycloak.test/token",
				"client_id": "staff-portal",
				"client_secret": "secret",
			},
		)
		from da_services.integrations.staff_api import auth as staff_auth

		staff_auth._CC_TOKEN_CACHE.clear()
		with patch("da_services.integrations.staff_api.auth.requests.post", post):
			headers, cookies = client._auth_and_csrf_headers()

		self.assertEqual(seen["url"], "https://keycloak.test/token")
		self.assertEqual(seen["data"]["grant_type"], "client_credentials")
		self.assertEqual(seen["data"]["client_id"], "staff-portal")
		self.assertEqual(headers["Authorization"], "Bearer staff-token")
		self.assertEqual(headers["X-CSRF-Token"], cookies["X-CSRF-Token"])

	def test_staff_login_sends_the_iam_csrf_cookie(self):
		seen = {}

		def session(keycloak, timeout, **_kwargs):
			seen["username"] = keycloak["username"]
			seen["iam_base_url"] = keycloak["iam_base_url"]
			seen["timeout"] = timeout
			return "staff-user", "iam-csrf"

		client = HTTPCropRegistryClient(
			staff_url="https://staff-crop.test",
			keycloak={
				"username": "crop-agent",
				"password": "secret",
				"iam_base_url": "https://iam.crop.test",
				"kc_realm_url": "https://keycloak.test/realms/staff",
				"login_provider_id": "1",
				"token_url": "https://keycloak.test/token",
				"client_id": "staff-portal",
				"client_secret": "secret",
			},
			registry_client_id="cropsown-registry-staff-portal",
		)
		with patch("da_services.integrations.staff_api.auth.staff_session", session):
			headers, cookies = client._auth_and_csrf_headers()
		self.assertEqual(seen["username"], "crop-agent")
		self.assertEqual(seen["iam_base_url"], "https://iam.crop.test")
		self.assertEqual(headers["Authorization"], "Bearer staff-user")
		self.assertEqual(headers["X-CSRF-Token"], "iam-csrf")
		self.assertEqual(cookies["X-CSRF-Token"], "iam-csrf")

	def test_cleared_access_cookie_is_ignored(self):
		class _Headers:
			def getlist(self, name):
				return ['X-Access-Token=""; Path=/', "X-Access-Token=aaa.bbb.ccc; Path=/"]

		response = type(
			"Response",
			(),
			{
				"raw": type("Raw", (), {"headers": _Headers()})(),
				"cookies": type("Cookies", (), {"get": staticmethod(lambda name: "")})(),
			},
		)()
		self.assertEqual(_cookie_value(response, "X-Access-Token"), "aaa.bbb.ccc")


def _client():
	return HTTPCropRegistryClient(
		staff_url="https://staff-crop.test",
		tokens=type("Tokens", (), {"get_token": lambda self: "cached-token"})(),
	)


class TestCropSownPayload(unittest.TestCase):
	def test_profile_payload_keeps_section_records_and_tabs(self):
		submission = CropSubmission(
			submission_id="sub-1",
			draft_status="FINAL",
			approval_status="PENDING",
			sections=[CropSection(section_register_id="sec-1", records=[{"local_name": "Teff"}])],
		)
		payload = crop_sown_payload("farmer-1", submission, [{"tab_id": "tab-1", "records": []}])
		self.assertEqual(payload["farmer_id"], "farmer-1")
		self.assertEqual(payload["submission_id"], "sub-1")
		self.assertEqual(payload["sections"][0]["records"], [{"local_name": "Teff"}])
		self.assertEqual(payload["tabs"][0]["tab_id"], "tab-1")

	def test_submission_is_read_when_farmer_id_is_not_a_registry_subject(self):
		"""FR-… on a Crop Sown form is not always a Farmer Registry subject id."""
		submission_id = "75baf279-50a8-4ecc-b42d-3d9a62a43774"
		submission = CropSubmission(
			submission_id=submission_id,
			draft_status="FINAL",
			approval_status="APPROVED",
		)
		client = Mock()
		client.get_submission.return_value = submission
		client.get_tabs.return_value = [{"tab_id": "tab-1", "records": []}]
		previous = frappe.local.form_dict
		frappe.local.form_dict = frappe._dict(submission_id=submission_id)
		try:
			with (
				patch("da_services.api.v1.crop_sown.require_roles", return_value=object()),
				patch(
					"da_services.api.v1.crop_sown._internal_farmer_id",
					return_value="FR-427495614351",
				),
				patch(
					"da_services.api.v1.crop_sown.farmers.get_farmer_profile",
					side_effect=frappe.DoesNotExistError,
				),
				patch("da_services.api.v1.crop_sown.get_crop_registry_client", return_value=client),
			):
				from da_services.api.v1.crop_sown import get_farmer_crops

				result = get_farmer_crops("FR-427495614351")
		finally:
			frappe.local.form_dict = previous

		client.get_submission.assert_called_once_with(submission_id)
		self.assertEqual(result["data"]["submission_id"], submission_id)
		self.assertEqual(result["data"]["farmer_id"], "FR-427495614351")
		self.assertEqual(result["data"]["approval_status"], "APPROVED")

	def test_save_wrapper_posts_one_section(self):
		client = Mock()
		client.save_section.return_value = {"submission_id": "sub-new", "draft_status": "DRAFT"}
		previous = frappe.local.form_dict
		frappe.local.form_dict = frappe._dict(
			section_id="section-1",
			section_register_id="crop-section-1",
			section_payload=[{"local_name": "Teff"}],
			form_id="form-1",
			register_id="reg-1",
		)
		try:
			with (
				patch("da_services.api.v1.crop_sown.require_roles", return_value=object()),
				patch("da_services.api.v1.crop_sown._internal_farmer_id", return_value="farmer-1"),
				patch(
					"da_services.api.v1.crop_sown.farmers.get_farmer_profile",
					side_effect=frappe.DoesNotExistError,
				),
				patch("da_services.api.v1.crop_sown.get_crop_registry_client", return_value=client),
			):
				from da_services.api.v1.crop_sown import save_farmer_crop

				result = save_farmer_crop("farmer-1")
		finally:
			frappe.local.form_dict = previous

		client.save_section.assert_called_once_with(
			section_id="section-1",
			section_register_id="crop-section-1",
			section_payload=[{"local_name": "Teff"}],
			submission_id=None,
			form_id="form-1",
			register_id="reg-1",
		)
		self.assertEqual(result["data"]["submission_id"], "sub-new")
		self.assertEqual(result["data"]["farmer_id"], "farmer-1")

	def test_finalize_wrapper_returns_pending_final_submission(self):
		client = Mock()
		client.finalize_submission.return_value = {
			"submission_id": "sub-new",
			"draft_status": "FINAL",
			"approval_status": "PENDING",
		}
		with (
			patch("da_services.api.v1.crop_sown.require_roles", return_value=object()),
			patch("da_services.api.v1.crop_sown._internal_farmer_id", return_value="farmer-1"),
			patch(
				"da_services.api.v1.crop_sown.farmers.get_farmer_profile",
				side_effect=frappe.DoesNotExistError,
			),
			patch("da_services.api.v1.crop_sown.get_crop_registry_client", return_value=client),
		):
			from da_services.api.v1.crop_sown import finalize_farmer_crop

			result = finalize_farmer_crop("farmer-1", "sub-new")

		client.finalize_submission.assert_called_once_with("sub-new")
		self.assertEqual(result["data"]["draft_status"], "FINAL")
		self.assertEqual(result["data"]["approval_status"], "PENDING")


class TestMockCropRegistry(unittest.TestCase):
	def test_fixture_submission_and_tabs(self):
		client = MockCropRegistryClient()
		crop = client.get_submission(FIXTURE_SUBMISSION_ID)

		self.assertEqual(crop.submission_id, FIXTURE_SUBMISSION_ID)
		self.assertEqual(crop.draft_status, "FINAL")
		self.assertEqual(crop.approval_status, "PENDING")
		self.assertEqual(crop.sections[0].section_register_id, "crop-section-1")
		self.assertEqual(crop.sections[0].records[0]["local_name"], "Synthetic maize")
		tabs = client.get_tabs(FIXTURE_SUBMISSION_ID)
		self.assertEqual(tabs[0]["tab_id"], "crop-tab-1")

	def test_unknown_submission_is_absent(self):
		client = MockCropRegistryClient()
		self.assertIsNone(client.get_submission("missing"))
		self.assertEqual(client.get_tabs("missing"), [])

	def test_list_for_farmer_returns_the_linked_fixture(self):
		rows = MockCropRegistryClient().list_for_farmer("farmer-1")
		self.assertEqual(rows[0]["link_internal_record_id"], "farmer-1")
		self.assertEqual(rows[0]["record_name"], "Synthetic maize")


class TestCropMapper(unittest.TestCase):
	def test_maps_verified_read_fields_and_skips_bad_sections(self):
		crop = submission_to_crop(
			{
				"submission_id": "sub-9",
				"draft_status": "FINAL",
				"approval_status": "PENDING",
				"section_payloads": [
					"not-a-section",
					{
						"section_register_id": "sec-1",
						"records": [{"local_name": "Teff"}, "skip"],
					},
				],
			}
		)
		self.assertEqual(crop.submission_id, "sub-9")
		self.assertEqual(len(crop.sections), 1)
		self.assertEqual(crop.sections[0].records, [{"local_name": "Teff"}])


class TestHttpReadPath(unittest.TestCase):
	def test_get_submission_posts_the_verified_envelope(self):
		seen = {}

		def post(url, **kwargs):
			seen["url"] = url
			seen["json"] = kwargs["json"]
			seen["headers"] = kwargs["headers"]
			seen["cookies"] = kwargs["cookies"]
			return _Resp(
				200,
				_envelope(
					{
						"submission_id": "sub-1",
						"draft_status": "FINAL",
						"approval_status": "PENDING",
						"section_payloads": [
							{"section_register_id": "sec-1", "records": [{"local_name": "Teff"}]}
						],
					}
				),
			)

		with patch("da_services.integrations.crop_registry.http.requests.post", post):
			crop = _client().get_submission("sub-1")

		self.assertEqual(seen["url"], "https://staff-crop.test/intake-form-data/get_intake_form_submission")
		body = seen["json"]
		self.assertEqual(body["request_header"]["sender_app_mnemonic"], "OAN_REGISTRY_API_TEST")
		self.assertEqual(body["request_body"]["request_payload"], {"submission_id": "sub-1"})
		self.assertEqual(body["request_body"]["pagination_request"]["filter_by"], {})
		self.assertEqual(seen["headers"]["Authorization"], "Bearer cached-token")
		self.assertEqual(seen["cookies"]["X-CSRF-Token"], seen["headers"]["X-CSRF-Token"])
		self.assertEqual(crop.draft_status, "FINAL")
		self.assertEqual(crop.sections[0].records[0]["local_name"], "Teff")

	def test_save_section_posts_the_collection_payload(self):
		seen = {}

		def post(url, **kwargs):
			seen["url"] = url
			seen["payload"] = kwargs["json"]["request_body"]["request_payload"]
			return _Resp(200, _envelope({"submission_id": "sub-new", "draft_status": "DRAFT"}))

		with patch("da_services.integrations.crop_registry.http.requests.post", post):
			saved = _client().save_section(
				section_id="section-1",
				section_register_id="crop-section-1",
				section_payload=[{"local_name": "Teff"}],
				form_id="form-1",
				register_id="reg-1",
			)

		self.assertEqual(seen["url"], "https://staff-crop.test/intake-form-data/save_intake_form_submission")
		self.assertEqual(
			seen["payload"],
			{
				"submission_id": None,
				"section_id": "section-1",
				"section_payload": [{"local_name": "Teff"}],
				"section_register_id": "crop-section-1",
				"form_id": "form-1",
				"register_id": "reg-1",
			},
		)
		self.assertEqual(saved["submission_id"], "sub-new")

	def test_finalize_posts_only_the_submission_id(self):
		seen = {}

		def post(url, **kwargs):
			seen["url"] = url
			seen["payload"] = kwargs["json"]["request_body"]["request_payload"]
			return _Resp(
				200,
				_envelope(
					{"submission_id": "sub-new", "draft_status": "FINAL", "approval_status": "PENDING"}
				),
			)

		with patch("da_services.integrations.crop_registry.http.requests.post", post):
			finalized = _client().finalize_submission("sub-new")

		self.assertEqual(seen["url"], "https://staff-crop.test/intake-form-data/finalize_intake_form_submission")
		self.assertEqual(seen["payload"], {"submission_id": "sub-new"})
		self.assertEqual(finalized["draft_status"], "FINAL")
		self.assertEqual(finalized["approval_status"], "PENDING")

	def test_business_error_is_a_client_error(self):
		def post(url, **kwargs):
			return _Resp(200, _envelope(None, status="ERROR", error="not allowed"))

		with patch("da_services.integrations.crop_registry.http.requests.post", post):
			with self.assertRaises(CropRegistryClientError):
				_client().get_submission("sub-1")

	def test_http_5xx_is_a_server_error(self):
		def post(url, **kwargs):
			return _Resp(503, text="down")

		with patch("da_services.integrations.crop_registry.http.requests.post", post):
			with self.assertRaises(CropRegistryServerError):
				_client().get_submission("sub-1")

	def test_tabs_follow_discovery_then_read_each_tab(self):
		calls = []

		def post(url, **kwargs):
			payload = kwargs["json"]["request_body"]["request_payload"]
			calls.append((url, payload))
			if url.endswith("/register-metadata/get_all_registers"):
				return _Resp(200, _envelope([{"register_mnemonic": "CropSown", "register_id": "reg-1"}]))
			if url.endswith("/intake-form-metadata/get_all_intake_forms"):
				return _Resp(
					200,
					_envelope(
						[
							{"register_id": "reg-1", "form_mnemonic": "Crop Planning", "form_id": "form-1"},
							{"register_id": "reg-1", "form_mnemonic": "Other", "form_id": "form-2"},
						]
					),
				)
			if url.endswith("/intake-form-metadata/render_intake_form"):
				return _Resp(200, _envelope({"form_id": "form-1", "tabs": [{"tab_id": "tab-1"}]}))
			if url.endswith("/intake-form-data/get_tab_records"):
				return _Resp(200, _envelope([{"section_register_id": "sec-1", "records": []}]))
			raise AssertionError(url)

		with patch("da_services.integrations.crop_registry.http.requests.post", post):
			tabs = _client().get_tabs("sub-1")

		self.assertEqual(calls[0][1], {})
		self.assertEqual(calls[1][1], {"register_id": "reg-1"})
		self.assertEqual(calls[2][1], {"form_id": "form-1"})
		self.assertEqual(calls[3][1], {"submission_id": "sub-1", "tab_id": "tab-1"})
		self.assertEqual(
			tabs, [{"tab_id": "tab-1", "records": [{"section_register_id": "sec-1", "records": []}]}]
		)

	def test_search_keeps_rows_linked_to_the_farmer(self):
		seen = {"urls": [], "search_text": None}

		def post(url, **kwargs):
			payload = kwargs["json"]["request_body"]["request_payload"]
			pagination = kwargs["json"]["request_body"]["pagination_request"]
			seen["urls"].append(url)
			seen["payload"] = payload
			seen["search_text"] = pagination["search_text"]
			body = _envelope(
				[
					{
						"submission_id": "sub-approved",
						"record_name": "FR-427495614351",
						"approval_status": "APPROVED",
						"draft_status": "FINAL",
					},
					{
						"internal_record_id": "crop-2",
						"link_internal_record_id": "someone-else",
						"record_name": "Maize",
					},
				]
			)
			if url.endswith("/register-metadata/get_all_registers"):
				return _Resp(200, _envelope([{"register_mnemonic": "CropSown", "register_id": "reg-1"}]))
			return _Resp(200, body)

		with patch("da_services.integrations.crop_registry.http.requests.post", post):
			rows = _client().list_for_farmer("FR-427495614351")

		self.assertIn("https://staff-crop.test/intake-form-data/search_in_intake_form_submissions", seen["urls"])
		self.assertIn("https://staff-crop.test/register-data/search_in_a_register", seen["urls"])
		self.assertEqual(seen["payload"], {"register_id": "reg-1"})
		self.assertEqual(seen["search_text"], "FR-427495614351")
		self.assertEqual([row["submission_id"] for row in rows], ["sub-approved"])

	def test_ambiguous_register_is_rejected(self):
		def post(url, **kwargs):
			return _Resp(200, _envelope([{"register_mnemonic": "Other", "register_id": "reg-x"}]))

		with patch("da_services.integrations.crop_registry.http.requests.post", post):
			with self.assertRaises(CropRegistryClientError):
				_client().get_tabs("sub-1")


if __name__ == "__main__":
	unittest.main()
