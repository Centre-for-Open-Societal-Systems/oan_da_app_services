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
from da_services.integrations.crop_registry.intake import (
	IntakeContractError,
	build_section_records,
	describe_form,
	form_selector,
	resolve_section,
	submit_with_metadata,
)
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
		client.submit_sections.return_value = {"submission_id": "sub-new", "draft_status": "DRAFT"}
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

				frappe.local.message_log = [{"message": "Farmer FR-297590870792 not found."}]
				frappe.local.response["http_status_code"] = 404
				result = save_farmer_crop("FR-297590870792")
		finally:
			frappe.local.form_dict = previous

		self.assertEqual(frappe.local.message_log, [])
		client.submit_sections.assert_called_once()
		(sections,) = client.submit_sections.call_args.args
		self.assertEqual(sections[0]["section_id"], "section-1")
		self.assertEqual(sections[0]["section_payload"], [{"local_name": "Teff"}])
		self.assertIsNone(client.submit_sections.call_args.kwargs["submission_id"])
		self.assertIsNone(client.submit_sections.call_args.kwargs["subject_record_id"])
		self.assertEqual(result["data"]["submission_id"], "sub-new")
		self.assertEqual(result["data"]["farmer_id"], "FR-297590870792")
		self.assertNotIn("http_status_code", frappe.local.response)

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

	def test_list_uses_the_profile_reg_id_once(self):
		client = Mock()
		client.list_for_farmer.return_value = [
			{"submission_id": "sub-approved", "record_name": "FR-427495614351"}
		]
		previous = frappe.local.form_dict
		frappe.local.form_dict = frappe._dict(reg_id="FR-427495614351")
		try:
			with (
				patch("da_services.api.v1.crop_sown.require_roles", return_value=object()),
				patch("da_services.api.v1.crop_sown._internal_farmer_id", return_value="farmer-1"),
				patch(
					"da_services.api.v1.crop_sown.farmers.get_farmer_profile",
					return_value={"farmer_id": "farmer-1"},
				),
				patch("da_services.api.v1.crop_sown.get_crop_registry_client", return_value=client),
				patch(
					"da_services.api.v1.crop_sown.frappe.db.get_value",
					return_value="FR-427495614351",
				),
			):
				from da_services.api.v1.crop_sown import get_farmer_crops

				result = get_farmer_crops("farmer-1")
		finally:
			frappe.local.form_dict = previous

		client.list_for_farmer.assert_called_once_with("farmer-1", reg_id="FR-427495614351")
		self.assertEqual(result["data"]["crops"][0]["submission_id"], "sub-approved")

	def test_reg_id_for_another_farmer_is_rejected(self):
		client = Mock()
		previous = frappe.local.form_dict
		frappe.local.form_dict = frappe._dict(reg_id="FR-someone-else")
		try:
			with (
				patch("da_services.api.v1.crop_sown.require_roles", return_value=object()),
				patch("da_services.api.v1.crop_sown._internal_farmer_id", return_value="farmer-1"),
				patch(
					"da_services.api.v1.crop_sown.farmers.get_farmer_profile",
					return_value={"farmer_id": "farmer-1"},
				),
				patch("da_services.api.v1.crop_sown.get_crop_registry_client", return_value=client),
				patch(
					"da_services.api.v1.crop_sown.frappe.db.get_value",
					return_value="FR-427495614351",
				),
			):
				from da_services.api.v1.crop_sown import get_farmer_crops

				result = get_farmer_crops("farmer-1")
		finally:
			frappe.local.form_dict = previous

		client.list_for_farmer.assert_not_called()
		self.assertEqual(result["status"], "error")
		self.assertIn("reg_id", result["message"])


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

		self.assertEqual(
			seen["url"], "https://staff-crop.test/intake-form-data/finalize_intake_form_submission"
		)
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

		self.assertIn(
			"https://staff-crop.test/intake-form-data/search_in_intake_form_submissions", seen["urls"]
		)
		self.assertIn("https://staff-crop.test/register-data/search_in_a_register", seen["urls"])
		self.assertEqual(seen["payload"], {"register_id": "reg-1"})
		self.assertEqual(seen["search_text"], "FR-427495614351")
		self.assertEqual([row["submission_id"] for row in rows], ["sub-approved"])

	def test_profile_ids_search_each_crop_endpoint_once(self):
		"""Submissions use the Farmer-ID. The register uses the internal id."""
		seen = {"urls": [], "search_text": []}

		def post(url, **kwargs):
			seen["urls"].append(url)
			seen["search_text"].append(kwargs["json"]["request_body"]["pagination_request"]["search_text"])
			if url.endswith("/register-metadata/get_all_registers"):
				return _Resp(200, _envelope([{"register_mnemonic": "CropSown", "register_id": "reg-1"}]))
			if "search_in_intake_form_submissions" in url:
				return _Resp(
					200,
					_envelope(
						[
							{
								"submission_id": "sub-approved",
								"record_name": "FR-427495614351",
								"approval_status": "APPROVED",
							}
						]
					),
				)
			return _Resp(
				200,
				_envelope(
					[
						{
							"internal_record_id": "crop-2",
							"link_internal_record_id": "farmer-1",
							"record_name": "Maize",
						}
					]
				),
			)

		with patch("da_services.integrations.crop_registry.http.requests.post", post):
			rows = _client().list_for_farmer("farmer-1", reg_id="FR-427495614351")

		self.assertEqual(
			seen["urls"],
			[
				"https://staff-crop.test/register-metadata/get_all_registers",
				"https://staff-crop.test/intake-form-data/search_in_intake_form_submissions",
				"https://staff-crop.test/register-data/search_in_a_register",
			],
		)
		self.assertEqual(seen["search_text"], ["", "FR-427495614351", "farmer-1"])
		self.assertEqual([row["submission_id"] for row in rows if row.get("submission_id")], ["sub-approved"])
		self.assertEqual(rows[1]["link_internal_record_id"], "farmer-1")

	def test_configured_register_id_skips_discovery(self):
		seen = {"urls": []}

		def post(url, **kwargs):
			seen["urls"].append(url)
			self.assertNotIn("get_all_registers", url)
			payload = kwargs["json"]["request_body"]["request_payload"]
			self.assertEqual(payload, {"register_id": "crop-reg-fixed"})
			return _Resp(200, _envelope([]))

		client = HTTPCropRegistryClient(
			staff_url="https://staff-crop.test",
			tokens=type("Tokens", (), {"get_token": lambda self: "cached-token"})(),
			register_id="crop-reg-fixed",
		)
		with patch("da_services.integrations.crop_registry.http.requests.post", post):
			self.assertEqual(client.list_for_farmer("farmer-1", reg_id="FR-1"), [])

		self.assertEqual(
			seen["urls"],
			[
				"https://staff-crop.test/intake-form-data/search_in_intake_form_submissions",
				"https://staff-crop.test/register-data/search_in_a_register",
			],
		)

	def test_ambiguous_register_is_rejected(self):
		def post(url, **kwargs):
			return _Resp(200, _envelope([{"register_mnemonic": "Other", "register_id": "reg-x"}]))

		with patch("da_services.integrations.crop_registry.http.requests.post", post):
			with self.assertRaises(CropRegistryClientError):
				_client().get_tabs("sub-1")


