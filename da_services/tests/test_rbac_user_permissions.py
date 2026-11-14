"""DA RBAC Assignment scopes -> User Permissions (services/rbac_user_permissions.py)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate

from da_services.services import constants as C
from da_services.services import kebele_user_permissions as kup
from da_services.services import rbac_user_permissions as rup
from da_services.setup.install import seed_not_assigned_kebele

USER = "rbac-up-test@example.com"
DA_ID = "DA-RUP-TEST"
GEO = (
	("DA Country", {"country_code": "XU", "country_name": "Upland"}),
	("DA Region", {"region_code": "XU01", "region_name": "Upland North", "country": "XU"}),
	("DA Region", {"region_code": "XU02", "region_name": "Upland South", "country": "XU"}),
	("DA Zone", {"zone_code": "XU0101", "zone_name": "North Zone", "region": "XU01"}),
	("DA Woreda", {"woreda_code": "XU010101", "woreda_name": "North Woreda", "zone": "XU0101"}),
	("DA Kebele", {"kebele_code": "XU01010101", "kebele_name": "North Kebele", "woreda": "XU010101"}),
)
KEBELE = "XU01010101"


def _perms(user=USER) -> set:
	rows = frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": ("in", list(rup.SCOPE_DOCTYPES.values()))},
		fields=["allow", "for_value"],
	)
	return {(r.allow, r.for_value) for r in rows}


def _not_assigned(user=USER) -> bool:
	return bool(
		frappe.db.exists(
			"User Permission", {"user": user, "allow": "DA Kebele", "for_value": C.NOT_ASSIGNED_KEBELE}
		)
	)


def _post(**values):
	"""An Effective DA Kebele Assignment for USER, in force today."""
	return frappe.get_doc(
		{
			"doctype": "DA Kebele Assignment",
			"user": USER,
			"kebele": KEBELE,
			"state": "Effective",
			"mode": "Automatic",
			"effective_date": nowdate(),
			"registry_sync_state": "Not sent",
			**values,
		}
	).insert(ignore_permissions=True)


def _assign(**values):
	doc = {
		"doctype": "DA RBAC Assignment",
		"user": USER,
		"role": C.ROLE_SUPERVISOR,
		"active": 1,
		"region_scope": "XU01",
		"zone_scope": "XU0101",
		"woreda_scope": "XU010101",
		"effective_from": nowdate(),
		**values,
	}
	return frappe.get_doc(doc).insert(ignore_permissions=True)


class TestRBACUserPermissions(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self._clear()
		if not frappe.db.exists("User", USER):
			frappe.get_doc(
				{"doctype": "User", "email": USER, "first_name": "RBAC UP", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.db.set_value("User", USER, "da_registry_id", DA_ID)  # a DA's DA-ID comes from the user
		seed_not_assigned_kebele()
		for doctype, values in GEO:
			code = next(v for k, v in values.items() if k.endswith("_code"))
			if not frappe.db.exists(doctype, code):
				frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)

	def tearDown(self):
		self._clear()
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	@staticmethod
	def _clear():
		frappe.db.delete("DA Kebele Assignment", {"user": USER})
		frappe.db.delete("DA RBAC Assignment", {"user": USER})
		frappe.db.delete("User Permission", {"user": USER})
		for doctype, values in reversed(GEO):
			frappe.db.delete(doctype, {"name": next(v for k, v in values.items() if k.endswith("_code"))})

	def test_save_creates_permissions_for_each_scope(self):
		_assign()
		self.assertEqual(
			_perms(),
			{("DA Region", "XU01"), ("DA Zone", "XU0101"), ("DA Woreda", "XU010101")},
		)

	def test_update_scope_and_deactivate(self):
		doc = _assign(role=C.ROLE_EXECUTIVE)
		doc.update({"region_scope": "XU02", "zone_scope": None, "woreda_scope": None})
		doc.save(ignore_permissions=True)
		self.assertEqual(_perms(), {("DA Region", "XU02")})

		doc.active = 0
		doc.save(ignore_permissions=True)
		self.assertEqual(_perms(), set())

	def test_window_future_start_and_end(self):
		tomorrow = add_days(nowdate(), 1)
		_assign(role=C.ROLE_EXECUTIVE, effective_from=tomorrow, zone_scope=None, woreda_scope=None)
		self.assertEqual(_perms(), set())  # not started yet

		rup.sync_all_user_permissions()  # today: still not started
		self.assertEqual(_perms(), set())
		rup.sync_user_permissions(USER, today=getdate(tomorrow))  # the day it starts
		self.assertEqual(_perms(), {("DA Region", "XU01")})

	def test_scheduler_revokes_after_effective_to(self):
		_assign(effective_to=nowdate())
		self.assertEqual(len(_perms()), 3)  # effective_to is the last day it holds
		rup.sync_user_permissions(USER, today=getdate(add_days(nowdate(), 1)))
		self.assertEqual(_perms(), set())

	def test_value_shared_by_two_assignments_survives_one_ending(self):
		ending = _assign(role=C.ROLE_EXECUTIVE, zone_scope=None, woreda_scope=None)
		_assign()
		ending.active = 0
		ending.save(ignore_permissions=True)
		self.assertIn(("DA Region", "XU01"), _perms())  # the Supervisor grant still needs it

	def test_delete_revokes(self):
		doc = _assign()
		doc.delete(ignore_permissions=True)
		self.assertEqual(_perms(), set())

	def test_development_agent_gets_not_assigned_kebele(self):
		_assign(role=C.ROLE_DA)
		self.assertTrue(_not_assigned())
		self.assertEqual(len(_perms()), 3)  # the scope permissions as usual

	def test_other_roles_get_no_not_assigned_kebele(self):
		_assign()
		self.assertFalse(_not_assigned())

	def test_not_assigned_follows_the_development_agent_window(self):
		doc = _assign(role=C.ROLE_DA, effective_to=nowdate())
		self.assertTrue(_not_assigned())
		rup.sync_user_permissions(USER, today=getdate(add_days(nowdate(), 1)))
		self.assertFalse(_not_assigned())  # ended
		rup.sync_user_permissions(USER)
		doc.active = 0
		doc.save(ignore_permissions=True)
		self.assertFalse(_not_assigned())

	def test_kebele_sync_leaves_not_assigned_alone(self):
		_assign(role=C.ROLE_DA)
		kup.sync_user_permissions(USER)
		kup.sync_all_user_permissions()
		self.assertTrue(_not_assigned())

	def test_kebele_assignment_removes_not_assigned_and_supersede_brings_it_back(self):
		_assign(role=C.ROLE_DA)
		self.assertTrue(_not_assigned())
		posting = _post()
		self.assertFalse(_not_assigned())  # the DA now has a DA Kebele permission
		self.assertTrue(rup.has_kebele_permission(USER))
		posting.state = "Superseded"
		posting.save(ignore_permissions=True)
		self.assertFalse(rup.has_kebele_permission(USER))
		self.assertTrue(_not_assigned())  # no Kebele left

	def test_no_not_assigned_when_the_da_already_has_a_kebele(self):
		_post()
		_assign(role=C.ROLE_DA)
		self.assertFalse(_not_assigned())
