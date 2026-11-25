"""DA Registry -> DA RBAC / DA Kebele Assignment, from the User hook
(services/da_registry_assignment_sync.py), and the shared staff-API helpers it uses
(services/utils.py).

The registry call is replaced by patching ``fetch_registry_record`` for the hook tests,
and ``requests.post`` / the SSO login for the request-shape tests; nothing leaves the box.
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.integrations.sso import SSOLoginResult
from da_services.services import constants as C
from da_services.services import da_registry_assignment_sync as sync
from da_services.services import utils
from da_services.tests.fixtures import ensure_geo_masters

TAG = "darsync"
DA_ID = "424b1f9d-13d0-41d1-b1b3-d891ce2b2a98"
DA_ID_2 = "424b1f9d-13d0-41d1-b1b3-d891ce2b2a99"
RECORD = {
	"internal_record_id": DA_ID,
	"region_code": "ET04",
	"zone_code": "ET04-Z01",
	"woreda_code": "ET04-W01",
	"kebele_code": "ET04-W01-K01",
}
RECORD_MOVED = {
	"region_code": "ET03",
	"zone_code": "ET03-Z02",
	"woreda_code": "ET03-W02",
	"kebele_code": "ET03-W02-K01",
}
SETTINGS = {
	"da_registry_domain": "da-registry.test",
	"da_registry_form_register_id": "6f1c2d8e-4a7b-4c3e-9d2f-8b5a1e7c3f90",
	"master_sync_username": "sync-user",
	"master_sync_password": "s3cret",
	"master_sync_login_key": "agent",
}
FETCH = "da_services.services.da_registry_assignment_sync.fetch_registry_record"


def _email(n: int) -> str:
	return f"{TAG}-{n}@da-test.local"


def _cleanup():
	frappe.set_user("Administrator")
	users = frappe.get_all("User", filters={"name": ("like", f"{TAG}-%")}, pluck="name")
	if users:
		# DA Hierarchy nodes, leaves first (a node with children cannot be deleted).
		for node in frappe.get_all(
			sync.HIERARCHY, filters={"user": ("in", users)}, pluck="name", order_by="lft desc"
		):
			frappe.delete_doc(sync.HIERARCHY, node, force=True, ignore_permissions=True)
		for dt in (sync.RBAC, sync.KEBELE_ASSIGNMENT, "User Permission"):
			frappe.db.delete(dt, {"user": ("in", users)})
		for user in users:
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	frappe.db.delete("User", {"da_registry_id": ("in", [DA_ID, DA_ID_2])})


def _response(status=200, body=None):
	response = MagicMock()
	response.status_code = status
	response.json.return_value = body
	response.text = ""
	return response


def _ok(payload):
	return {"response_header": {"response_status": "SUCCESS"}, "response_body": {"response_payload": payload}}


class TestUserHook(IntegrationTestCase):
	def setUp(self):
		_cleanup()
		ensure_geo_masters()

	def tearDown(self):
		_cleanup()

	def _user(self, n=1, da_id=DA_ID):
		doc = frappe.get_doc(
			{
				"doctype": "User",
				"email": _email(n),
				"first_name": f"{TAG}{n}",
				"send_welcome_email": 0,
				"da_registry_id": da_id,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def _rbac(self, user, **filters):
		return frappe.get_all(
			sync.RBAC,
			filters={"user": user, **filters},
			fields=[
				"name",
				"role",
				"active",
				"region_scope",
				"zone_scope",
				"woreda_scope",
				"effective_from",
				"notes",
			],
			order_by="creation asc",
		)

	def _kebele(self, user, **filters):
		return frappe.get_all(
			sync.KEBELE_ASSIGNMENT,
			filters={"user": user, **filters},
			fields=["name", "kebele", "state", "mode", "effective_date", "reason", "da_id"],
			order_by="creation asc",
		)

	def test_create_with_da_id_creates_rbac_and_kebele_assignments(self):
		with patch(FETCH, return_value=RECORD) as fetch:
			user = self._user().name
		fetch.assert_called_once_with(DA_ID)

		[rbac] = self._rbac(user)
		self.assertEqual(rbac.role, C.ROLE_DA)
		self.assertEqual(rbac.active, 1)
		self.assertEqual(
			(rbac.region_scope, rbac.zone_scope, rbac.woreda_scope), ("ET04", "ET04-Z01", "ET04-W01")
		)
		self.assertEqual(str(rbac.effective_from), today())
		self.assertEqual(rbac.notes, sync.API_NOTE)

		[kebele] = self._kebele(user)
		self.assertEqual(
			(kebele.kebele, kebele.state, kebele.mode), ("ET04-W01-K01", "Effective", "Automatic")
		)
		self.assertEqual(str(kebele.effective_date), today())
		self.assertEqual(kebele.reason, sync.API_NOTE)
		self.assertEqual(kebele.da_id, DA_ID)

	def test_after_insert_syncs_and_on_update_of_the_same_insert_skips(self):
		doc = frappe.new_doc("User")
		doc.update({"email": _email(9), "first_name": f"{TAG}9", "da_registry_id": DA_ID})
		with patch.object(sync, "sync_user_assignments") as run:
			doc.flags.in_insert = False  # as Frappe has it during after_insert
			sync.on_user_update(doc, "after_insert")
			doc.flags.in_insert = True
			sync.on_user_update(doc, "on_update")
		run.assert_called_once_with(doc.name, DA_ID)
		self.assertNotIn("da_registry_synced_on_insert", doc.flags)

	def test_create_without_da_id_does_not_call_the_registry(self):
		with patch(FETCH) as fetch:
			user = self._user(da_id=None).name
		fetch.assert_not_called()
		self.assertEqual(self._rbac(user), [])

	def test_no_kebele_code_creates_only_the_rbac_assignment(self):
		record = {k: v for k, v in RECORD.items() if k != "kebele_code"}
		with patch(FETCH, return_value=record):
			user = self._user().name
		self.assertEqual(len(self._rbac(user)), 1)
		self.assertEqual(self._kebele(user), [])

	def test_saving_without_changing_the_da_id_does_not_call_the_registry(self):
		with patch(FETCH, return_value=RECORD):
			doc = self._user()
		with patch(FETCH) as fetch:
			doc.reload()
			doc.last_name = "Changed"
			doc.save(ignore_permissions=True)
		fetch.assert_not_called()

	def test_the_failure_message_hides_unexpected_errors(self):
		self.assertEqual(sync._user_message(utils.StaffAPIError("set X", kind="config")), "set X")
		self.assertEqual(
			sync._user_message(
				utils.StaffAPIError("ConnectionError(host=internal.local)", kind="unavailable")
			),
			"see the Error Log",
		)

	def test_a_manual_assignment_is_left_active(self):
		with patch(FETCH) as _:
			doc = self._user(da_id=None)
		frappe.db.set_value("User", doc.name, "da_registry_id", DA_ID)
		manual = frappe.get_doc(
			{
				"doctype": sync.RBAC,
				"user": doc.name,
				"role": C.ROLE_DA,
				"active": 1,
				"region_scope": "ET03",
				"effective_from": today(),
				"notes": "granted by hand",
			}
		).insert(ignore_permissions=True)

		self.assertIsNotNone(sync.sync_user_assignments(doc.name, DA_ID, fetch=lambda _id: RECORD)["rbac"])
		self.assertEqual(frappe.db.get_value(sync.RBAC, manual.name, "active"), 1)

	def test_the_same_record_twice_creates_nothing_new(self):
		with patch(FETCH, return_value=RECORD):
			user = self._user().name
		again = sync.sync_user_assignments(user, DA_ID, fetch=lambda _id: RECORD)
		self.assertEqual(again, {"rbac": None, "kebele": None, "hierarchy": user})  # node updated in place
		self.assertEqual(len(self._rbac(user)), 1)
		self.assertEqual(len(self._kebele(user)), 1)

	def test_a_registry_failure_still_saves_the_user_and_creates_nothing(self):
		with patch(FETCH, side_effect=utils.StaffAPIError("Token refused", kind="unauthorized")):
			user = self._user().name
		self.assertTrue(frappe.db.exists("User", user))
		self.assertEqual(self._rbac(user), [])
		self.assertEqual(self._kebele(user), [])

	def test_an_unknown_kebele_rolls_back_the_rbac_assignment_too(self):
		with patch(FETCH, return_value={**RECORD, "kebele_code": "NO-SUCH-KEBELE"}):
			user = self._user().name
		self.assertTrue(frappe.db.exists("User", user))
		self.assertEqual(self._rbac(user), [])
		self.assertEqual(self._kebele(user), [])

	def test_a_record_without_region_code_skips_only_the_rbac_assignment(self):
		with patch(FETCH, return_value={"kebele_code": "ET04-W01-K01"}):
			user = self._user().name
		self.assertEqual(self._rbac(user), [])
		self.assertEqual(len(self._kebele(user)), 1)
		self.assertTrue(frappe.db.exists("DA Hierarchy", {"user": user}))


class TestHierarchySync(IntegrationTestCase):
	"""The DA Hierarchy node made from the record, and its User Permissions."""

	def setUp(self):
		_cleanup()
		ensure_geo_masters()

	def tearDown(self):
		_cleanup()

	def _user(self, n, username=None):
		doc = frappe.get_doc(
			{
				"doctype": "User",
				"email": _email(n),
				"first_name": f"{TAG}{n}",
				"username": username,
				"send_welcome_email": 0,
			}
		)
		doc.insert(ignore_permissions=True)
		# Set after insert: the User hook syncs only on insert, and the Kebele step needs it.
		frappe.db.set_value("User", doc.name, "da_registry_id", f"{TAG}-da-{n}")
		return doc.name

	def _sync(self, user, **record):
		return sync.sync_user_assignments(user, DA_ID, fetch=lambda _id: {**RECORD, **record})

	def _perms(self, user):
		return set(
			frappe.get_all("User Permission", filters={"user": user, "allow": "User"}, pluck="for_value")
		)

	def test_the_node_is_filled_from_the_record(self):
		user = self._user(1)
		out = self._sync(user, position="Development Agent", country_code="ET")
		node = frappe.get_doc(sync.HIERARCHY, out["hierarchy"])
		self.assertEqual(node.user, user)
		# designation and country are not taken from the record
		self.assertIsNone(node.designation)
		self.assertIsNone(node.country)
		self.assertEqual(
			(node.region, node.zone, node.woreda, node.kebele),
			("ET04", "ET04-Z01", "ET04-W01", "ET04-W01-K01"),
		)
		self.assertEqual(node.parent_da_hierarchy, "All DA Hierarchy")  # no reports_to
		self.assertEqual(node.is_group, 0)
		self.assertEqual(self._perms(user), {user})  # only themselves

	def test_reports_to_places_the_user_under_their_boss(self):
		boss = self._user(2, username=f"{TAG}boss")
		user = self._user(1)
		self._sync(user, reports_to=f"{TAG}boss")
		boss_node = frappe.get_doc(sync.HIERARCHY, {"user": boss})  # created for the boss
		self.assertEqual(boss_node.is_group, 1)
		self.assertEqual(frappe.db.get_value(sync.HIERARCHY, {"user": user}, "parent_da_hierarchy"), boss)
		self.assertEqual(self._perms(boss), {boss, user})  # the boss sees the report
		self.assertEqual(self._perms(user), {user})

	def test_a_manager_sees_every_level_below(self):
		top = self._user(3, username=f"{TAG}top")
		mid = self._user(2, username=f"{TAG}mid")
		da = self._user(1)
		self._sync(mid, reports_to=f"{TAG}top")
		self._sync(da, reports_to=f"{TAG}mid")
		self.assertEqual(self._perms(top), {top, mid, da})
		self.assertEqual(self._perms(mid), {mid, da})
		self.assertEqual(frappe.db.get_value(sync.HIERARCHY, mid, "is_group"), 1)

	def test_moving_to_another_boss_moves_the_permission(self):
		old = self._user(2, username=f"{TAG}old")
		new = self._user(3, username=f"{TAG}new")
		da = self._user(1)
		self._sync(da, reports_to=f"{TAG}old")
		self._sync(da, reports_to=f"{TAG}new")
		self.assertEqual(self._perms(old), {old})
		self.assertEqual(self._perms(new), {new, da})

	def test_an_unknown_reports_to_goes_under_the_root(self):
		user = self._user(1)
		self._sync(user, reports_to="nobody-by-this-name")
		self.assertEqual(
			frappe.db.get_value(sync.HIERARCHY, {"user": user}, "parent_da_hierarchy"), "All DA Hierarchy"
		)

	def test_the_daily_job_rebuilds_missing_permissions(self):
		from da_services.services import hierarchy_user_permissions as hup

		boss = self._user(2, username=f"{TAG}boss")
		da = self._user(1)
		self._sync(da, reports_to=f"{TAG}boss")
		frappe.db.delete("User Permission", {"user": boss, "allow": "User"})
		with patch.object(frappe.db, "commit"):  # sync_each commits per user; keep the test's rollback
			hup.sync_all_user_permissions()
		self.assertEqual(self._perms(boss), {boss, da})

	def test_only_user_is_restricted(self):
		user = self._user(1)
		self._sync(user, position="Development Agent", country_code="ET")
		allows = set(frappe.get_all("User Permission", filters={"user": user}, pluck="allow"))
		self.assertIn("User", allows)
		self.assertNotIn(sync.HIERARCHY, allows)
		self.assertNotIn("DA Country", allows)

	def test_deleting_a_node_removes_the_permissions(self):
		boss = self._user(2, username=f"{TAG}boss")
		da = self._user(1)
		self._sync(da, reports_to=f"{TAG}boss")
		frappe.delete_doc(sync.HIERARCHY, da, ignore_permissions=True)
		self.assertEqual(self._perms(da), set())
		self.assertEqual(self._perms(boss), {boss})


class TestRegistryRequest(IntegrationTestCase):
	"""fetch_registry_record and the services/utils helpers."""

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
		return SSOLoginResult(access_token="tok1", csrf_token="csrf1", user={})

	def test_request_uses_settings_da_id_and_the_sync_account_tokens(self):
		with (
			patch("da_services.services.utils.sso_login", side_effect=self._login),
			patch(
				"da_services.services.utils.requests.post", return_value=_response(body=_ok(RECORD))
			) as post,
		):
			record = sync.fetch_registry_record(DA_ID)

		self.assertEqual(record, RECORD)
		self.assertEqual(self.logins[0]["username"], "sync-user")
		self.assertEqual(self.logins[0]["key"], "agent")
		args, kwargs = post.call_args
		self.assertEqual(args[0], "https://da-registry.test/register-data/get_subject_record")
		self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok1")
		self.assertEqual(kwargs["headers"]["X-CSRF-Token"], "csrf1")
		self.assertEqual(kwargs["cookies"], {"X-CSRF-Token": "csrf1"})
		self.assertEqual(
			kwargs["json"]["request_body"]["request_payload"],
			{"subject_register_id": SETTINGS["da_registry_form_register_id"], "subject_record_id": DA_ID},
		)
		self.assertEqual(kwargs["json"]["request_header"]["sender_app_mnemonic"], utils.sender_app())

	def test_missing_settings_are_named_before_any_login(self):
		frappe.db.set_single_value("DA Services Settings", "da_registry_form_register_id", "")
		frappe.db.set_single_value("DA Services Settings", "master_sync_username", "")
		frappe.clear_document_cache("DA Services Settings", "DA Services Settings")
		with (
			patch("da_services.services.utils.sso_login", side_effect=self._login),
			self.assertRaises(utils.StaffAPIError) as ctx,
		):
			sync.fetch_registry_record(DA_ID)
		self.assertEqual(ctx.exception.kind, "config")
		self.assertIn("DA Registry Form Register ID", str(ctx.exception))
		self.assertIn("Username", str(ctx.exception))
		self.assertEqual(self.logins, [])

	def test_registry_errors_are_classified(self):
		tokens = SSOLoginResult(access_token="t", csrf_token="c", user={})
		cases = (
			(_response(401, {}), "unauthorized"),
			(_response(503, {}), "unavailable"),
			(
				_response(
					200, {"response_header": {"response_status": "FAILURE", "response_error_message": "no"}}
				),
				"rejected",
			),
			(_response(200, None), "rejected"),
		)
		for response, kind in cases:
			with (
				self.subTest(kind=kind),
				patch("da_services.services.utils.requests.post", return_value=response),
			):
				with self.assertRaises(utils.StaffAPIError) as ctx:
					utils.post_staff_api("https://x.test/p", {}, tokens=tokens, action="test")
				self.assertEqual(ctx.exception.kind, kind)

	def test_an_empty_record_is_refused(self):
		with (
			patch("da_services.services.utils.sso_login", side_effect=self._login),
			patch("da_services.services.utils.requests.post", return_value=_response(body=_ok({}))),
			self.assertRaises(sync.AssignmentSyncError),
		):
			sync.fetch_registry_record(DA_ID)

	def test_base_url_from_domain(self):
		self.assertEqual(utils.base_url_from_domain("host.test/", "X"), "https://host.test")
		self.assertEqual(utils.base_url_from_domain("http://host.test", "X"), "http://host.test")
		with self.assertRaises(utils.StaffAPIError):
			utils.base_url_from_domain("", "DA Registry Domain")