def _section(**overrides):
	section = {
		"section_id": "section-1",
		"section_register_id": "reg-1",
		"is_list": False,
		"widgets": {},
	}
	section.update(overrides)
	return section


class TestIntakeContract(unittest.TestCase):
	def test_section_register_id_selects_the_section(self):
		sections = [
			_section(section_id="section-1", section_register_id="0c0343ca"),
			_section(section_id="section-2", section_register_id="reg-land"),
		]
		found = resolve_section(sections, {"section_register_id": "0c0343ca"})
		self.assertEqual(found["section_id"], "section-1")
		found = resolve_section(sections, {"section_id": "0c0343ca"})
		self.assertEqual(found["section_id"], "section-1")
		with self.assertRaises(IntakeContractError):
			resolve_section(sections, {"section_id": "section-1", "section_register_id": "reg-land"})

	def test_a_field_added_in_metadata_is_accepted_and_a_removed_field_is_rejected(self):
		record = {"local_name": "Teff", "note": "late"}
		original = [{"field_name": "local_name", "data_type": "string", "required": True}]
		with_note = [*original, {"field_name": "note", "data_type": "string", "required": False}]
		with self.assertRaises(IntakeContractError):
			build_section_records(section=_section(), fields=original, records=[record])
		self.assertEqual(
			build_section_records(section=_section(), fields=with_note, records=[record]),
			[record],
		)

	def test_a_new_required_field_and_a_bad_choice_are_rejected(self):
		fields = [
			{"field_name": "local_name", "data_type": "string", "required": False},
			{"field_name": "season", "data_type": "string", "required": True},
		]
		widgets = {
			"season": {
				"widget-required": True,
				"widget-data-source": {"options": [{"value": "MEHER"}]},
			}
		}
		with self.assertRaises(IntakeContractError):
			build_section_records(
				section=_section(widgets=widgets),
				fields=fields,
				records=[{"local_name": "Teff"}],
			)
		with self.assertRaises(IntakeContractError):
			build_section_records(
				section=_section(widgets=widgets),
				fields=fields,
				records=[{"local_name": "Teff", "season": "BELG"}],
			)
		kept = build_section_records(
			section=_section(widgets=widgets),
			fields=fields,
			records=[{"local_name": "Teff", "season": "MEHER"}],
		)
		self.assertEqual(kept[0]["season"], "MEHER")

	def test_catalog_membership_follows_the_attribute_list(self):
		fields = [{"field_name": "commodity", "data_type": "string", "required": True}]
		widgets = {"commodity": {"widget-data-source": {"params": {"attribute_id": "CROP_COMMODITY"}}}}
		catalogs = {"CROP_COMMODITY": [{"value_id": "CROP_COMMODITY_6"}]}
		with self.assertRaises(IntakeContractError):
			build_section_records(
				section=_section(widgets=widgets),
				fields=fields,
				records=[{"commodity": "MAIZE"}],
				catalogs=catalogs,
			)
		kept = build_section_records(
			section=_section(widgets=widgets),
			fields=fields,
			records=[{"commodity": "CROP_COMMODITY_6"}],
			catalogs=catalogs,
		)
		self.assertEqual(kept[0]["commodity"], "CROP_COMMODITY_6")

	def test_managed_fields_are_rejected_and_read_only_values_are_not_sent(self):
		fields = [{"field_name": "local_name", "data_type": "string", "required": True}]
		with self.assertRaises(IntakeContractError):
			build_section_records(
				section=_section(),
				fields=fields,
				records=[{"local_name": "Teff", "functional_record_id": "FR-1"}],
			)
		with self.assertRaises(IntakeContractError):
			build_section_records(
				section=_section(widgets={"local_name": {"widget-readonly": True}}),
				fields=fields,
				records=[{"local_name": "Teff"}],
			)

	def test_screen_section_id_selects_the_registry_section(self):
		sections = [
			_section(
				section_id="staff-planning",
				section_register_id="reg-plan",
				section_mnemonic="cs_planning_details",
			),
			_section(
				section_id="staff-sowing", section_register_id="reg-sow", section_mnemonic="cs_sowing_details"
			),
		]
		found = resolve_section(sections, {"section_id": "planning"})
		self.assertEqual(found["section_id"], "staff-planning")

	def test_screen_strings_are_coerced_and_blank_optionals_are_omitted(self):
		fields = [
			{"field_name": "planned_area", "data_type": "number", "required": False},
			{"field_name": "is_plot_not_registered", "data_type": "boolean", "required": False},
			{"field_name": "note", "data_type": "string", "required": False},
			{"field_name": "season", "data_type": "string", "required": True},
		]
		widgets = {
			"season": {
				"widget-required": True,
				"widget-data-source": {
					"params": {"attribute_id": "CROP_SEASON"},
				},
			}
		}
		catalogs = {
			"CROP_SEASON": [
				{"value_id": "CROP_SEASON_MEHER", "value_code": "Meher", "value_display": "Meher"}
			]
		}
		kept = build_section_records(
			section=_section(widgets=widgets),
			fields=fields,
			records=[
				{
					"planned_area": "1.2",
					"is_plot_not_registered": "No",
					"note": "",
					"season": "Meher",
				}
			],
			catalogs=catalogs,
		)
		self.assertEqual(
			kept[0],
			{
				"planned_area": 1.2,
				"is_plot_not_registered": False,
				"season": "CROP_SEASON_MEHER",
			},
		)

	def test_crop_report_values_are_grouped_by_screen_section(self):
		from da_services.api.v1.crop_sown import _section_requests

		submission_id, subject_record_id, sections = _section_requests(
			{
				"values": {
					"planning.planned_area": "1.2",
					"identity.crop_year": "2026",
					"planning.season": "Meher",
				}
			}
		)
		self.assertIsNone(submission_id)
		self.assertIsNone(subject_record_id)
		self.assertEqual(
			sections,
			[
				{"section_id": "identity", "records": [{"crop_year": "2026"}]},
				{"section_id": "planning", "records": [{"planned_area": "1.2", "season": "Meher"}]},
			],
		)

	def test_a_child_section_is_linked_after_the_main_section_is_saved(self):
		form = {
			"form_id": "form-1",
			"register_id": "reg-1",
			"tabs": [
				{
					"sections": [
						{"section_id": "main", "section_register_id": "reg-1", "is_list": False},
						{"section_id": "land", "section_register_id": "reg-land", "is_list": True},
					]
				}
			],
		}
		fields = {
			"reg-1": [{"field_name": "local_name", "data_type": "string", "required": True}],
			"reg-land": [{"field_name": "area_in_hectare", "data_type": "number", "required": True}],
		}
		posted = []

		def save(**kwargs):
			posted.append(kwargs)
			body = {"submission_id": "sub-1", "draft_status": "DRAFT"}
			if kwargs["section_register_id"] == "reg-1":
				body["section_payloads"] = [
					{"section_register_id": "reg-1", "records": [{"internal_record_id": "row-1"}]}
				]
			return body

		submit_with_metadata(
			form=form,
			fields_for=lambda register_id: fields[register_id],
			catalogs={},
			requests=[
				{"section_id": "main", "records": [{"local_name": "Teff"}]},
				{"section_id": "land", "records": [{"area_in_hectare": 1.5}]},
			],
			submission_id=None,
			subject_record_id=None,
			save=save,
		)
		self.assertIsNone(posted[0]["submission_id"])
		self.assertEqual(posted[1]["submission_id"], "sub-1")
		self.assertEqual(posted[1]["section_payload"][0]["link_internal_record_id"], "row-1")
		self.assertNotIn("local_name", posted[1]["section_payload"][0])

	def test_submit_posts_only_fields_present_in_metadata(self):
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
						[{"register_id": "reg-1", "form_mnemonic": "Crop Planning", "form_id": "form-1"}]
					),
				)
			if url.endswith("/intake-form-metadata/render_intake_form"):
				return _Resp(
					200,
					_envelope(
						{
							"form_id": "form-1",
							"register_id": "reg-1",
							"tabs": [
								{
									"tab_id": "tab-1",
									"sections": [
										{
											"section_id": "section-1",
											"section_register_id": "reg-1",
											"is_list": False,
											"section_ui_schema": {},
										}
									],
								}
							],
						}
					),
				)
			if url.endswith("/register-metadata/get_register_fields"):
				return _Resp(
					200,
					_envelope(
						{
							"fields": [
								{"field_name": "local_name", "data_type": "string", "required": True},
								{"field_name": "note", "data_type": "string", "required": False},
							]
						}
					),
				)
			if url.endswith("/intake-form-data/save_intake_form_submission"):
				return _Resp(200, _envelope({"submission_id": "sub-1", "draft_status": "DRAFT"}))
			raise AssertionError(url)

		client = _client()
		with patch("da_services.integrations.crop_registry.http.requests.post", post):
			saved = client.submit_sections(
				[{"section_id": "section-1", "records": [{"local_name": "Teff", "note": "late"}]}]
			)
			with self.assertRaises(CropRegistryClientError):
				client.submit_sections(
					[{"section_id": "section-1", "records": [{"local_name": "Teff", "gone": "x"}]}]
				)

		saves = [payload for url, payload in calls if url.endswith("/save_intake_form_submission")]
		self.assertEqual(
			saves,
			[
				{
					"submission_id": None,
					"section_id": "section-1",
					"section_payload": [{"local_name": "Teff", "note": "late"}],
					"section_register_id": "reg-1",
					"form_id": "form-1",
					"register_id": "reg-1",
				}
			],
		)
		self.assertEqual(saved["submission_id"], "sub-1")

	def test_register_required_flag_is_not_a_caller_requirement(self):
		fields = [{"field_name": "created_at", "data_type": "datetime", "required": True}]
		built = build_section_records(
			section=_section(),
			fields=[*fields, {"field_name": "local_name", "data_type": "string", "required": False}],
			records=[{"local_name": "Teff"}],
		)
		self.assertEqual(built, [{"local_name": "Teff"}])

	def test_describe_form_uses_the_widget_contract(self):
		form = {
			"form_id": "form-1",
			"register_id": "reg-1",
			"form_mnemonic": "Crop planning intake",
			"tabs": [
				{
					"sections": [
						{
							"section_id": "season",
							"section_register_id": "reg-1",
							"section_mnemonic": "cs_intake_season",
							"is_list": False,
							"section_ui_schema": {
								"production_season": {
									"column-key": "production_season",
									"widget-label": "Production Season",
									"widget-required": True,
									"widget-data-source": {"params": {"attribute_id": "CROP_SEASON"}},
								},
								"cropping_system": {
									"column-key": "cropping_system",
									"widget-data-source": {
										"options": [
											{"label": "Mono cropping", "value": "MONO_CROPPING"},
										]
									},
								},
								"region": {
									"column-key": "region",
									"widget-data-source": {
										"endpoint": "geo-level-values",
										"params": {
											"valueKey": "level_value_id",
											"labelKey": "level_value_mnemonic",
										},
									},
								},
								"created_at": {"column-key": "created_at", "widget-required": True},
							},
						}
					]
				}
			],
		}
		fields = {
			"reg-1": [
				{"field_name": "production_season", "data_type": "string", "required": False},
				{"field_name": "cropping_system", "data_type": "string", "required": False},
				{"field_name": "region", "data_type": "string", "required": False},
				{"field_name": "created_at", "data_type": "datetime", "required": True},
			]
		}
		described = describe_form(
			form,
			fields,
			{"CROP_SEASON": [{"value_id": "SEASON_MEHER", "value_code": "MEHER", "value_display": "Meher"}]},
		)
		by_name = {field["field_name"]: field for field in described["sections"][0]["fields"]}
		self.assertTrue(by_name["production_season"]["required"])
		self.assertEqual(by_name["production_season"]["label"], "Production Season")
		self.assertEqual(
			by_name["production_season"]["choices"],
			[{"value_id": "SEASON_MEHER", "value_display": "Meher"}],
		)
		self.assertTrue(by_name["production_season"]["catalog_loaded"])
		self.assertEqual(
			by_name["cropping_system"]["options"],
			[{"label": "Mono cropping", "value": "MONO_CROPPING"}],
		)
		self.assertEqual(by_name["region"]["geo"]["endpoint"], "geo-level-values")
		self.assertFalse(by_name["created_at"]["required"])
		self.assertTrue(by_name["created_at"]["readonly"])
		self.assertEqual(form_selector("Crop Sown", "Planning"), "planning")
		self.assertEqual(form_selector(None, "Planning"), "planning")


