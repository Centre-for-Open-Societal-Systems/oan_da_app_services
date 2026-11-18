"""DA Visit doctype, lifecycle, follow-ups, scope and service layer (DA-195)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, now_datetime, today

from da_services.services import constants as C
from da_services.services import visits
from da_services.tests.fixtures import (
	cleanup_test_rows,
	ensure_da_user,
	ensure_kebele_assignment,
	live_geo,
	set_da_registry_id,
	stamp_visit_geo,
)

DA1 = "visit-da1@da.local"  # DA-000001, ET04 / ET04-W01
DA3 = "visit-da3@da.local"  # DA-000003, ET03
SUP = "visit-sup@da.local"  # Supervisor ET04 / ET04-W01
EXEC = "visit-exec@da.local"  # Executive ET04
COMMS = "visit-comms@da.local"
ADMIN = "Administrator"


def _user(email, roles):
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = email.split("@")[0]
		u.send_welcome_email = 0
		u.insert(ignore_permissions=True, ignore_links=True)
	u = frappe.get_doc("User", email)
	for r in roles:
		if r not in frappe.get_roles(email):
			u.add_roles(r)
	return email


def _grant(user, role, **kw):
	doc = frappe.new_doc("DA RBAC Assignment")
	doc.user = user
	doc.role = role
	doc.effective_from = today()
	for k, v in kw.items():
		setattr(doc, k, v)
	doc.insert(ignore_permissions=True, ignore_links=True)
	return doc


class TestDAVisit(IntegrationTestCase):
	USERS = (DA1, DA3, SUP, EXEC, COMMS)
	DA_IDS = ("DA-000001", "DA-000003")

	def setUp(self):
		frappe.set_user(ADMIN)
		cleanup_test_rows(users=self.USERS, da_ids=self.DA_IDS)
		self.geo = live_geo()
		_user(DA1, [C.ROLE_DA])
		_user(DA3, [C.ROLE_DA])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_user(EXEC, [C.ROLE_EXECUTIVE])
		_user(COMMS, [C.ROLE_COMMS])
		set_da_registry_id(DA1, "DA-000001")
		set_da_registry_id(DA3, "DA-000003")
		_grant(
			DA1,
			C.ROLE_DA,
			da_id="DA-000001",
			region_scope=self.geo["region"],
			woreda_scope=self.geo["woreda"],
		)
		_grant(
			DA3,
			C.ROLE_DA,
			da_id="DA-000003",
			region_scope=self.geo["foreign_region"],
			woreda_scope=self.geo["foreign_woreda"],
		)
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope=self.geo["region"], woreda_scope=self.geo["woreda"])
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope=self.geo["region"])
		_grant(COMMS, C.ROLE_COMMS)
		ensure_kebele_assignment("DA-000001", self.geo["kebele"])
		self.tomorrow = f"{add_days(today(), 1)} 09:00:00"
		frappe.set_user(DA1)
		self.v1 = visits.plan_visit(
			farmer_id="FR-88466",
			scheduled_at=f"{today()} 08:30:00",
			purpose="Crop inspection",
			user=DA1,
		)
		frappe.set_user(ADMIN)
		self.v3 = visits.plan_visit(
			farmer_id="FR-77001",
			scheduled_at=self.tomorrow,
			user=DA3,
			da_id="DA-000003",
		)
		stamp_visit_geo(self.v1["name"], self.geo)
		stamp_visit_geo(
			self.v3["name"],
			{
				"region": self.geo["foreign_region"],
				"zone": frappe.db.get_value("DA Woreda", self.geo["foreign_woreda"], "zone"),
				"woreda": self.geo["foreign_woreda"],
				"kebele": self.geo["foreign_kebele"],
			},
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def tearDown(self):
		frappe.set_user(ADMIN)
		cleanup_test_rows(users=self.USERS, da_ids=self.DA_IDS)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	# -- plan ---------------------------------------------------------------------

	def test_plan_fills_posting_and_defaults(self):
		doc = frappe.get_doc("DA Visit", self.v1["name"])
		self.assertTrue(doc.name.startswith("DA-VIS-"))
		self.assertEqual(
			(doc.region, doc.woreda, doc.kebele), (self.geo["region"], self.geo["woreda"], self.geo["kebele"])
		)
		self.assertEqual(doc.physical_status, "Planned")
		self.assertEqual(doc.source, "Planned")
		self.assertIsNone(doc.assigned_by)  # self-planned
		self.assertEqual(self.v1["ui_status"], "Planned")

	def test_da_plans_only_for_own_da_id(self):
		out = visits.plan_visit(farmer_id="FR-1", scheduled_at=self.tomorrow, user=DA1, da_id="DA-000003")
		self.assertEqual(out["da_id"], "DA-000001")

	def test_supervisor_plans_for_own_woreda_and_is_recorded(self):
		da2_user = ensure_da_user("DA-000002")
		frappe.set_user(SUP)
		out = visits.plan_visit(
			farmer_id="FR-2", scheduled_at=self.tomorrow, user=da2_user, da_id="DA-000002"
		)
		self.assertEqual(out["assigned_by"], SUP)
		# Row da_id comes from User.da_registry_id (fetch_from); visibility from DocType hooks.
		out = visits.plan_visit(farmer_id="FR-3", scheduled_at=self.tomorrow, user=DA3, da_id="DA-000003")
		self.assertEqual(out["da_id"], "DA-000003")
		self.assertEqual(out["assigned_by"], SUP)

	def test_plan_requires_schedule_and_is_idempotent(self):
		with self.assertRaises(frappe.ValidationError):
			visits.plan_visit(farmer_id="FR-1", scheduled_at=None, user=DA1)
		a = visits.plan_visit(farmer_id="FR-1", scheduled_at=self.tomorrow, user=DA1, client_op_id="op-v1")
		b = visits.plan_visit(farmer_id="FR-1", scheduled_at=self.tomorrow, user=DA1, client_op_id="op-v1")
		self.assertEqual(a["name"], b["name"])

	def test_advisory_qa_only_on_advisory_visits(self):
		out = visits.plan_visit(
			farmer_id="FR-1",
			scheduled_at=self.tomorrow,
			visit_type="Advisory",
			advisory_qa=[{"question": "Rust on teff?", "answer": "Spray within 3 days"}],
			user=DA1,
		)
		self.assertEqual(len(out["advisory_qa"]), 1)
		doc = frappe.get_doc("DA Visit", self.v1["name"])
		doc.append("advisory_qa", {"question": "x"})
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	# -- lifecycle ----------------------------------------------------------------

	def test_start_then_submit_creates_follow_up_task_and_visit(self):
		out = visits.start_visit(self.v1["name"], gps={"lat": 9.1236, "lng": 37.0521, "accuracy_m": 12})
		self.assertEqual(out["physical_status"], "In progress")
		doc = frappe.get_doc("DA Visit", self.v1["name"])
		self.assertEqual(doc.gps_lat, 9.1236)
		self.assertIsNotNone(doc.arrived_at)

		out = visits.submit_outcome(
			self.v1["name"],
			observed="Rust pustules on ~15% of the plot",
			advice_given="Propiconazole within 3 days",
			farmer_response="Will apply",
			next_action={
				"type": "Follow-up visit",
				"note": "Check spray result",
				"date": add_days(today(), 14),
			},
		)
		self.assertEqual(out["physical_status"], "Physical Visit Complete")
		self.assertEqual(out["ui_status"], "Completed")
		self.assertIsNotNone(out["completed_at"])
		self.assertTrue(out["follow_up_task"])
		self.assertTrue(out["follow_up_visit"])

		task = frappe.get_doc("DA Task", out["follow_up_task"])
		self.assertEqual(
			(task.task_type, task.status, task.visit, task.da_id),
			("Follow-up", "Open", self.v1["name"], "DA-000001"),
		)
		self.assertEqual(task.generated_by_rule, "visit.follow_up")
		fu = frappe.get_doc("DA Visit", out["follow_up_visit"])
		self.assertEqual(
			(fu.visit_type, fu.physical_status, fu.farmer_id), ("Follow-up", "Planned", "FR-88466")
		)

	def test_submit_with_task_only_creates_no_visit(self):
		out = visits.submit_outcome(
			self.v1["name"],
			observed="ok",
			next_action={"type": "Task", "note": "Bring seed sample", "date": add_days(today(), 3)},
		)
		self.assertTrue(out["follow_up_task"])
		self.assertFalse(out["follow_up_visit"])
		self.assertEqual(frappe.db.count("DA Visit", {"da_id": "DA-000001"}), 1)

	def test_closed_visit_cannot_change(self):
		visits.submit_outcome(self.v1["name"], observed="done")
		with self.assertRaises(frappe.ValidationError):
			visits.submit_outcome(self.v1["name"], observed="again")
		doc = frappe.get_doc("DA Visit", self.v1["name"])
		doc.physical_status = "Planned"
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_reschedule_supersedes_and_links(self):
		new = visits.reschedule_visit(
			self.v1["name"], scheduled_at=self.tomorrow, reason="Farmer unavailable"
		)
		old = frappe.get_doc("DA Visit", self.v1["name"])
		self.assertEqual((old.physical_status, old.reschedule_reason), ("Rescheduled", "Farmer unavailable"))
		self.assertEqual((new["physical_status"], new["rescheduled_from"]), ("Planned", self.v1["name"]))
		with self.assertRaises(frappe.ValidationError):
			visits.start_visit(self.v1["name"])

	def test_derived_statuses(self):
		past = visits.plan_visit(
			farmer_id="FR-9", scheduled_at=add_to_date(now_datetime(), hours=-3), user=DA1
		)
		stamp_visit_geo(past["name"], self.geo)
		self.assertEqual(past["planner_status"], "overdue")
		doc = frappe.get_doc("DA Visit", past["name"])
		doc.farmer_confirmation = "Confirmed"
		doc.save()
		self.assertEqual(visits.get_visit(past["name"])["ui_status"], "Confirmed")

	# -- scope --------------------------------------------------------------------

	def _visible(self, user) -> set[str]:
		frappe.set_user(user)
		return {r.da_id for r in frappe.get_list("DA Visit", fields=["da_id"])}

	def test_list_scope_per_role(self):
		self.assertEqual(self._visible(DA1), {"DA-000001"})
		self.assertEqual(self._visible(SUP), {"DA-000001"})
		self.assertEqual(self._visible(EXEC), {"DA-000001"})
		self.assertEqual(self._visible(ADMIN), {"DA-000001", "DA-000003"})
		frappe.set_user(COMMS)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list("DA Visit", fields=["da_id"])

	def test_executive_read_only_on_write(self):
		# Detail is plain get_doc (no service gate). Write still uses Role DocPerm.
		frappe.set_user(EXEC)
		visits.get_visit(self.v1["name"])
		with self.assertRaises(frappe.PermissionError):
			visits.start_visit(self.v1["name"])

	# -- dashboard ------------------------------------------------------------------

	def test_dashboard_counts_and_today_items(self):
		frappe.set_user(ADMIN)
		for i in range(3):
			visits.plan_visit(
				farmer_id=f"FR-{i}", scheduled_at=f"{add_days(today(), -i - 1)} 10:00:00", user=DA1
			)
		frappe.set_user(DA1)
		visits.submit_outcome(self.v1["name"], observed="ok")
		counts = visits.dashboard_counts("DA-000001", add_days(today(), -30), today())
		self.assertEqual(
			counts, {"planned": 4, "completed": 1, "missed": 0, "rescheduled": 0, "in_progress": 0}
		)

		items = visits.today_items("DA-000001")
		self.assertEqual(len(items), 1)
		self.assertEqual(items[0]["kind"], "visit")
		self.assertEqual(items[0]["time"], "08:30")
		self.assertEqual(items[0]["status"], "Done")
		self.assertIn("FR-88466", items[0]["title"])

	def test_list_filters(self):
		frappe.set_user(DA1)
		rows, total = visits.list_visits(date=today())
		self.assertEqual(total, 1)
		self.assertEqual(rows[0]["name"], self.v1["name"])
		rows, total = visits.list_visits(status="Completed")
		self.assertEqual(total, 0)

	def test_farmer_advisory_lists_questions_newest_visit_first(self):
		frappe.set_user(DA1)
		older = visits.plan_visit(
			farmer_id="FR-ADV-1",
			scheduled_at=self.tomorrow,
			visit_type="Advisory",
			advisory_qa=[
				{"question": "Rust on teff?", "answer": "Spray within 3 days"},
				{"question": "When to top-dress?", "answer": ""},
			],
			user=DA1,
		)
		# A farmer has one open Advisory visit at a time, so each is closed before the next is planned.
		frappe.db.set_value("DA Visit", older["name"], "physical_status", visits.COMPLETE)
		newer = visits.plan_visit(
			farmer_id="FR-ADV-1",
			scheduled_at=add_days(self.tomorrow, 2),
			visit_type="Advisory",
			advisory_qa=[{"question": "Which seed rate?", "answer": "25 kg/ha"}],
			user=DA1,
		)
		frappe.db.set_value("DA Visit", newer["name"], "physical_status", visits.COMPLETE)
		# This farmer's Advisory visit with no questions, and another farmer's Advisory visit.
		empty = visits.plan_visit(
			farmer_id="FR-ADV-1", scheduled_at=add_days(self.tomorrow, 4), visit_type="Advisory", user=DA1
		)
		other = visits.plan_visit(
			farmer_id="FR-ADV-2",
			scheduled_at=self.tomorrow,
			visit_type="Advisory",
			advisory_qa=[{"question": "Not this farmer", "answer": ""}],
			user=DA1,
		)
		for visit in (older, newer, empty, other):
			stamp_visit_geo(visit["name"], self.geo)

		rows = visits.list_farmer_advisory("FR-ADV-1")
		self.assertEqual(
			[(row.visit, row.question, row.answer) for row in rows],
			[
				(newer["name"], "Which seed rate?", "25 kg/ha"),
				(older["name"], "Rust on teff?", "Spray within 3 days"),
				(older["name"], "When to top-dress?", ""),
			],
		)
		self.assertEqual(rows[0].physical_status, visits.COMPLETE)
		self.assertEqual(len(visits.list_farmer_advisory("FR-ADV-1", limit=1, start=1)), 1)

		# The questions follow the visit's own visibility: a DA elsewhere gets none.
		frappe.set_user(DA3)
		self.assertEqual(visits.list_farmer_advisory("FR-ADV-1"), [])

	def test_list_area_filters_use_user_permissions(self):
		# Out-of-scope geography filters no longer throw; get_list + User Permissions return empty.
		frappe.set_user(SUP)
		try:
			rows, total = visits.list_visits(woreda=self.geo["woreda"])
			self.assertEqual(total, 1)
			self.assertEqual(rows[0]["name"], self.v1["name"])
			rows, total = visits.list_visits(kebele=self.geo["kebele"])
			self.assertEqual(total, 1)
			_rows, total = visits.list_visits(woreda=self.geo["foreign_woreda"])
			self.assertEqual(total, 0)
		finally:
			frappe.set_user(ADMIN)

		frappe.set_user(DA1)
		try:
			rows, total = visits.list_visits(kebele=self.geo["kebele"])
			self.assertEqual(total, 1)
			# Equality filter on a kebele this visit does not carry (sibling may equal primary).
			_rows, total = visits.list_visits(kebele=self.geo["foreign_kebele"])
			self.assertEqual(total, 0)
		finally:
			frappe.set_user(ADMIN)
