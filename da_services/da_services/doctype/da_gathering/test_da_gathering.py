# Copyright (c) 2026, COSS - Centre for Open Societal Systems and Contributors
# See license.txt

"""DA Gathering DocType + service: plan/list/get/cancel and product rules."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from da_services.services import constants as C
from da_services.services import gatherings
from da_services.tests.fixtures import (
	cleanup_test_rows,
	ensure_kebele_assignment,
	live_geo,
	set_da_registry_id,
)

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []

DA1 = "gath-da1@da.local"
DA3 = "gath-da3@da.local"
SUP = "gath-sup@da.local"
ADMIN = "Administrator"

F_IN = "FR-GATH-IN-001"
F_IN2 = "FR-GATH-IN-002"
F_NOPHONE = "FR-GATH-NOPHONE-001"


def _ensure_user(email, roles):
	if not frappe.db.exists("User", email):
		user_doc = frappe.new_doc("User")
		user_doc.email = email
		user_doc.first_name = email.split("@")[0]
		user_doc.send_welcome_email = 0
		user_doc.insert(ignore_permissions=True, ignore_links=True)
	user_doc = frappe.get_doc("User", email)
	for role in roles:
		if role not in frappe.get_roles(email):
			user_doc.add_roles(role)
	return email


def _grant_rbac(user, role, **scope_fields):
	assignment = frappe.new_doc("DA RBAC Assignment")
	assignment.user = user
	assignment.role = role
	assignment.effective_from = today()
	for field_name, value in scope_fields.items():
		setattr(assignment, field_name, value)
	assignment.insert(ignore_permissions=True, ignore_links=True)
	return assignment


def _location():
	"""Client may only send venue + landmark + lat/lng."""
	return {
		"name": "Kebele meeting hall",
		"landmark": "Beside the church",
		"lat": 9.032,
		"lng": 38.7469,
	}


def _farmers(*farmer_ids: str) -> list[dict]:
	"""FE-required invitee snapshots (no DA Farmer Reference)."""
	names = {
		F_IN: ("In Kebele One", "911234567"),
		F_IN2: ("In Kebele Two", "911234568"),
		F_NOPHONE: ("No Phone", ""),
	}
	rows = []
	for farmer_id in farmer_ids:
		name, phone = names.get(farmer_id, (f"Farmer {farmer_id}", "911000000"))
		rows.append({"farmer_id": farmer_id, "farmer_name": name, "phone": phone})
	return rows


class IntegrationTestDAGathering(IntegrationTestCase):
	"""DocType + service behaviour for DA Gathering."""

	USERS = (DA1, DA3, SUP)
	DA_IDS = ("DA-000001", "DA-000003")

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.reload_doc("DA Services", "doctype", "da_gathering_attendee", force=True)
		frappe.reload_doc("DA Services", "doctype", "da_gathering_sms_log", force=True)
		frappe.reload_doc("DA Services", "doctype", "da_gathering", force=True)

	def setUp(self):
		frappe.set_user(ADMIN)
		cleanup_test_rows(users=self.USERS, da_ids=self.DA_IDS)
		self.geo = live_geo()
		_ensure_user(DA1, [C.ROLE_DA])
		_ensure_user(DA3, [C.ROLE_DA])
		_ensure_user(SUP, [C.ROLE_SUPERVISOR])
		set_da_registry_id(DA1, "DA-000001")
		set_da_registry_id(DA3, "DA-000003")
		_grant_rbac(
			DA1,
			C.ROLE_DA,
			da_id="DA-000001",
			region_scope=self.geo["region"],
			woreda_scope=self.geo["woreda"],
		)
		_grant_rbac(
			DA3,
			C.ROLE_DA,
			da_id="DA-000003",
			region_scope=self.geo["foreign_region"],
			woreda_scope=self.geo["foreign_woreda"],
		)
		_grant_rbac(SUP, C.ROLE_SUPERVISOR, region_scope=self.geo["region"], woreda_scope=self.geo["woreda"])
		ensure_kebele_assignment("DA-000001", self.geo["kebele"])
		ensure_kebele_assignment("DA-000003", self.geo["foreign_kebele"])
		self.tomorrow = f"{add_days(today(), 1)} 09:00:00"
		frappe.set_user(DA1)
		self.gathering = gatherings.plan_gathering(
			title="Fertilizer timing",
			scheduled_at=self.tomorrow,
			farmers=_farmers(F_IN, F_IN2),
			location=_location(),
			user=DA1,
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def tearDown(self):
		frappe.set_user(ADMIN)
		cleanup_test_rows(users=self.USERS, da_ids=self.DA_IDS)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def test_plan_missing_user_fails_closed(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			gatherings.plan_gathering(
				title="X",
				scheduled_at=self.tomorrow,
				farmers=_farmers(F_IN),
				location=_location(),
				user="",
			)
		self.assertIn("user is required", str(ctx.exception))

	def test_plan_requires_farmer_name_and_phone(self):
		with self.assertRaises(frappe.ValidationError) as missing_name:
			gatherings.plan_gathering(
				title="X",
				scheduled_at=self.tomorrow,
				farmers=[{"farmer_id": F_IN, "farmer_name": "", "phone": "911234567"}],
				location=_location(),
				user=DA1,
			)
		self.assertIn("farmer_name is required", str(missing_name.exception))
		with self.assertRaises(frappe.ValidationError) as missing_phone:
			gatherings.plan_gathering(
				title="X",
				scheduled_at=self.tomorrow,
				farmers=[{"farmer_id": F_IN, "farmer_name": "Name", "phone": ""}],
				location=_location(),
				user=DA1,
			)
		self.assertIn("phone is required", str(missing_phone.exception))

	def test_plan_missing_reqd_fields_fail_via_frappe(self):
		with self.assertRaises((frappe.MandatoryError, frappe.ValidationError)):
			gatherings.plan_gathering(
				title="X",
				scheduled_at=self.tomorrow,
				farmers=_farmers(F_IN),
				location={"name": "", "landmark": "x"},
				user=DA1,
			)

	def test_plan_happy_path_and_sms_rows(self):
		doc = frappe.get_doc("DA Gathering", self.gathering["name"])
		self.assertTrue(doc.name.startswith("DA-GATH-"))
		self.assertEqual(doc.status, "Planned")
		self.assertEqual(doc.da_id, "DA-000001")
		self.assertEqual(doc.kebele, self.geo["kebele"])
		self.assertEqual(doc.woreda, self.geo["woreda"])
		self.assertEqual(doc.zone, self.geo["zone"])
		self.assertEqual(doc.region, self.geo["region"])
		self.assertIn("Kebele meeting hall", doc.address)
		self.assertEqual(len(doc.attendees), 2)
		self.assertEqual(doc.attendees[0].farmer_name, "In Kebele One")
		self.assertEqual(doc.attendees[0].phone, "911234567")
		self.assertEqual(len(doc.sms_logs), 2)
		self.assertTrue(all(sms_log.status == "Queued" for sms_log in doc.sms_logs))
		self.assertIn(doc.address, doc.sms_logs[0].message_body)
		self.assertNotIn("9.032", doc.sms_logs[0].message_body)
		self.assertEqual(self.gathering["farmer_ids"], [F_IN, F_IN2])

	def test_plan_rejects_more_than_20(self):
		farmers = [
			{"farmer_id": f"FR-GATH-MANY-{index:03d}", "farmer_name": f"Farmer {index}", "phone": "911000000"}
			for index in range(21)
		]
		with self.assertRaises(frappe.ValidationError):
			gatherings.plan_gathering(
				title="Too many",
				scheduled_at=self.tomorrow,
				farmers=farmers,
				location=_location(),
				user=DA1,
			)

	def test_plan_idempotent_on_client_op_id(self):
		first = gatherings.plan_gathering(
			title="Idem",
			scheduled_at=self.tomorrow,
			farmers=_farmers(F_IN),
			location=_location(),
			user=DA1,
			client_op_id="gath-op-1",
		)
		second = gatherings.plan_gathering(
			title="Idem again",
			scheduled_at=self.tomorrow,
			farmers=_farmers(F_IN2),
			location=_location(),
			user=DA1,
			client_op_id="gath-op-1",
		)
		self.assertEqual(first["name"], second["name"])

	def test_supervisor_assigned_by_recorded(self):
		frappe.set_user(SUP)
		result = gatherings.plan_gathering(
			title="Supervisor plan",
			scheduled_at=self.tomorrow,
			farmers=_farmers(F_IN),
			location=_location(),
			user=DA1,
		)
		self.assertEqual(result["assigned_by"], SUP)
		self.assertEqual(result["da_id"], "DA-000001")

	def test_cancel_transition(self):
		result = gatherings.cancel_gathering(self.gathering["name"], reason="Rain")
		self.assertEqual(result["status"], "Cancelled")
		self.assertEqual(result["cancel_reason"], "Rain")
		with self.assertRaises(frappe.ValidationError):
			gatherings.cancel_gathering(self.gathering["name"], reason="again")

	def test_list_scoped_by_frappe_permissions(self):
		frappe.set_user(DA1)
		rows, total = gatherings.list_gatherings()
		names = {row["name"] for row in rows}
		self.assertIn(self.gathering["name"], names)
		self.assertGreaterEqual(total, 1)

		frappe.set_user(DA3)
		rows_out_of_scope, _ = gatherings.list_gatherings(kebele=self.geo["kebele"])
		self.assertNotIn(self.gathering["name"], {row["name"] for row in rows_out_of_scope})

	def test_get_gathering(self):
		result = gatherings.get_gathering(self.gathering["name"])
		self.assertEqual(result["name"], self.gathering["name"])
		self.assertEqual(len(result["attendees"]), 2)
		self.assertEqual(result["location"]["name"], "Kebele meeting hall")
		self.assertEqual(result["location"]["kebele"], self.geo["kebele"])
		self.assertIn("Kebele meeting hall", result["location"]["address"])

	def test_doctype_rejects_duplicate_attendees_on_save(self):
		doc = frappe.get_doc("DA Gathering", self.gathering["name"])
		doc.append(
			"attendees",
			{"farmer_id": F_IN, "farmer_name": "Dup", "phone": "911111111", "sms_status": "Pending"},
		)
		with self.assertRaises(frappe.ValidationError):
			doc.save()
