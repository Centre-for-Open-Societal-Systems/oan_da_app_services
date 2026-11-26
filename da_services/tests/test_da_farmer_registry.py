"""GET /farmers, /farmers/{internal_id}/details and /farmers/{internal_id}/land_details: the
Farmer Registry ``search_in_a_register``, ``get_subject_record`` and ``get_tab_records``,
with the caller's own tokens."""

from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests import IntegrationTestCase

from da_services.api.v1.farmers import get_farmer_details, get_farmer_land_details, list_farmers
from da_services.integrations.da_farmer_registry import FarmerRegistryClient, FarmerRegistryError
from da_services.integrations.da_farmer_registry.client import base_url_from_domain

POST = "da_services.integrations.da_farmer_registry.client.requests.post"
HEADERS = {"Authorization": "Bearer tok-1", "X-CSRF-Token": "csrf-1"}
PAYLOAD = [{"farmer_id": "F-1", "first_name": "test"}]
PAGINATION = {"current_page": 1, "page_size": 100, "number_of_items": 1, "number_of_pages": 1}
INTERNAL_ID = "9a2ddcb2-1d06-451c-9c09-5151f8107b14"
BY_ID = {"current_page": 1, "page_size": 100, "search_text": "", "sort_by": "", "filter_by": ""}
RECORD = {"internal_record_id": INTERNAL_ID, "region_name": "Tigray", "kebele_name": "Lekatit"}
LAND = [{"land_id": "L-1", "area_in_hectare": "2.00", "current_land_use": "AGRICULTURAL"}]


def _success(payload, pagination=None):
	return _answer(
		200,
		{
			"response_header": {"response_status": "SUCCESS"},
			"response_body": {"response_payload": payload, "pagination_response": pagination},
		},
	)


def _answer(status=200, body=None):
	response = MagicMock(status_code=status)
	response.json.return_value = (
		body
		if body is not None
		else {
			"response_header": {"response_status": "SUCCESS"},
			"response_body": {"response_payload": PAYLOAD, "pagination_response": PAGINATION},
		}
	)
	return response


