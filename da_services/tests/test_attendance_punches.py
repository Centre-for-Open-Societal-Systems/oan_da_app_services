"""DA Attendance Punch: create + today-latest with next_action."""

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


def _split_now(when=None) -> tuple[str, str]:
	dt = when or now_datetime()
	stamp = str(dt)
	# "YYYY-MM-DD HH:MM:SS[.ffffff]"
	date_part, time_part = stamp.split(" ", 1)
	if "." in time_part:
		time_part = time_part.split(".", 1)[0]
	return date_part, time_part


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

	def _punch(self, *, action: str = "Punch In", woreda=True, kebele=True, when=None, **over):
		punched_date, punched_time = _split_now(when)
		body = {
			"action": action,
			"punched_date": punched_date,
			"punched_time": punched_time,
			"region": self.geo["region"],
			"zone": self.geo["zone"],
		}
		if woreda:
			body["woreda"] = self.geo["woreda"]
		if kebele:
			body["kebele"] = self.geo["kebele"]
		body.update(over)
		svc.create_punch(**body)
		# Latest for this user/day after insert (tests assert via get_today_latest / get_doc).
		return frappe.get_all(
			"DA Attendance Punch",
			filters={"user": frappe.session.user},
			fields=[
				"name",
				"action",
				"punched_date",
				"punched_time",
				"da_id",
				"region",
				"zone",
				"woreda",
				"kebele",
				"gps_lat",
				"gps_lng",
			],
			order_by="creation desc",
			limit=1,
		)[0]

	def test_today_empty_then_create_and_next_action(self):
		frappe.set_user(DA_USER)
		empty = svc.get_today_latest()
		self.assertEqual(empty["next_action"], "Punch In")
		self.assertIsNone(empty["id"])
		self.assertIsNone(empty["punched_date"])
		self.assertIsNone(empty["punched_time"])

		created = self._punch(lat=9.032, lng=38.7469)
		self.assertEqual(created.action, "Punch In")
		self.assertEqual(created.da_id, DA_ID)
		self.assertEqual(created.region, self.geo["region"])
		self.assertEqual(float(created.gps_lat), 9.032)
		self.assertEqual(float(created.gps_lng), 38.7469)

		latest = svc.get_today_latest()
		self.assertEqual(latest["id"], created.name)
		self.assertEqual(latest["next_action"], "Punch Out")
		self.assertEqual(latest["punched_date"], str(created.punched_date))
		self.assertTrue(latest["punched_time"])

	def test_supervisor_without_kebele_and_optional_geo(self):
		frappe.set_user(SUP_USER)
		created = self._punch(action="Punch In", kebele=False)
		self.assertEqual(created.da_id, SUP_DA_ID)
		self.assertEqual(created.woreda, self.geo["woreda"])
		self.assertFalse(created.kebele)

		# region/zone optional: punch with none
		svc.create_punch(
			action="Punch Out",
			punched_date=today(),
			punched_time="18:00:00",
		)
		row = frappe.get_all(
			"DA Attendance Punch",
			filters={"user": SUP_USER, "action": "Punch Out"},
			fields=["region", "zone", "name"],
			limit=1,
		)[0]
		self.assertFalse(row.region)
		self.assertFalse(row.zone)

	def test_multiple_punches_next_action_flips(self):
		frappe.set_user(DA_USER)
		first = self._punch(action="Punch In")
		latest = svc.get_today_latest()
		self.assertEqual(latest["id"], first.name)
		self.assertEqual(latest["next_action"], "Punch Out")

		second = self._punch(action="Punch Out")
		latest = svc.get_today_latest()
		self.assertEqual(latest["id"], second.name)
		self.assertEqual(latest["next_action"], "Punch In")

		third = self._punch(action="Punch In")
		latest = svc.get_today_latest()
		self.assertEqual(latest["id"], third.name)
		self.assertEqual(latest["next_action"], "Punch Out")

	def test_yesterday_punch_not_in_today_latest(self):
		frappe.set_user(DA_USER)
		yesterday = f"{add_days(today(), -1)} 18:00:00"
		self._punch(action="Punch In", when=yesterday)
		empty = svc.get_today_latest()
		self.assertEqual(empty["next_action"], "Punch In")
		self.assertIsNone(empty["id"])

		today_row = self._punch(action="Punch In")
		latest = svc.get_today_latest()
		self.assertEqual(latest["id"], today_row.name)

	def test_missing_da_registry_id_rejected(self):
		frappe.set_user(DA_USER)
		frappe.db.set_value("User", DA_USER, "da_registry_id", None, update_modified=False)
		with self.assertRaises(frappe.MandatoryError):
			self._punch()

	def test_missing_punched_date_rejected(self):
		frappe.set_user(DA_USER)
		with self.assertRaises(frappe.MandatoryError):
			svc.create_punch(
				action="Punch In",
				punched_date=None,
				punched_time="09:00:00",
			)
