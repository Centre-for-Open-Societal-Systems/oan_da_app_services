"""Master Data geography -> DA Administrative Area (services/area_sync.py)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from da_services.integrations.da_registry import http as http_mod
from da_services.integrations.da_registry.client import DARegistryError, DARegistryUnavailable
from da_services.integrations.da_registry.http import HttpDARegistryClient
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import area_sync
from da_services.tests.test_da_registry_http import _ok, _Resp

# A small Master Data. Codes start with ET7 so the demo and fixture areas are never touched.
TREE = {
	"region": [("ET77", None, "Testland")],
	"zone": [("ET7701", "ET77", "North Test")],
	"woreda": [("ET770101", "ET7701", "Test Woreda"), ("ET770102", "ET7701", "Other Woreda")],
	"kebele": [
		("ET77010101", "ET770101", "Kebele One"),
		("ET77010102", "ET770101", "Kebele Two"),
		("ET77010201", "ET770102", "Kebele Three"),
	],
}
LEVEL_IDS = {"region": "l1", "zone": "l2", "woreda": "l3", "kebele": "l4"}


class FakeMasterData:
	"""Answers geo_levels / geo_level_values from a TREE, the way the client does."""

	def __init__(self, tree, levels=("region", "zone", "woreda", "kebele")):
		self.tree = tree
		self.levels = levels
		self.calls = []

	def geo_levels(self):
		return [{"level_id": "l0", "mnemonic": "country", "parent_level_id": None}] + [
			{"level_id": LEVEL_IDS[m], "mnemonic": m, "parent_level_id": None} for m in self.levels
		]

	def geo_level_values(self, level_id, parent=None):
		self.calls.append((level_id, parent))
		mnemonic = next(m for m, i in LEVEL_IDS.items() if i == level_id)
		return [
			{"code": code, "name": name, "parent": p}
			for code, p, name in self.tree.get(mnemonic, [])
			if parent is None or p == parent
		]

	def geo_level_values_many(self, level_id, parents):
		return {parent: self.geo_level_values(level_id, parent) for parent in parents}


def _rows() -> dict:
	rows = frappe.get_all(
		"DA Administrative Area",
		filters={"name": ("like", "ET7%")},
		fields=["name", "level", "parent_area", "name_en", "active", "source"],
	)
	return {r.name: r for r in rows}


class TestAreaSync(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA Administrative Area", {"name": ("like", "ET7%")})

	def tearDown(self):
		frappe.db.delete("DA Administrative Area", {"name": ("like", "ET7%")})
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def test_the_whole_tree_is_loaded_with_parents_and_names(self):
		md = FakeMasterData(TREE)
		counts = area_sync.sync_areas(md)
		self.assertEqual(counts, {"inserted": 7, "updated": 0, "unchanged": 0, "deactivated": 0})
		rows = _rows()
		self.assertEqual((rows["ET77"].level, rows["ET77"].parent_area), ("Region", None))
		woreda = rows["ET770101"]
		self.assertEqual(
			(woreda.level, woreda.parent_area, woreda.name_en), ("Woreda", "ET7701", "Test Woreda")
		)
		self.assertEqual(rows["ET77010201"].parent_area, "ET770102")
		self.assertTrue(all(r.source == "master_data" and r.active for r in rows.values()))
		# level by level, one call per parent; the country level is not walked
		self.assertEqual(md.calls[:3], [("l1", None), ("l2", "ET77"), ("l3", "ET7701")])
		self.assertIn(("l4", "ET770102"), md.calls)

	def test_a_second_run_updates_in_place_and_deactivates_what_master_data_dropped(self):
		area_sync.sync_areas(FakeMasterData(TREE))
		changed = {
			**TREE,
			"woreda": [("ET770101", "ET7701", "Renamed Woreda"), ("ET770102", "ET7701", "Other Woreda")],
			"kebele": TREE["kebele"][:2],
		}
		counts = area_sync.sync_areas(FakeMasterData(changed))
		self.assertEqual((counts["inserted"], counts["updated"], counts["deactivated"]), (0, 1, 1))
		rows = _rows()
		self.assertEqual(len(rows), 7)  # nothing deleted
		self.assertEqual(rows["ET770101"].name_en, "Renamed Woreda")
		self.assertFalse(rows["ET77010201"].active)  # kept for the assignments that link to it
		# listed again: the kebele is back to active and the woreda gets its name back
		counts = area_sync.sync_areas(FakeMasterData(TREE))
		self.assertEqual((counts["updated"], counts["deactivated"]), (2, 0))
		self.assertTrue(_rows()["ET77010201"].active)
		self.assertEqual(_rows()["ET770101"].name_en, "Test Woreda")

	def test_rows_from_other_sources_are_left_alone(self):
		frappe.get_doc(
			{
				"doctype": "DA Administrative Area",
				"code": "ET78",
				"level": "Region",
				"name_en": "Seeded",
				"active": 1,
			}
		).insert(ignore_permissions=True)
		area_sync.sync_areas(FakeMasterData(TREE))
		self.assertTrue(frappe.db.get_value("DA Administrative Area", "ET78", "active"))

	def test_a_partial_answer_changes_nothing(self):
		area_sync.sync_areas(FakeMasterData(TREE))
		# a level comes back empty (parent id mismatch, permission regression): no mass deactivation
		with self.assertRaises(DARegistryError):
			area_sync.sync_areas(FakeMasterData({**TREE, "kebele": []}))
		# too many areas gone at once: Master Data answered partially
		with self.assertRaises(DARegistryError):
			area_sync.sync_areas(FakeMasterData({**TREE, "kebele": TREE["kebele"][:1]}))
		self.assertTrue(all(r.active for r in _rows().values()))
		self.assertEqual(len(_rows()), 7)

	def test_a_failure_half_way_rolls_everything_back(self):
		class Flaky(FakeMasterData):
			def geo_level_values(self, level_id, parent=None):
				if level_id == "l3":
					raise DARegistryUnavailable("Master Data timed out")
				return super().geo_level_values(level_id, parent)

		with self.assertRaises(DARegistryUnavailable):
			area_sync.sync_areas(Flaky(TREE))
		self.assertEqual(_rows(), {})  # the regions and zones already upserted are gone
		with (
			patch.object(frappe, "log_error") as log,
			patch.object(area_sync, "get_client", return_value=Flaky(TREE)),
		):
			area_sync.sync_areas_scheduled()  # logged and skipped, nothing committed
		self.assertTrue(log.called)
		self.assertEqual(_rows(), {})

	def test_a_code_listed_twice_keeps_its_first_parent(self):
		tree = {**TREE, "kebele": TREE["kebele"] + [("ET77010101", "ET770102", "Kebele One again")]}
		counts = area_sync.sync_areas(FakeMasterData(tree))
		self.assertEqual(counts["inserted"], 7)
		self.assertEqual(_rows()["ET77010101"].parent_area, "ET770101")

	def test_an_empty_or_incomplete_master_data_changes_nothing(self):
		area_sync.sync_areas(FakeMasterData(TREE))
		with self.assertRaises(DARegistryError):
			area_sync.sync_areas(FakeMasterData({k: [] for k in TREE}))
		with self.assertRaises(DARegistryError):
			area_sync.sync_areas(FakeMasterData(TREE, levels=("region", "zone", "woreda")))  # no kebele level
		self.assertTrue(all(r.active for r in _rows().values()))
		self.assertEqual(len(_rows()), 7)

	def test_the_mock_backend_loads_the_demo_geography(self):
		before = {
			r.name: r
			for r in frappe.get_all(
				"DA Administrative Area",
				filters={"name": ("like", "ET0%")},
				fields=["name", "source", "active"],
			)
		}
		try:
			counts = area_sync.sync_areas(MockDARegistryClient())
			self.assertEqual(sum(counts[k] for k in ("inserted", "updated", "unchanged")), 13)
			self.assertEqual(
				frappe.db.get_value(
					"DA Administrative Area", "ET04-W01-K03", ["level", "parent_area", "name_en"]
				),
				("Kebele", "ET04-W01", "Dambi Gobu"),
			)
		finally:
			# Other test modules rely on these fixture rows: put them back as they were.
			synced = frappe.get_all(
				"DA Administrative Area",
				filters={"source": area_sync.SOURCE, "name": ("like", "ET0%")},
				pluck="name",
			)
			for code in synced:
				if code in before:
					frappe.db.set_value(
						"DA Administrative Area",
						code,
						{"source": before[code].source, "active": before[code].active},
					)
				else:
					frappe.delete_doc("DA Administrative Area", code, ignore_permissions=True, force=True)

	def test_the_scheduled_run_needs_a_service_identity(self):
		keys = {
			"da_registry_backend": "http",
			"da_registry_auth": "user",
			"da_registry_base_url": "https://staff.example",
			"da_registry_keycloak": {},
		}
		with patch.dict(frappe.conf, keys), patch.object(frappe, "log_error") as log:
			area_sync.sync_areas_scheduled()  # no exception
		self.assertTrue(log.called)


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

	def test_values_for_many_parents_come_back_keyed_by_parent(self):
		with patch.object(http_mod.requests, "post", side_effect=self._post):
			out = self._client().geo_level_values_many("l2", ["ET01", "ET02"])
		self.assertEqual(set(out), {"ET01", "ET02"})
		self.assertEqual(out["ET01"], [{"code": "ET01", "name": "Tigray", "parent": "ET"}])
		sent = sorted(
			c[1]["parent_level_value_id"] for c in self.calls if c[0].endswith("get_geo_level_values")
		)
		self.assertEqual(sent, ["ET01", "ET02"])

	def test_without_a_master_data_url_the_call_is_refused(self):
		with self.assertRaises(DARegistryError):
			self._client(master_data_url=None).geo_levels()