class TestDAFarmerRegistryClient(IntegrationTestCase):
	def test_display_fields_become_keys(self):
		row = {
			"record_id": "R-1",
			"display_fields": [
				{"field_name": "functional_record_id", "order": 1, "value": "FR-821910965037"},
				{"field_name": "first_name", "order": 2, "value": "Signoff"},
				{"field_name": "woreda_name", "order": 12, "value": "Tahtay Adiyabo"},
			],
		}
		body = {
			"response_header": {"response_status": "SUCCESS"},
			"response_body": {"response_payload": [row, {"plain": 1}], "pagination_response": PAGINATION},
		}
		with patch(POST, return_value=_answer(200, body)):
			payload, _pagination = self._client().search_in_a_register()
		self.assertEqual(
			payload,
			[
				{
					"record_id": "R-1",
					"functional_record_id": "FR-821910965037",
					"first_name": "Signoff",
					"woreda_name": "Tahtay Adiyabo",
				},
				{"plain": 1},
			],
		)

	def _client(self):
		return FarmerRegistryClient(
			base_url="https://farmer.example.org", register_id="REG-1", token="tok-1", csrf_token="csrf-1"
		)

	def test_the_request_envelope(self):
		with patch(POST, return_value=_answer()) as post:
			payload, pagination = self._client().search_in_a_register(
				current_page=2, page_size=10, filter_by={"first_name": {"eq": "test"}}, search_text="ab"
			)
		self.assertEqual((payload, pagination), (PAYLOAD, PAGINATION))
		self.assertEqual(
			post.call_args.args[0], "https://farmer.example.org/register-data/search_in_a_register"
		)
		kw = post.call_args.kwargs
		self.assertEqual(kw["headers"]["Authorization"], "Bearer tok-1")
		self.assertEqual(kw["headers"]["X-CSRF-Token"], "csrf-1")
		self.assertEqual(kw["cookies"], {"X-CSRF-Token": "csrf-1"})
		body = kw["json"]["request_body"]
		self.assertEqual(body["request_payload"], {"register_id": "REG-1"})
		self.assertEqual(
			body["pagination_request"],
			{
				"current_page": 2,
				"page_size": 10,
				"filter_by": {"first_name": {"eq": "test"}},
				"search_text": "ab",
			},
		)
		self.assertEqual(kw["json"]["request_header"]["sender_app_mnemonic"], "OAN_REGISTRY_API_TEST")

	def test_get_subject_record(self):
		with patch(POST, return_value=_success(RECORD)) as post:
			record = self._client().get_subject_record(INTERNAL_ID)
		self.assertEqual(record, RECORD)
		self.assertEqual(
			post.call_args.args[0], "https://farmer.example.org/register-data/get_subject_record"
		)
		kw = post.call_args.kwargs
		self.assertEqual(kw["headers"]["Authorization"], "Bearer tok-1")
		self.assertEqual(kw["cookies"], {"X-CSRF-Token": "csrf-1"})
		body = kw["json"]["request_body"]
		self.assertEqual(
			body["request_payload"], {"subject_register_id": "REG-1", "subject_record_id": INTERNAL_ID}
		)
		self.assertEqual(body["pagination_request"], BY_ID)

	def test_get_subject_record_display_fields_become_keys(self):
		record = {"internal_record_id": INTERNAL_ID, "display_fields": [{"field_name": "a", "value": 1}]}
		with patch(POST, return_value=_success(record)):
			self.assertEqual(
				self._client().get_subject_record(INTERNAL_ID), {"internal_record_id": INTERNAL_ID, "a": 1}
			)

	def test_get_tab_records(self):
		with patch(POST, return_value=_success(LAND, PAGINATION)) as post:
			payload, pagination = self._client().get_tab_records(INTERNAL_ID, "farmer_land_tab")
		self.assertEqual((payload, pagination), (LAND, PAGINATION))
		self.assertEqual(post.call_args.args[0], "https://farmer.example.org/register-data/get_tab_records")
		body = post.call_args.kwargs["json"]["request_body"]
		self.assertEqual(
			body["request_payload"],
			{"subject_register_id": "REG-1", "subject_record_id": INTERNAL_ID, "tab_id": "farmer_land_tab"},
		)
		self.assertEqual(body["pagination_request"], BY_ID)

	def test_failures_have_a_kind(self):
		cases = [
			(_answer(401), "unauthorized"),
			(_answer(503), "unavailable"),
			(_answer(200, {"response_header": {"response_status": "FAILURE"}}), "rejected"),
		]
		for response, kind in cases:
			with patch(POST, return_value=response), self.assertRaises(FarmerRegistryError) as ctx:
				self._client().search_in_a_register()
			self.assertEqual(ctx.exception.kind, kind)
		with (
			patch(POST, side_effect=requests.ConnectionError("down")),
			self.assertRaises(FarmerRegistryError) as ctx,
		):
			self._client().search_in_a_register()
		self.assertEqual(ctx.exception.kind, "unavailable")

	def test_base_url(self):
		self.assertEqual(base_url_from_domain("farmer.example.org/"), "https://farmer.example.org")
		self.assertEqual(base_url_from_domain("http://h:8000"), "http://h:8000")
		with self.assertRaises(FarmerRegistryError) as ctx:
			base_url_from_domain("")
		self.assertEqual(ctx.exception.kind, "config")


