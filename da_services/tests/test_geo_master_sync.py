"""Master Data geography -> DA Country / Region / Zone / Woreda / Kebele (services/geo_master_sync.py)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from da_services.integrations.sso import SSOLoginResult
from da_services.services import geo_master_sync
from da_services.services.geo_master_sync import GeoSyncError, MasterDataSource

LEVEL_IDS = {"country": "l0", "region": "l1", "zone": "l2", "woreda": "l3", "kebele": "l4"}

# Every fake code starts with this prefix; the real synced masters are never touched.
FAKE_PREFIX = "XT"
_FAKE_ROWS = {"name": ("like", f"{FAKE_PREFIX}%")}


def _tree():
	return {
		"country": [("XT", None, "Testland")],
		"region": [("XT01", "XT", "North"), ("XT02", "XT", "South")],
		"zone": [("XT0101", "XT01", "North Zone")],
		"woreda": [("XT010101", "XT0101", "Woreda One")],
		"kebele": [(f"XT0101010{i}", "XT010101", f"Kebele {i}") for i in range(1, 6)],
	}


class FakeSource:
	def __init__(self, tree):
		self.tree = tree

	def geo_levels(self):
		return [{"level_id": i, "mnemonic": m} for m, i in LEVEL_IDS.items()]

	def geo_level_values_many(self, level_id, parents):
		level = next(m for m, i in LEVEL_IDS.items() if i == level_id)
		return {
			parent: [
				{"code": c, "name": n, "parent": p}
				for c, p, n in self.tree.get(level, [])
				if parent is None or p == parent
			]
			for parent in parents
		}


def _row(doctype, name):
	return frappe.db.get_value(doctype, name, "*", as_dict=True)


def _clear():
	"""Remove only the fake rows this module inserted (children first)."""
	for level in reversed(geo_master_sync.LEVELS):
		frappe.db.delete(geo_master_sync.DOCTYPES[level], _FAKE_ROWS)


_real_existing_rows = geo_master_sync._existing_rows


def _fake_existing_rows(doctype, level, depth):
	"""The fake sync sees only its own rows.

	``_sync`` deactivates every active row the source no longer lists and refuses a run
	that would deactivate more than a fifth of them. Hiding the real synced masters keeps
	both behaviours testable without wiping or deactivating production-like data.
	"""
	rows = _real_existing_rows(doctype, level, depth)
	return {code: row for code, row in rows.items() if code.startswith(FAKE_PREFIX)}


class TestGeoMasterSync(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		_clear()
		patcher = patch.object(geo_master_sync, "_existing_rows", _fake_existing_rows)
		patcher.start()
		self.addCleanup(patcher.stop)

	def tearDown(self):
		_clear()
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def test_first_run_inserts_with_parents(self):
		counts = geo_master_sync.sync_geo_masters(FakeSource(_tree()))
		self.assertEqual(counts["DA Country"]["inserted"], 1)
		self.assertEqual(counts["DA Region"]["inserted"], 2)
		self.assertEqual(counts["DA Kebele"]["inserted"], 5)
		kebele = _row("DA Kebele", "XT01010101")
		self.assertEqual((kebele.woreda, kebele.is_active), ("XT010101", 1))
		self.assertEqual(_row("DA Zone", "XT0101").region, "XT01")
		self.assertEqual(kebele.level_id, "l4")
		self.assertEqual(_row("DA Region", "XT02").country, "XT")

	def test_update_deactivate_and_reactivate(self):
		geo_master_sync.sync_geo_masters(FakeSource(_tree()))

		tree = _tree()
		tree["region"][0] = ("XT01", "XT", "North Renamed")
		tree["kebele"].pop()  # XT01010105 is gone
		counts = geo_master_sync.sync_geo_masters(FakeSource(tree))
		self.assertEqual(counts["DA Region"]["updated"], 1)
		self.assertEqual(counts["DA Region"]["unchanged"], 1)
		self.assertEqual(counts["DA Kebele"]["deactivated"], 1)
		self.assertEqual(_row("DA Region", "XT01").region_name, "North Renamed")
		self.assertEqual(_row("DA Kebele", "XT01010105").is_active, 0)

		counts = geo_master_sync.sync_geo_masters(FakeSource(_tree()))
		self.assertEqual(counts["DA Kebele"]["updated"], 1)
		self.assertEqual(_row("DA Kebele", "XT01010105").is_active, 1)

	def test_partial_answer_changes_nothing(self):
		geo_master_sync.sync_geo_masters(FakeSource(_tree()))
		tree = _tree()
		tree["region"][0] = ("XT01", "XT", "Should Not Stick")
		tree["kebele"] = tree["kebele"][:2]  # 3 of 5 gone: more than a fifth
		with self.assertRaises(GeoSyncError):
			geo_master_sync.sync_geo_masters(FakeSource(tree))
		self.assertEqual(_row("DA Region", "XT01").region_name, "North")
		self.assertEqual(frappe.db.count("DA Kebele", {"is_active": 1, **_FAKE_ROWS}), 5)

	def test_empty_level_changes_nothing(self):
		tree = _tree()
		tree["woreda"] = []
		with self.assertRaises(GeoSyncError):
			geo_master_sync.sync_geo_masters(FakeSource(tree))
		self.assertEqual(frappe.db.count("DA Country", _FAKE_ROWS), 0)


class _Resp:
	def __init__(self, payload, status=200):
		self.status_code = status
		self._payload = payload

	def json(self):
		return self._payload


def _ok(rows):
	return _Resp(
		{
			"response_header": {"response_status": "SUCCESS"},
			"response_body": {"pagination_response": None, "response_payload": rows},
		}
	)


SETTINGS = {
	"master_data_domain": "master-data.test",
	"master_sync_username": "sync-user",
	"master_sync_password": "s3cret",
	"master_sync_login_key": "agent",
}


class TestMasterDataSource(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		settings = frappe.get_single("DA Services Settings")
		self._saved = {k: settings.get(k) for k in SETTINGS}
		self._saved["master_sync_password"] = settings.get_password(
			"master_sync_password", raise_exception=False
		)
		settings.update(SETTINGS)
		settings.save()
		self.logins = []

	def tearDown(self):
		settings = frappe.get_single("DA Services Settings")
		settings.update({k: v or "" for k, v in self._saved.items()})
		settings.save()

	def _login(self, **kwargs):
		self.logins.append(kwargs)
		n = len(self.logins)
		return SSOLoginResult(access_token=f"tok{n}", csrf_token=f"csrf{n}", user={})

	def test_login_from_settings_and_tokens_sent(self):
		calls = []

		def post(url, headers, cookies, json, timeout):
			calls.append((url, headers, cookies, json))
			return _ok([{"level_id": "l0", "level_mnemonic": "country", "parent_level_id": None}])

		with patch("da_services.integrations.master.client.requests.post", side_effect=post):
			levels = MasterDataSource(login=self._login).geo_levels()

		self.assertEqual(levels, [{"level_id": "l0", "mnemonic": "country"}])
		login = self.logins[0]
		self.assertEqual(
			(login["username"], login["password"], login["key"]), ("sync-user", "s3cret", "agent")
		)
		url, headers, cookies, _body = calls[0]
		self.assertEqual(url, "https://master-data.test/geo/get_all_geo_levels")
		self.assertEqual(headers["Authorization"], "Bearer tok1")
		self.assertEqual(headers["X-CSRF-Token"], "csrf1")
		self.assertEqual(cookies, {"X-CSRF-Token": "csrf1"})

	def test_pages_and_relogin_on_expired_token(self):
		page_one = [
			{
				"level_value_id": f"XT01{i:02d}",
				"level_value_mnemonic": f"Z{i}",
				"parent_level_value_id": "XT01",
			}
			for i in range(geo_master_sync.PAGE_SIZE)
		]
		page_two = [
			{"level_value_id": "XT01ZZ", "level_value_mnemonic": "Last", "parent_level_value_id": "XT01"}
		]
		sent = []

		def post(url, headers, cookies, json, timeout):
			sent.append((headers["Authorization"], json["request_body"]))
			if headers["Authorization"] == "Bearer tok1":
				return _Resp({}, status=401)  # the first token has expired
			body = json["request_body"]
			self.assertEqual(body["request_payload"], {"level_id": "l2", "parent_level_value_id": "XT01"})
			return _ok(page_one if body["pagination_request"]["current_page"] == 1 else page_two)

		source = MasterDataSource(login=self._login)
		with patch("da_services.integrations.master.client.requests.post", side_effect=post):
			values = source.geo_level_values_many("l2", ["XT01"])

		self.assertEqual(len(self.logins), 2)
		self.assertEqual(len(values["XT01"]), geo_master_sync.PAGE_SIZE + 1)
		self.assertEqual(values["XT01"][-1], {"code": "XT01ZZ", "name": "Last", "parent": "XT01"})

	def test_missing_settings_refused(self):
		frappe.db.set_single_value("DA Services Settings", "master_sync_username", "")
		frappe.clear_document_cache("DA Services Settings", "DA Services Settings")
		with self.assertRaises(GeoSyncError) as ctx:
			MasterDataSource(login=self._login).geo_levels()
		self.assertIn("Username", str(ctx.exception))
		self.assertEqual(self.logins, [])
