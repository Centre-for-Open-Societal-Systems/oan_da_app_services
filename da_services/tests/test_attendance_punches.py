"""DA Attendance Punch: create + today-latest (no sequence checks)."""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime, today

from da_services.services import attendance_punches as svc
from da_services.services import constants as C
from da_services.tests.fixtures import live_geo, set_da_registry_id

DA_USER = "atp-da@da.local"
SUP_USER = "atp-sup@da.local"
DA_ID = "DA-ATP-0001"
SUP_DA_ID = "DA-ATP-SUP1"
ALL_USERS = (DA_USER, SUP_USER)


def _user(email: str, roles: list[str]) -> str:
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = email.split("@")[0]
		u.send_welcome_email = 0
		u.insert(ignore_permissions=True, ignore_links=True)
	u = frappe.get_doc("User", email)
	for role in roles:
		if role not in frappe.get_roles(email):
			u.add_roles(role)
	return email


class TestAttendancePunches(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.geo = live_geo()

	def setUp(self):
		frappe.set_user("Administrator")
		self._clean()
		_user(DA_USER, [C.ROLE_DA])
		_user(SUP_USER, [C.ROLE_SUPERVISOR])
		set_da_registry_id(DA_USER, DA_ID)
		set_da_registry_id(SUP_USER, SUP_DA_ID)

	def tearDown(self):
		frappe.set_user("Administrator")
		self._clean()

	def _clean(self):
		frappe.db.delete("DA Attendance Punch", {"user": ("in", ALL_USERS)})
		for email in ALL_USERS:
			if frappe.db.exists("User", email):
				frappe.db.set_value("User", email, "da_registry_id", None, update_modified=False)

	def _punch(self, *, action: str = "Punch In", woreda=True, kebele=True, punched_at=None, **over):
		body = {
			"action": action,
			"punched_at": punched_at or now_datetime(),
			"region": self.geo["region"],
			"zone": self.geo["zone"],
		}
		if woreda:
			body["woreda"] = self.geo["woreda"]
		if kebele:
			body["kebele"] = self.geo["kebele"]
		body.update(over)
		return svc.create_punch(**body)

	def test_today_empty_then_create_returns_row(self):
		frappe.set_user(DA_USER)
		self.assertIsNone(svc.get_today_latest())
		created = self._punch()
		self.assertEqual(created["action"], "Punch In")
		self.assertEqual(created["user"], DA_USER)
		self.assertEqual(created["da_id"], DA_ID)
		self.assertEqual(created["region"], self.geo["region"])
		self.assertEqual(created["zone"], self.geo["zone"])
		self.assertEqual(created["woreda"], self.geo["woreda"])
		self.assertEqual(created["kebele"], self.geo["kebele"])
		latest = svc.get_today_latest()
		self.assertIsNotNone(latest)
		self.assertEqual(latest["id"], created["id"])
		self.assertEqual(latest["punched_at"], created["punched_at"])

	def test_supervisor_region_zone_woreda_without_kebele(self):
		frappe.set_user(SUP_USER)
		created = self._punch(action="Punch In", kebele=False)
		self.assertEqual(created["da_id"], SUP_DA_ID)
		self.assertEqual(created["woreda"], self.geo["woreda"])
		self.assertFalse(created.get("kebele"))

	def test_multiple_punches_same_day_allowed(self):
		frappe.set_user(DA_USER)
		first = self._punch(action="Punch In")
		second = self._punch(action="Punch Out")
		self.assertNotEqual(first["id"], second["id"])
		latest = svc.get_today_latest()
		self.assertEqual(latest["id"], second["id"])
		self.assertEqual(latest["action"], "Punch Out")
		third = self._punch(action="Punch In")
		latest = svc.get_today_latest()
		self.assertEqual(latest["id"], third["id"])
		self.assertEqual(latest["action"], "Punch In")

	def test_yesterday_punch_not_in_today_latest(self):
		frappe.set_user(DA_USER)
		yesterday = f"{add_days(today(), -1)} 18:00:00"
		self._punch(action="Punch In", punched_at=yesterday)
		self.assertIsNone(svc.get_today_latest())
		today_row = self._punch(action="Punch In")
		latest = svc.get_today_latest()
		self.assertEqual(latest["id"], today_row["id"])

	def test_missing_da_registry_id_rejected(self):
		frappe.set_user(DA_USER)
		frappe.db.set_value("User", DA_USER, "da_registry_id", None, update_modified=False)
		with self.assertRaises(frappe.MandatoryError):
			self._punch()

	def test_missing_punched_at_rejected(self):
		frappe.set_user(DA_USER)
		with self.assertRaises(frappe.MandatoryError):
			svc.create_punch(
				action="Punch In",
				punched_at=None,
				region=self.geo["region"],
				zone=self.geo["zone"],
			)
