"""A2C Credit page reads: mocked HTTP client, validation, authorization forwarding and envelopes.

Nothing here contacts A2C. ``requests.get`` and the caller's Authorization
header are stubbed, so the tests are about the wrapper contract only.
"""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import frappe
import requests

from da_services.api.v1 import credit as api
from da_services.integrations.a2c import client as a2c
from da_services.integrations.a2c.client import (
	A2CConfigurationError,
	A2CNotFound,
	A2CRejectedError,
	A2CResponseError,
	A2CUnavailable,
	A2CValidationError,
	HttpA2CClient,
	MockA2CClient,
	reset_client,
)

_BASE_URL = "https://a2c.test"
_SUMMARY = {
	"data": {
		"stages": {
			"Active": 9,
			"Applied": 0,
			"Approved": 1,
			"Disbursed": 0,
			"Processed": 0,
			"Rejected": 0,
			"Submitted": 1,
			"Under Review": 0,
			"Verified": 0,
		},
		"tab_counts": {"all": 11, "my": 0, "unassigned": 11},
		"total": 11,
	},
	"message": "Loan summary retrieved successfully",
	"meta": {},
	"request_id": "synthetic-request-id",
	"status": "success",
}
_LIST = {
	"data": [
		{
			"application_id": "APP-TEST-001",
			"bank": "TEST-BANK",
			"creation": "2026-11-20T15:37:23+05:30",
			"first_name": "Test",
			"is_successful": False,
			"is_terminal": False,
			"kebele": None,
			"last_name": "Applicant",
			"lead_id": "LD-TEST-001",
			"loan_amount": 25000,
			"loan_product": "TEST-PRODUCT",
			"loan_product_name": "Test Loan Product",
			"loan_type": "Input loan",
			"phone_number": "+0000000000",
			"region": None,
			"sequence": 4,
			"stage_id": "approved-test",
			"stage_label": "Approved",
			"status": "Approved",
			"step": 4,
			"submitted_at": "2026-11-20T15:39:55+05:30",
			"woreda": None,
		}
	],
	"message": "Loan applications retrieved successfully",
	"meta": {},
	"pagination": {
		"has_next": False,
		"limit": 20,
		"page": 1,
		"total": 1,
		"total_pages": 1,
	},
	"request_id": "synthetic-request-id",
	"status": "success",
}
_DOCUMENTS_NOT_FOUND = {
	"code": "NOT_FOUND",
	"details": {},
	"message": "A2C Loan Application not found",
	"request_id": "synthetic-request-id",
	"status": "error",
}


class _Resp:
	def __init__(self, status_code=200, body=None, json_error=None):
		self.status_code = status_code
		self._body = body
		self._json_error = json_error

	def json(self):
		if self._json_error is not None:
			raise self._json_error
		return self._body


def _http_client(**overrides) -> HttpA2CClient:
	conf = {
		"a2c_api_base_url": _BASE_URL,
		"a2c_timeout_seconds": 7,
	}
	conf.update(overrides)
	with patch.dict(frappe.conf, conf):
		return HttpA2CClient()


def _no_request():
	"""Make header lookup behave like a request that sends no Authorization."""
	return patch("da_services.integrations.a2c.client.frappe.get_request_header", return_value=None)


