"""Master Data geography client. These tests do not call a live service."""

import unittest
from unittest.mock import MagicMock, patch

from da_services.integrations.master import MasterDataClient, MasterDataError
from da_services.integrations.master.client import base_url_from_domain

_LEVELS = [
	{"level_id": "l0", "level_mnemonic": "country", "parent_level_id": None},
	{"level_id": "l1", "level_mnemonic": "region", "parent_level_id": "l0"},
	{"level_id": "l2", "level_mnemonic": "zone", "parent_level_id": "l1"},
]
_ZONES = [
	{
		"level_value_id": "ET04-Z01",
		"level_id": "l2",
		"level_value_mnemonic": "East Shewa",
		"parent_level_value_id": "ET04",
	},
	{
		"level_value_id": "ET03-Z01",
		"level_id": "l2",
		"level_value_mnemonic": "North Gondar",
		"parent_level_value_id": "ET03",
	},
]


def _reply(payload, status=200, ok=True, pagination=None):
	r = MagicMock()
	r.status_code = status
	r.json.return_value = {
		"response_header": {
			"response_status": "SUCCESS" if ok else "FAILURE",
			"response_error_message": "no",
		},
		"response_body": {"response_payload": payload, "pagination_response": pagination},
	}
	return r


def _client():
	return MasterDataClient(base_url="https://md.test", token="tok", csrf_token="csrf")


class TestMasterData(unittest.TestCase):
	def test_domain(self):
		self.assertEqual(base_url_from_domain("md.test"), "https://md.test")
		self.assertEqual(base_url_from_domain("http://md.test/"), "http://md.test")
		with self.assertRaises(MasterDataError) as ctx:
			base_url_from_domain("")
		self.assertEqual(ctx.exception.kind, "config")

	def test_children_of_parent_and_forwarded_credentials(self):
		with patch(
			"da_services.integrations.master.client.requests.post",
			side_effect=[_reply(_LEVELS), _reply(_ZONES)],
		) as post:
			rows, page = _client().get_master_data("geo", "zone", "ET04", current_page=2, page_size=50)
		self.assertEqual([r["code"] for r in rows], ["ET04-Z01"])
		self.assertEqual(
			rows[0],
			{
				"id": "ET04-Z01",
				"code": "ET04-Z01",
				"name": "East Shewa",
				"field_name": "zone",
				"parent": "ET04",
				"sort_order": 0,
			},
		)
		levels_call, values_call = post.call_args_list
		self.assertEqual(
			values_call.kwargs["json"]["request_body"]["pagination_request"],
			{"current_page": 2, "page_size": 50, "filter_by": {}, "search_text": ""},
		)
		self.assertEqual(page, {"current_page": 2, "page_size": 50, "total_items": 1, "total_pages": 2})
		self.assertEqual(levels_call.args[0], "https://md.test/geo/get_all_geo_levels")
		self.assertEqual(values_call.args[0], "https://md.test/geo/get_geo_level_values")
		body = values_call.kwargs["json"]["request_body"]["request_payload"]
		self.assertEqual(body, {"level_id": "l2", "parent_level_value_id": "ET04"})
		self.assertEqual(values_call.kwargs["headers"]["Authorization"], "Bearer tok")
		self.assertEqual(values_call.kwargs["headers"]["X-CSRF-Token"], "csrf")
		self.assertEqual(values_call.kwargs["cookies"], {"X-CSRF-Token": "csrf"})

	def test_unauthorized(self):
		with patch(
			"da_services.integrations.master.client.requests.post", return_value=_reply([], status=401)
		):
			with self.assertRaises(MasterDataError) as ctx:
				_client().get_master_data("geo", "region")
		self.assertEqual(ctx.exception.kind, "unauthorized")

	def test_unknown_level(self):
		with self.assertRaises(MasterDataError):
			_client().get_master_data("geo", "village")


_PURPOSE = {
	"attribute_values": [
		{
			"attribute_id": "DOCUMENT_PURPOSE",
			"value_id": "DOCPURP_ELIGIBILITY",
			"value_code": "ELIGIBILITY",
			"value_display": "Programme eligibility",
			"parent_value_id": None,
			"sort_order": 2,
		},
		{
			"attribute_id": "DOCUMENT_PURPOSE",
			"value_id": "DOCPURP_IDENTITY",
			"value_code": "IDENTITY",
			"value_display": "Identity verification",
			"parent_value_id": None,
			"sort_order": 1,
		},
	],
	"total": 2,
}


class TestAttributeValues(unittest.TestCase):
	def test_code_list_has_the_same_row_shape(self):
		with patch(
			"da_services.integrations.master.client.requests.post",
			return_value=_reply(_PURPOSE, pagination={"number_of_items": 2, "number_of_pages": 1}),
		) as post:
			rows, page = _client().get_master_data("attribute", "DOCUMENT_PURPOSE", search="id")
		self.assertEqual([r["code"] for r in rows], ["IDENTITY", "ELIGIBILITY"])
		self.assertEqual(
			rows[0],
			{
				"id": "DOCPURP_IDENTITY",
				"code": "IDENTITY",
				"name": "Identity verification",
				"field_name": "DOCUMENT_PURPOSE",
				"parent": None,
				"sort_order": 1,
			},
		)
		self.assertEqual(page, {"current_page": 1, "page_size": 100, "total_items": 2, "total_pages": 1})
		call = post.call_args
		self.assertEqual(call.args[0], "https://md.test/attributes/get_attribute_values")
		body = call.kwargs["json"]["request_body"]
		self.assertEqual(body["request_payload"], {"attribute_id": "DOCUMENT_PURPOSE"})
		self.assertEqual(body["pagination_request"]["search_text"], "id")
		self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer tok")

	def test_unknown_data_type(self):
		with self.assertRaises(MasterDataError):
			_client().get_master_data("other", "x")

	def test_null_sort_order_is_zero(self):
		payload = {
			"attribute_values": [
				{
					"attribute_id": "A",
					"value_id": "2",
					"value_code": "TWO",
					"value_display": "Two",
					"sort_order": 2,
				},
				{
					"attribute_id": "A",
					"value_id": "0",
					"value_code": "NONE",
					"value_display": "None",
					"sort_order": None,
				},
			]
		}
		with patch("da_services.integrations.master.client.requests.post", return_value=_reply(payload)):
			rows, _page = _client().get_master_data("attribute", "A")
		self.assertEqual([(r["code"], r["sort_order"]) for r in rows], [("NONE", 0), ("TWO", 2)])
