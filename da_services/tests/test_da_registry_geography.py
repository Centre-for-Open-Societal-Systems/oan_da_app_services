"""DA Registry client: Master Data geography levels and values (integrations/da_registry/http.py)."""

from unittest.mock import patch

from frappe.tests import IntegrationTestCase

from da_services.integrations.da_registry import http as http_mod
from da_services.integrations.da_registry.client import DARegistryError
from da_services.integrations.da_registry.http import HttpDARegistryClient
from da_services.tests.test_da_registry_http import _ok, _Resp


class TestHttpGeography(IntegrationTestCase):
	def setUp(self):
		http_mod._TOKEN_CACHE.clear()
		self.calls = []

	def _post(self, url, json=None, headers=None, cookies=None, timeout=None, **kw):
		if url.endswith("/token"):
			return _Resp(200, {"access_token": "tok", "expires_in": 300})
		body = json["request_body"]
		self.calls.append((url, body["request_payload"], body.get("pagination_request"), headers))
		if url.endswith("/geo/get_all_geo_levels"):
			payload = [
				{"level_id": "l0", "level_mnemonic": "country", "parent_level_id": None},
				{"level_id": "l1", "level_mnemonic": "Region", "parent_level_id": "l0"},
			]
		elif url.endswith("/geo/get_geo_level_values"):
			payload = [
				{
					"level_value_id": "ET01",
					"level_id": "l1",
					"level_value_mnemonic": "Tigray",
					"parent_level_value_id": "ET",
				},
				{"level_value_id": None},  # ignored
			]
		else:
			raise AssertionError(url)
		return _Resp(200, _ok({"response_payload": payload}), url)

	def _client(self, master_data_url="https://master.example"):
		return HttpDARegistryClient(
			"https://staff.example",
			keycloak={"token_url": "https://kc.example/token", "client_id": "c", "client_secret": "s"},
			master_data_url=master_data_url,
		)

	def test_levels_and_values_are_read_from_master_data_with_the_registry_token(self):
		with patch.object(http_mod.requests, "post", side_effect=self._post):
			levels = self._client().geo_levels()
			values = self._client().geo_level_values("l1")
		self.assertEqual(levels[1], {"level_id": "l1", "mnemonic": "region", "parent_level_id": "l0"})
		self.assertEqual(values, [{"code": "ET01", "name": "Tigray", "parent": "ET"}])
		self.assertTrue(all(url.startswith("https://master.example/geo/") for url, *_ in self.calls))
		self.assertEqual(self.calls[-1][1], {"level_id": "l1", "parent_level_value_id": ""})
		self.assertEqual(self.calls[-1][3]["Authorization"], "Bearer tok")

	def test_a_child_level_sends_the_parent(self):
		with patch.object(http_mod.requests, "post", side_effect=self._post):
			self._client().geo_level_values("l2", "ET01")
		self.assertEqual(self.calls[-1][1], {"level_id": "l2", "parent_level_value_id": "ET01"})

	def test_without_a_master_data_url_the_call_is_refused(self):
		with self.assertRaises(DARegistryError):
			self._client(master_data_url=None).geo_levels()