class TestListFarmersEndpoint(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.settings = frappe.get_single("DA Services Settings")
		self.saved = (self.settings.farmer_registry_domain, self.settings.farmer_registry_form_register_id)
		frappe.db.set_single_value("DA Services Settings", "farmer_registry_domain", "farmer.example.org")
		frappe.db.set_single_value("DA Services Settings", "farmer_registry_form_register_id", "REG-1")
		self.headers = patch("frappe.get_request_header", side_effect=lambda k, d=None: HEADERS.get(k, d))
		self.headers.start()
		frappe.response.pop("http_status_code", None)

	def tearDown(self):
		self.headers.stop()
		frappe.db.set_single_value("DA Services Settings", "farmer_registry_domain", self.saved[0])
		frappe.db.set_single_value("DA Services Settings", "farmer_registry_form_register_id", self.saved[1])
		frappe.response.pop("http_status_code", None)

	def test_returns_the_registry_keys(self):
		with patch(POST, return_value=_answer()) as post:
			result = list_farmers(
				current_page="1", page_size="100", filter_by='{"first_name": {"eq": "test"}}', search_text=""
			)
		self.assertEqual(result["status"], "success", result)
		self.assertEqual(result["data"], PAYLOAD)
		self.assertEqual({k: result["meta"][k] for k in PAGINATION}, PAGINATION)
		request = post.call_args.kwargs["json"]["request_body"]["pagination_request"]
		self.assertEqual(request["filter_by"], {"first_name": {"eq": "test"}})
		self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer tok-1")

	def test_defaults(self):
		with (
			patch(POST, return_value=_answer()) as post,
			patch("da_services.services.utils.frappe.permissions.get_user_permissions", return_value={}),
		):
			list_farmers()
		request = post.call_args.kwargs["json"]["request_body"]["pagination_request"]
		self.assertEqual(request, {"current_page": 1, "page_size": 100, "filter_by": {}, "search_text": ""})

	def test_bad_parameters(self):
		for kwargs in (
			{"current_page": "0"},
			{"page_size": "1001"},
			{"filter_by": "[1]"},
			{"filter_by": "{"},
		):
			with patch(POST) as post:
				result = list_farmers(**kwargs)
			self.assertEqual(result["status"], "error", kwargs)
			post.assert_not_called()

	def test_not_configured(self):
		frappe.db.set_single_value("DA Services Settings", "farmer_registry_form_register_id", "")
		with patch(POST) as post:
			result = list_farmers()
		self.assertEqual(result["code"], "FARMER_REGISTRY_NOT_CONFIGURED")
		post.assert_not_called()

	def test_filter_by_goes_through_the_geo_user_permissions(self):
		with (
			patch(
				"da_services.api.v1.farmers.apply_geo_user_permissions",
				return_value={"woreda_name": {"eq": "Bako Tibe"}},
			) as apply,
			patch(POST, return_value=_answer()) as post,
		):
			list_farmers(filter_by='{"first_name": {"eq": "test"}}')
		apply.assert_called_once_with({"first_name": {"eq": "test"}})
		sent = post.call_args.kwargs["json"]["request_body"]["pagination_request"]["filter_by"]
		self.assertEqual(sent, {"woreda_name": {"eq": "Bako Tibe"}})

	def test_a_refused_geo_filter_is_403(self):
		with (
			patch(POST) as post,
			patch(
				"da_services.services.utils.frappe.permissions.get_user_permissions",
				return_value={"DA Woreda": [{"doc": "ET04-W01"}]},
			),
		):
			frappe.set_user("Guest")
			try:
				result = list_farmers(filter_by='{"woreda_code": {"eq": "ET03-W02"}}')
			finally:
				frappe.set_user("Administrator")
		self.assertEqual(result["status"], "error")
		self.assertIn("ET03-W02", result["message"])
		post.assert_not_called()

	def test_registry_errors_map_to_status(self):
		for response, status, code in (
			(_answer(401), 403, "FARMER_REGISTRY_ACCESS_DENIED"),
			(_answer(403), 403, "FARMER_REGISTRY_ACCESS_DENIED"),
			(_answer(500), 503, "SERVICE_UNAVAILABLE"),
			(_answer(200, {"response_header": {"response_status": "FAILURE"}}), 502, "BAD_GATEWAY"),
		):
			with patch(POST, return_value=response):
				result = list_farmers()
			self.assertEqual((frappe.response.get("http_status_code"), result["code"]), (status, code))


class TestFarmerDetailsEndpoints(IntegrationTestCase):
	"""GET /farmers/{internal_id}/details and /farmers/{internal_id}/land_details."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.settings = frappe.get_single("DA Services Settings")
		self.saved = (self.settings.farmer_registry_domain, self.settings.farmer_registry_form_register_id)
		frappe.db.set_single_value("DA Services Settings", "farmer_registry_domain", "farmer.example.org")
		frappe.db.set_single_value("DA Services Settings", "farmer_registry_form_register_id", "REG-1")
		self.headers = patch("frappe.get_request_header", side_effect=lambda k, d=None: HEADERS.get(k, d))
		self.headers.start()
		frappe.response.pop("http_status_code", None)

	def tearDown(self):
		self.headers.stop()
		frappe.db.set_single_value("DA Services Settings", "farmer_registry_domain", self.saved[0])
		frappe.db.set_single_value("DA Services Settings", "farmer_registry_form_register_id", self.saved[1])
		frappe.response.pop("http_status_code", None)

	def test_details_returns_the_registry_record(self):
		with patch(POST, return_value=_success(RECORD)) as post:
			result = get_farmer_details(INTERNAL_ID)
		self.assertEqual(result["status"], "success", result)
		self.assertEqual(result["data"], RECORD)
		self.assertEqual(
			post.call_args.args[0], "https://farmer.example.org/register-data/get_subject_record"
		)
		self.assertEqual(
			post.call_args.kwargs["json"]["request_body"]["request_payload"],
			{"subject_register_id": "REG-1", "subject_record_id": INTERNAL_ID},
		)
		self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer tok-1")

	def test_details_does_not_cache_the_farmer(self):
		with patch(POST, return_value=_success(RECORD)):
			get_farmer_details(INTERNAL_ID)
		self.assertFalse(frappe.db.exists("DA Farmer Reference", INTERNAL_ID))

	def test_details_of_an_unknown_farmer_is_an_error(self):
		with patch(POST, return_value=_success(None)):
			result = get_farmer_details(INTERNAL_ID)
		self.assertEqual(result["status"], "error")
		self.assertIn(INTERNAL_ID, result["message"])

	def test_land_details_returns_the_lands_tab(self):
		with patch(POST, return_value=_success(LAND, PAGINATION)) as post:
			result = get_farmer_land_details(INTERNAL_ID)
		self.assertEqual(result["status"], "success", result)
		self.assertEqual(result["data"], LAND)
		self.assertEqual({k: result["meta"][k] for k in PAGINATION}, PAGINATION)
		self.assertEqual(post.call_args.args[0], "https://farmer.example.org/register-data/get_tab_records")
		self.assertEqual(
			post.call_args.kwargs["json"]["request_body"]["request_payload"],
			{"subject_register_id": "REG-1", "subject_record_id": INTERNAL_ID, "tab_id": "farmer_land_tab"},
		)

	def test_land_details_of_a_farmer_without_land_is_empty(self):
		with patch(POST, return_value=_success(None)):
			result = get_farmer_land_details(INTERNAL_ID)
		self.assertEqual((result["status"], result["data"]), ("success", []))

	def test_not_configured(self):
		frappe.db.set_single_value("DA Services Settings", "farmer_registry_form_register_id", "")
		for endpoint in (get_farmer_details, get_farmer_land_details):
			with patch(POST) as post:
				result = endpoint(INTERNAL_ID)
			self.assertEqual(result["code"], "FARMER_REGISTRY_NOT_CONFIGURED")
			post.assert_not_called()

	def test_registry_errors_map_to_status(self):
		for endpoint in (get_farmer_details, get_farmer_land_details):
			for response, status, code in (
				(_answer(401), 401, "AUTHENTICATION_ERROR"),
				(_answer(500), 503, "SERVICE_UNAVAILABLE"),
				(_answer(200, {"response_header": {"response_status": "FAILURE"}}), 502, "BAD_GATEWAY"),
			):
				with patch(POST, return_value=response):
					result = endpoint(INTERNAL_ID)
				self.assertEqual((frappe.response.get("http_status_code"), result["code"]), (status, code))
