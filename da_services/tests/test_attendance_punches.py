"""DA Attendance Punch: create + today-latest with next_action + daily list."""

from __future__ import annotations

import frappe
from frappe.permissions import add_user_permission
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime, today

from da_services.services import attendance_punches as svc
from da_services.services import constants as C
from da_services.tests.fixtures import live_geo, set_da_registry_id

DA_USER = "atp-da@da.local"
DA2_USER = "atp-da2@da.local"
SUP_USER = "atp-sup@da.local"
DA_ID = "DA-ATP-0001"
DA2_ID = "DA-ATP-0002"
SUP_DA_ID = "DA-ATP-SUP1"
ALL_USERS = (DA_USER, DA2_USER, SUP_USER)


def _user(
	email: str, roles: list[str], *, first_name: str | None = None, last_name: str | None = None
) -> str:
	first = first_name or email.split("@")[0]
	last = last_name or ""
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = first
		u.last_name = last
		u.send_welcome_email = 0
		u.insert(ignore_permissions=True, ignore_links=True)
	else:
		frappe.db.set_value(
			"User",
			email,
			{
				"first_name": first,
				"last_name": last,
				"full_name": f"{first} {last}".strip(),
			},
			update_modified=False,
		)
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
		_user(DA_USER, [C.ROLE_DA], first_name="Tadesse", last_name="Alemu")
		_user(DA2_USER, [C.ROLE_DA], first_name="Oluma", last_name="Fikadu")
		_user(SUP_USER, [C.ROLE_SUPERVISOR])
		set_da_registry_id(DA_USER, DA_ID)
		set_da_registry_id(DA2_USER, DA2_ID)
		set_da_registry_id(SUP_USER, SUP_DA_ID)

	def tearDown(self):
		frappe.set_user("Administrator")
		self._clean()

	def _clean(self):
		frappe.db.delete("DA Attendance Punch", {"user": ("in", ALL_USERS)})
		frappe.db.delete("User Permission", {"user": ("in", ALL_USERS)})
		for email in ALL_USERS:
			if frappe.db.exists("User", email):
				frappe.db.set_value("User", email, "da_registry_id", None, update_modified=False)
				frappe.cache.hdel("user_permissions", email)

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

	def test_list_in_and_out_hours_and_names(self):
		frappe.set_user(DA_USER)
		day = today()
		svc.create_punch(
			action="Punch In",
			punched_date=day,
			punched_time="09:00:00",
			region=self.geo["region"],
			zone=self.geo["zone"],
			woreda=self.geo["woreda"],
			kebele=self.geo["kebele"],
		)
		svc.create_punch(
			action="Punch Out",
			punched_date=day,
			punched_time="13:27:00",
			region=self.geo["region"],
			zone=self.geo["zone"],
			woreda=self.geo["woreda"],
			kebele=self.geo["kebele"],
		)
		rows, total = svc.list_attendance(date=day, user=DA_USER)
		self.assertEqual(total, 1)
		self.assertEqual(len(rows), 1)
		row = rows[0]
		self.assertEqual(row["user"], DA_USER)
		self.assertEqual(row["user_name"], "Tadesse Alemu")
		self.assertEqual(row["da_id"], DA_ID)
		self.assertEqual(row["kebele"], self.geo["kebele"])
		kebele_name = frappe.db.get_value("DA Kebele", self.geo["kebele"], "kebele_name")
		self.assertEqual(row["kebele_name"], kebele_name)
		self.assertEqual(row["punched_date"], str(day))
		self.assertEqual(row["status"], "Checked In")
		self.assertEqual(row["check_in"], "09:00:00")
		self.assertEqual(row["check_out"], "13:27:00")
		self.assertEqual(row["hours_logged"], "4h 27m")

	def test_list_in_only_null_hours(self):
		frappe.set_user(DA_USER)
		day = today()
		svc.create_punch(
			action="Punch In",
			punched_date=day,
			punched_time="08:51:00",
			kebele=self.geo["kebele"],
		)
		rows, total = svc.list_attendance(date=day, user=DA_USER)
		self.assertEqual(total, 1)
		row = rows[0]
		self.assertEqual(row["status"], "Checked In")
		self.assertEqual(row["check_in"], "08:51:00")
		self.assertIsNone(row["check_out"])
		self.assertIsNone(row["hours_logged"])

	def test_list_date_filter_excludes_other_days(self):
		frappe.set_user(DA_USER)
		yesterday = add_days(today(), -1)
		svc.create_punch(
			action="Punch In",
			punched_date=yesterday,
			punched_time="10:00:00",
			kebele=self.geo["kebele"],
		)
		svc.create_punch(
			action="Punch In",
			punched_date=today(),
			punched_time="11:00:00",
			kebele=self.geo["kebele"],
		)
		rows, total = svc.list_attendance(date=today(), user=DA_USER)
		self.assertEqual(total, 1)
		self.assertEqual(rows[0]["punched_date"], str(today()))
		self.assertEqual(rows[0]["check_in"], "11:00:00")

	def test_list_out_of_grant_kebele_filter_empty(self):
		# User Permission on primary kebele; filter to foreign kebele → empty page (not 403).
		if not self.geo.get("foreign_kebele"):
			self.skipTest("no foreign_kebele on this site")
		frappe.set_user("Administrator")
		add_user_permission("DA Kebele", self.geo["kebele"], DA_USER, ignore_permissions=True)
		frappe.cache.hdel("user_permissions", DA_USER)

		frappe.set_user(DA_USER)
		svc.create_punch(
			action="Punch In",
			punched_date=today(),
			punched_time="09:00:00",
			region=self.geo["region"],
			zone=self.geo["zone"],
			woreda=self.geo["woreda"],
			kebele=self.geo["kebele"],
		)
		rows, total = svc.list_attendance(user=DA_USER, date=today(), kebele=self.geo["foreign_kebele"])
		self.assertEqual(total, 0)
		self.assertEqual(rows, [])

	def test_list_user_required(self):
		frappe.set_user(DA_USER)
		with self.assertRaises(frappe.ValidationError):
			svc.list_attendance(user="")
		with self.assertRaises(frappe.ValidationError):
			svc.list_attendance(user="   ")

	def test_list_filters_kebele_woreda_and_date_range(self):
		frappe.set_user(DA_USER)
		day = today()
		yesterday = add_days(day, -1)
		geo = dict(
			region=self.geo["region"],
			zone=self.geo["zone"],
			woreda=self.geo["woreda"],
			kebele=self.geo["kebele"],
		)
		svc.create_punch(action="Punch In", punched_date=yesterday, punched_time="09:00:00", **geo)
		svc.create_punch(action="Punch In", punched_date=day, punched_time="10:00:00", **geo)

		by_kebele, n = svc.list_attendance(user=DA_USER, date=day, kebele=self.geo["kebele"])
		self.assertEqual(n, 1)
		self.assertEqual(by_kebele[0]["kebele"], self.geo["kebele"])
		self.assertEqual(by_kebele[0]["check_in"], "10:00:00")

		by_range, n = svc.list_attendance(user=DA_USER, from_date=yesterday, to_date=day)
		self.assertEqual(n, 2)
		self.assertEqual({r["punched_date"] for r in by_range}, {str(yesterday), str(day)})

		by_woreda, n = svc.list_attendance(user=DA_USER, date=day, woreda=self.geo["woreda"])
		self.assertEqual(n, 1)
		self.assertEqual(by_woreda[0]["da_id"], DA_ID)

		# Other user's punches are not mixed in when user is required.
		frappe.set_user(DA2_USER)
		svc.create_punch(action="Punch In", punched_date=day, punched_time="11:00:00", **geo)
		frappe.set_user("Administrator")
		only_da, n = svc.list_attendance(user=DA_USER, date=day)
		self.assertEqual(n, 1)
		self.assertEqual(only_da[0]["user"], DA_USER)

	def test_list_incomplete_date_range_rejected(self):
		frappe.set_user(DA_USER)
		with self.assertRaises(frappe.ValidationError):
			svc.list_attendance(user=DA_USER, from_date=today())
		with self.assertRaises(frappe.ValidationError):
			svc.list_attendance(user=DA_USER, to_date=today())