class TestA2CReadClient(unittest.TestCase):
	def setUp(self):
		reset_client()

	def tearDown(self):
		reset_client()

	def test_summary_reads_the_documented_path_and_forwards_bearer(self):
		seen = {}

		def get(url, **kwargs):
			seen.update(url=url, **kwargs)
			return _Resp(body=_SUMMARY)

		with (
			patch("da_services.integrations.a2c.client.requests.get", get),
			patch(
				"da_services.integrations.a2c.client.frappe.get_request_header",
				return_value="Bearer caller-token",
			),
		):
			body = _http_client().get_loan_application_summary()

		self.assertEqual(seen["url"], f"{_BASE_URL}/v1/loan-applications/summary")
		self.assertIsNone(seen["params"])
		self.assertEqual(seen["headers"]["Accept"], "application/json")
		self.assertEqual(seen["headers"]["Authorization"], "Bearer caller-token")
		self.assertEqual(seen["timeout"], 7)
		self.assertNotIn("Cookie", seen["headers"])
		self.assertNotIn("X-CSRF-Token", seen["headers"])
		self.assertEqual(body, _SUMMARY)

	def test_list_forwards_only_status_and_page(self):
		seen = {}

		def get(url, **kwargs):
			seen.update(url=url, **kwargs)
			return _Resp(body=_LIST)

		with (
			patch("da_services.integrations.a2c.client.requests.get", get),
			_no_request(),
		):
			body = _http_client().list_loan_applications(status="Approved", page="1")

		self.assertEqual(seen["url"], f"{_BASE_URL}/v1/loan-applications")
		self.assertEqual(seen["params"], {"page": 1, "status": "Approved"})
		self.assertEqual(body, _LIST)

	def test_list_defaults_to_page_one_and_omits_a_missing_status(self):
		seen = {}

		def get(url, **kwargs):
			seen.update(url=url, **kwargs)
			return _Resp(body=_LIST)

		with (
			patch("da_services.integrations.a2c.client.requests.get", get),
			_no_request(),
		):
			_http_client().list_loan_applications()

		self.assertEqual(seen["params"], {"page": 1})

	def test_page_must_be_a_positive_integer(self):
		for page in (0, -1, "-3", "abc", "", "  ", "1.5", "+2", True):
			with self.subTest(page=page):
				get = Mock()
				with (
					patch("da_services.integrations.a2c.client.requests.get", get),
					_no_request(),
					self.assertRaises(A2CValidationError),
				):
					_http_client().list_loan_applications(page=page)
				get.assert_not_called()

	def test_status_must_be_one_safe_query_value(self):
		for status in ("Under/Review", "a&b", "a?b", "a\nb", "x" * 65):
			with self.subTest(status=status):
				get = Mock()
				with (
					patch("da_services.integrations.a2c.client.requests.get", get),
					_no_request(),
					self.assertRaises(A2CValidationError),
				):
					_http_client().list_loan_applications(status=status)
				get.assert_not_called()

	def test_application_id_accepts_only_safe_single_segments(self):
		for value in ("app-123", "APP_1.2", "A", "a" * 128):
			with self.subTest(value=value):
				self.assertEqual(a2c.validate_application_id(value), value)

		for value in (
			"",
			"..",
			"../../etc/passwd",
			"a/b",
			"a?x=1",
			"a#b",
			"app id",
			"app%2Fid",
			".hidden",
			"a" * 129,
		):
			with self.subTest(value=value):
				with self.assertRaises(A2CValidationError):
					a2c.validate_application_id(value)

	def test_documents_path_uses_one_validated_segment(self):
		seen = {}

		def get(url, **kwargs):
			seen.update(url=url, **kwargs)
			return _Resp(404, _DOCUMENTS_NOT_FOUND)

		with (
			patch("da_services.integrations.a2c.client.requests.get", get),
			_no_request(),
			self.assertRaises(A2CNotFound),
		):
			_http_client().get_loan_application_documents("APP-TEST-001")

		self.assertEqual(seen["url"], f"{_BASE_URL}/v1/loan-applications/APP-TEST-001/documents")
		self.assertIsNone(seen["params"])

	def test_documents_reject_injection_without_calling_upstream(self):
		for value in ("../../admin", "app-1/documents/../other", "app 1"):
			with self.subTest(value=value):
				get = Mock()
				with (
					patch("da_services.integrations.a2c.client.requests.get", get),
					_no_request(),
					self.assertRaises(A2CValidationError),
				):
					_http_client().get_loan_application_documents(value)
				get.assert_not_called()

	def test_authorization_is_read_per_call_and_never_stored(self):
		sent = []

		def get(url, **kwargs):
			sent.append(kwargs["headers"].get("Authorization"))
			return _Resp(body=_SUMMARY)

		client = _http_client()
		with (
			patch("da_services.integrations.a2c.client.requests.get", get),
			patch(
				"da_services.integrations.a2c.client.frappe.get_request_header",
				side_effect=["Bearer first", None],
			),
		):
			client.get_loan_application_summary()
			client.get_loan_application_summary()

		self.assertEqual(sent, ["Bearer first", None])
		self.assertNotIn("Bearer first", repr(client.__dict__))
		self.assertFalse(hasattr(client, "headers"))

	def test_upstream_401_and_403_are_safe_bad_gateway_errors(self):
		for code in (401, 403):
			with self.subTest(code=code):
				with (
					patch(
						"da_services.integrations.a2c.client.requests.get",
						return_value=_Resp(code, {"detail": "denied"}),
					),
					patch(
						"da_services.integrations.a2c.client.frappe.get_request_header",
						return_value="Bearer secret-token",
					),
					patch("da_services.integrations.a2c.client.frappe.log_error") as log,
					self.assertRaises(A2CRejectedError) as caught,
				):
					_http_client().get_loan_application_summary()

				self.assertEqual(caught.exception.http_status_code, 502)
				logged = log.call_args.kwargs["message"]
				self.assertNotIn("secret-token", logged)
				self.assertNotIn("Bearer", logged)
				self.assertNotIn("denied", logged)

	def test_upstream_404_is_not_found(self):
		with (
			patch(
				"da_services.integrations.a2c.client.requests.get",
				return_value=_Resp(404, _DOCUMENTS_NOT_FOUND),
			),
			_no_request(),
			self.assertRaises(A2CNotFound),
		):
			_http_client().get_loan_application_documents("APP-TEST-001")

	def test_upstream_5xx_is_unavailable(self):
		for code in (500, 502, 503):
			with self.subTest(code=code):
				with (
					patch(
						"da_services.integrations.a2c.client.requests.get",
						return_value=_Resp(code, {"detail": "down"}),
					),
					_no_request(),
					self.assertRaises(A2CUnavailable),
				):
					_http_client().get_loan_application_summary()

	def test_timeout_and_connection_errors_are_unavailable(self):
		for error in (requests.Timeout("slow"), requests.ConnectionError("refused")):
			with self.subTest(error=error):
				with (
					patch(
						"da_services.integrations.a2c.client.requests.get",
						side_effect=error,
					),
					_no_request(),
					self.assertRaises(A2CUnavailable),
				):
					_http_client().list_loan_applications()

	def test_non_json_and_scalar_json_are_response_errors(self):
		for response in (
			_Resp(body=None, json_error=ValueError("not json")),
			_Resp(body="plain text"),
			_Resp(body=3),
			_Resp(body=None),
		):
			with self.subTest(response=response._body):
				with (
					patch(
						"da_services.integrations.a2c.client.requests.get",
						return_value=response,
					),
					_no_request(),
					self.assertRaises(A2CResponseError),
				):
					_http_client().get_loan_application_summary()

	def test_missing_base_url_is_a_configuration_error(self):
		with patch.dict(frappe.conf, {"a2c_api_base_url": ""}):
			with self.assertRaises(A2CConfigurationError):
				HttpA2CClient()
		with patch.dict(frappe.conf, {"a2c_api_base_url": "a2c.test"}):
			with self.assertRaises(A2CConfigurationError):
				HttpA2CClient()

	def test_mock_backend_does_not_fabricate_credit_data(self):
		client = MockA2CClient()
		for call in (
			client.get_loan_application_summary,
			lambda: client.list_loan_applications(),
			lambda: client.get_loan_application_documents("app-1"),
		):
			with self.assertRaises(A2CConfigurationError):
				call()