def _paged(payload, pages):
	body = _envelope(payload)
	body["response_body"]["pagination_response"] = {"number_of_pages": pages}
	return body


class TestCropRenderContract(unittest.TestCase):
	def test_catalog_and_geography_read_every_page(self):
		seen = []

		def post(url, **kwargs):
			page = kwargs["json"]["request_body"]["pagination_request"]["current_page"]
			payload = kwargs["json"]["request_body"]["request_payload"]
			seen.append((url, page, payload))
			if url.endswith("/attributes/get_attribute_values"):
				value_id = f"ROW-{page}"
				return _Resp(
					200,
					_paged(
						{"attribute_values": [{"value_id": value_id, "value_display": value_id}]},
						2,
					),
				)
			if url.endswith("/geo/get_geo_level_values"):
				return _Resp(
					200,
					_paged(
						[
							{
								"level_value_id": f"L-{page}",
								"level_value_mnemonic": f"M-{page}",
								"display_name": "Place",
							}
						],
						2,
					),
				)
			raise AssertionError(url)

		client = HTTPCropRegistryClient(
			staff_url="https://staff-crop.test",
			tokens=type("Tokens", (), {"get_token": lambda self: "cached-token"})(),
			master_data_url="https://master-data.test",
		)
		with patch("da_services.integrations.crop_registry.http.requests.post", post):
			values = client._attribute_values("CROP_COMMODITY")
			places = client.geo_level_values("l2", "ET01")

		self.assertEqual([row["value_id"] for row in values], ["ROW-1", "ROW-2"])
		self.assertEqual([row["level_value_id"] for row in places], ["L-1", "L-2"])
		geo_calls = [item for item in seen if item[0].endswith("/geo/get_geo_level_values")]
		self.assertEqual(geo_calls[0][2], {"level_id": "l2", "parent_level_value_id": "ET01"})
		self.assertEqual([item[1] for item in geo_calls], [1, 2])

	def test_form_query_renders_only_the_named_intake_form(self):
		rendered = []

		def post(url, **kwargs):
			payload = kwargs["json"]["request_body"]["request_payload"]
			if url.endswith("/register-metadata/get_all_registers"):
				return _Resp(200, _envelope([{"register_mnemonic": "CropSown", "register_id": "reg-1"}]))
			if url.endswith("/intake-form-metadata/get_all_intake_forms"):
				return _Resp(
					200,
					_envelope(
						[
							{
								"register_id": "reg-1",
								"form_mnemonic": "Crop planning intake",
								"form_id": "form-plan",
							},
							{
								"register_id": "reg-1",
								"form_mnemonic": "Crop cultivation intake",
								"form_id": "form-cult",
							},
						]
					),
				)
			if url.endswith("/intake-form-metadata/render_intake_form"):
				rendered.append(payload["form_id"])
				return _Resp(
					200,
					_envelope(
						{
							"form_id": payload["form_id"],
							"register_id": "reg-1",
							"form_mnemonic": "Crop cultivation intake",
							"tabs": [
								{
									"tab_id": "tab-1",
									"sections": [
										{
											"section_id": "section-1",
											"section_register_id": "reg-1",
											"section_mnemonic": "cs_cultivation_details",
											"is_list": False,
											"section_ui_schema": {},
										}
									],
								}
							],
						}
					),
				)
			if url.endswith("/register-metadata/get_register_fields"):
				return _Resp(
					200,
					_envelope(
						{"fields": [{"field_name": "local_name", "data_type": "string", "required": False}]}
					),
				)
			raise AssertionError(url)

		client = _client()
		with patch("da_services.integrations.crop_registry.http.requests.post", post):
			described = client.describe_intake_form(form_name="cultivation")

		self.assertEqual(rendered, ["form-cult"])
		self.assertEqual(described["form_id"], "form-cult")
		self.assertEqual(
			described["intake_forms"],
			[
				{"form_id": "form-plan", "form_mnemonic": "Crop planning intake"},
				{"form_id": "form-cult", "form_mnemonic": "Crop cultivation intake"},
			],
		)


if __name__ == "__main__":
	unittest.main()
