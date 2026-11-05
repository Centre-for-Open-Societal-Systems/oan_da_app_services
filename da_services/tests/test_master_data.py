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


def _reply(payload, status=200, ok=True):
	r = MagicMock()
	r.status_code = status
	r.json.return_value = {
		"response_header": {
			"response_status": "SUCCESS" if ok else "FAILURE",
			"response_error_message": "no",
		},
		"response_body": {"response_payload": payload},
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
			rows = _client().get_master_data("zone", "ET04")
		self.assertEqual([r["code"] for r in rows], ["ET04-Z01"])
		self.assertEqual(rows[0]["name"], "East Shewa")
		levels_call, values_call = post.call_args_list
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
				_client().get_master_data("region")
		self.assertEqual(ctx.exception.kind, "unauthorized")

	def test_unknown_level(self):
		with self.assertRaises(MasterDataError):
			_client().get_master_data("village")