class TestA2CCreditApi(unittest.TestCase):
	def setUp(self):
		reset_client()
		self._form = frappe.local.form_dict
		self._message_log = list(frappe.local.message_log or [])
		frappe.local.form_dict = frappe._dict()
		frappe.local.response.pop("http_status_code", None)
		frappe.local.message_log = []

	def tearDown(self):
		reset_client()
		frappe.local.form_dict = self._form
		frappe.local.message_log = self._message_log
		frappe.local.response.pop("http_status_code", None)

	def _status(self):
		return frappe.local.response.get("http_status_code")

	def test_summary_is_wired_to_its_service(self):
		with (
			patch.object(api.svc, "get_loan_application_summary", return_value=_SUMMARY) as svc,
		):
			result = api.summary()
		svc.assert_called_once_with()
		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"], _SUMMARY)

	def test_unknown_upstream_fields_are_preserved(self):
		body = {
			**_SUMMARY,
			"unknown_top_level": {"kept": True},
			"data": {**_SUMMARY["data"], "unknown_nested": ["kept"]},
		}
		with (
			patch.object(api.svc, "get_loan_application_summary", return_value=body),
		):
			result = api.summary()

		self.assertEqual(result["data"], body)

	def test_list_reads_only_status_and_page_from_the_query(self):
		frappe.local.form_dict = frappe._dict(
			status="Approved",
			page="1",
			farmer_id="must-not-be-forwarded",
			extra="ignored",
		)
		with (
			patch.object(api.svc, "list_loan_applications", return_value=_LIST) as svc,
		):
			result = api.list_applications()

		svc.assert_called_once_with(status="Approved", page="1")
		self.assertEqual(result["data"], _LIST)

	def test_documents_passes_the_path_application_id(self):
		with (
			patch.object(
				api.svc,
				"get_loan_application_documents",
				side_effect=A2CNotFound("missing"),
			) as svc,
		):
			result = api.documents("APP-TEST-001")

		svc.assert_called_once_with("APP-TEST-001")
		self.assertEqual(result["status"], "error")
		self.assertEqual(result["code"], "NOT_FOUND")
		self.assertEqual(self._status(), 404)

	def test_invalid_input_is_400_before_any_upstream_call(self):
		cases = (
			("page", "0"),
			("page", "abc"),
			("status", "Under/Review"),
		)
		for name, value in cases:
			with self.subTest(name=name, value=value):
				frappe.local.form_dict = frappe._dict({name: value})
				upstream = Mock()
				with (
					patch(
						"da_services.services.credit.get_a2c_client",
						return_value=upstream,
					),
				):
					result = api.list_applications()

				self.assertEqual(result["status"], "error")
				self.assertEqual(result["code"], "VALIDATION_ERROR")
				self.assertEqual(self._status(), 400)
				upstream.list_loan_applications.assert_not_called()

	def test_injected_application_id_is_400_before_any_upstream_call(self):
		upstream = Mock()
		with (
			patch(
				"da_services.services.credit.get_a2c_client",
				return_value=upstream,
			),
		):
			result = api.documents("../../admin")

		self.assertEqual(result["status"], "error")
		self.assertEqual(result["code"], "VALIDATION_ERROR")
		self.assertEqual(self._status(), 400)
		upstream.get_loan_application_documents.assert_not_called()

	def test_client_failures_map_to_stable_statuses_and_codes(self):
		cases = (
			(A2CValidationError("page must be a positive integer."), 400, "VALIDATION_ERROR"),
			(A2CNotFound("missing"), 404, "NOT_FOUND"),
			(A2CRejectedError("denied"), 502, "BAD_GATEWAY"),
			(A2CResponseError("bad body"), 502, "BAD_GATEWAY"),
			(A2CUnavailable("down"), 503, "SERVICE_UNAVAILABLE"),
			(A2CConfigurationError("not configured"), 503, "SERVICE_UNAVAILABLE"),
		)
		for error, status, code in cases:
			with self.subTest(error=type(error).__name__):
				frappe.local.response.pop("http_status_code", None)
				with (
					patch.object(api.svc, "get_loan_application_summary", side_effect=error),
				):
					result = api.summary()

				self.assertEqual(result["status"], "error")
				self.assertEqual(result["code"], code)
				self.assertEqual(self._status(), status)

	def test_mock_backend_answers_503_rather_than_fake_data(self):
		with (
			patch.dict(frappe.conf, {"a2c_backend": "mock"}),
		):
			result = api.summary()

		self.assertEqual(result["status"], "error")
		self.assertEqual(result["code"], "SERVICE_UNAVAILABLE")
		self.assertEqual(self._status(), 503)

	def test_end_to_end_list_wiring_forwards_bearer_and_body(self):
		seen = {}

		def get(url, **kwargs):
			seen.update(url=url, **kwargs)
			return _Resp(body=_LIST)

		frappe.local.form_dict = frappe._dict(status="Approved", page="1")
		with (
			patch.dict(frappe.conf, {"a2c_api_base_url": _BASE_URL, "a2c_backend": "http"}),
			patch("da_services.integrations.a2c.client.requests.get", get),
			patch(
				"da_services.integrations.a2c.client.frappe.get_request_header",
				return_value="Bearer caller-token",
			),
		):
			result = api.list_applications()

		self.assertEqual(seen["url"], f"{_BASE_URL}/v1/loan-applications")
		self.assertEqual(seen["params"], {"page": 1, "status": "Approved"})
		self.assertEqual(seen["headers"]["Authorization"], "Bearer caller-token")
		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"], _LIST)

	def test_end_to_end_documents_not_found_maps_to_404(self):
		seen = {}

		def get(url, **kwargs):
			seen.update(url=url, **kwargs)
			return _Resp(404, _DOCUMENTS_NOT_FOUND)

		with (
			patch.dict(frappe.conf, {"a2c_api_base_url": _BASE_URL, "a2c_backend": "http"}),
			patch("da_services.integrations.a2c.client.requests.get", get),
			patch(
				"da_services.integrations.a2c.client.frappe.get_request_header",
				return_value="Bearer caller-token",
			),
		):
			result = api.documents("APP-TEST-001")

		self.assertEqual(seen["url"], f"{_BASE_URL}/v1/loan-applications/APP-TEST-001/documents")
		self.assertEqual(result["status"], "error")
		self.assertEqual(result["code"], "NOT_FOUND")
		self.assertEqual(self._status(), 404)


if __name__ == "__main__":
	unittest.main()
