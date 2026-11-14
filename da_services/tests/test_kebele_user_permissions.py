"""DA Kebele Assignment -> User Permission on DA Kebele (services/kebele_user_permissions.py)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate

from da_services.services import kebele_user_permissions as kup

USER = "kebele-up-test@example.com"
DA_ID = "DA-KUP-TEST"
GEO = (
	("DA Country", {"country_code": "XK", "country_name": "Kebeleland"}),
	("DA Region", {"region_code": "XK01", "region_name": "K Region", "country": "XK"}),
	("DA Zone", {"zone_code": "XK0101", "zone_name": "K Zone", "region": "XK01"}),
	("DA Woreda", {"woreda_code": "XK010101", "woreda_name": "K Woreda", "zone": "XK0101"}),
	("DA Kebele", {"kebele_code": "XK01010101", "kebele_name": "Kebele A", "woreda": "XK010101"}),
	("DA Kebele", {"kebele_code": "XK01010102", "kebele_name": "Kebele B", "woreda": "XK010101"}),
)
KEBELE_A, KEBELE_B = "XK01010101", "XK01010102"


def _code(values):
	return next(v for k, v in values.items() if k.endswith("_code"))


def _perms() -> set:
	return set(
		frappe.get_all("User Permission", filters={"user": USER, "allow": kup.ALLOW}, pluck="for_value")
	)


def _assign(kebele=KEBELE_A, **values):
	return frappe.get_doc(
		{
			"doctype": "DA Kebele Assignment",
			"user": USER,
			"kebele": kebele,
			"state": "Effective",
			"mode": "Automatic",
			"effective_date": nowdate(),
			"registry_sync_state": "Not sent",
			**values,
		}
	).insert(ignore_permissions=True)


class TestKebeleUserPermissions(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self._clear()
		if not frappe.db.exists("User", USER):
			frappe.get_doc(
				{"doctype": "User", "email": USER, "first_name": "Kebele UP", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.db.set_value("User", USER, "da_registry_id", DA_ID)
		for doctype, values in GEO:
			if not frappe.db.exists(doctype, _code(values)):
				frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)

	def tearDown(self):
		self._clear()
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	@staticmethod
	def _clear():
		frappe.db.delete("DA Kebele Assignment", {"user": USER})
		frappe.db.delete("User Permission", {"user": USER})
		for doctype, values in reversed(GEO):
			frappe.db.delete(doctype, {"name": _code(values)})

	def test_effective_creates_permission_and_fills_scope(self):
		doc = _assign()
		self.assertEqual(_perms(), {KEBELE_A})
		self.assertEqual((doc.da_id, doc.woreda, doc.zone, doc.region), (DA_ID, "XK010101", "XK0101", "XK01"))

	def test_reassignment_moves_permission(self):
		first = _assign()
		_assign(kebele=KEBELE_B)
		self.assertEqual(frappe.db.get_value("DA Kebele Assignment", first.name, "state"), "Superseded")
		self.assertEqual(_perms(), {KEBELE_B})

	def test_flagged_grants_nothing_and_keeps_posting_in_force(self):
		_assign()
		_assign(kebele=KEBELE_B, state="Flagged")
		self.assertEqual(_perms(), {KEBELE_A})

	def test_future_effective_date_granted_by_scheduler_on_the_day(self):
		tomorrow = add_days(nowdate(), 1)
		_assign(effective_date=tomorrow)
		self.assertEqual(_perms(), set())
		kup.sync_all_user_permissions()  # today: not yet
		self.assertEqual(_perms(), set())
		kup.sync_user_permissions(USER, today=getdate(tomorrow))
		self.assertEqual(_perms(), {KEBELE_A})

	def test_state_change_on_save_revokes(self):
		doc = _assign()
		doc.state = "Superseded"
		doc.save(ignore_permissions=True)
		self.assertEqual(_perms(), set())
