"""DA Visit doctype, lifecycle, follow-ups, scope and service layer (DA-195)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, now_datetime, today

from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import constants as C
from da_services.services import visits
from da_services.tests.fixtures import (
	aligned_mock_geo,
	bind_da_user,
	cleanup_test_rows,
	ensure_kebele_assignment,
	require_geo_masters,
)
from da_services.utils.permissions import get_scope_context

# Geography is the mock DA Registry's, rewritten from the synced masters (aligned_mock_geo).
DA1 = "visit-da1@da.local"  # DA-000001, primary Woreda
DA3 = "visit-da3@da.local"  # DA-000003, other Region
SUP = "visit-sup@da.local"  # Supervisor, primary Woreda
EXEC = "visit-exec@da.local"  # Executive, primary Region
COMMS = "visit-comms@da.local"
ADMIN = "Administrator"

# Rows this module creates; setUp / tearDown delete only these.
USERS = (DA1, DA3, SUP, EXEC, COMMS)
DA_IDS = ("DA-000001", "DA-000002", "DA-000003")


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


def _ctx(user):
	return get_scope_context(user)


class TestDAVisit(IntegrationTestCase):
	def setUp(self):
		frappe.set_user(ADMIN)
		cleanup_test_rows(users=USERS, da_ids=DA_IDS)
		require_geo_masters()
		MockDARegistryClient.reset()
		g = aligned_mock_geo()
		self.geo = g
		self.region, self.woreda, self.kebele = g["region"], g["woreda"], g["kebele"]
		self.kebele_alt, self.other_woreda = g["kebele_alt"], g["other_woreda"]
		_user(DA1, [C.ROLE_DA])
		_user(DA3, [C.ROLE_DA])
		bind_da_user(DA1, "DA-000001")  # a DA's own DA-ID is read from User.da_registry_id
		bind_da_user(DA3, "DA-000003")
		_user(SUP, [C.ROLE_SUPERVISOR])
		_user(EXEC, [C.ROLE_EXECUTIVE])
		_user(COMMS, [C.ROLE_COMMS])
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope=self.region, woreda_scope=self.woreda)
		_grant(
			DA3, C.ROLE_DA, da_id="DA-000003", region_scope=g["other_region"], woreda_scope=self.other_woreda
		)
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope=self.region, woreda_scope=self.woreda)
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope=self.region)
		_grant(COMMS, C.ROLE_COMMS)
		# DA lists are scoped to the Effective Kebele posting, which matches the registry kebele.
		ensure_kebele_assignment("DA-000001", self.kebele)
		self.tomorrow = f"{add_days(today(), 1)} 09:00:00"
		self.v1 = visits.plan_visit(
			farmer_id="FR-88466", scheduled_at=f"{today()} 08:30:00", purpose="Crop inspection", ctx=_ctx(DA1)
		)
		self.v3 = visits.plan_visit(
			farmer_id="FR-77001", scheduled_at=self.tomorrow, da_id="DA-000003", ctx=_ctx(ADMIN)
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def tearDown(self):
		frappe.set_user(ADMIN)
		cleanup_test_rows(users=USERS, da_ids=DA_IDS)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	# -- plan ---------------------------------------------------------------------

	def test_plan_fills_posting_and_defaults(self):
		doc = frappe.get_doc("DA Visit", self.v1["name"])
		self.assertTrue(doc.name.startswith("DA-VIS-"))
		self.assertEqual((doc.region, doc.woreda, doc.kebele), (self.region, self.woreda, self.kebele))
		self.assertEqual(doc.physical_status, "Planned")
		self.assertEqual(doc.source, "Planned")
		self.assertIsNone(doc.assigned_by)  # self-planned
		self.assertEqual(self.v1["ui_status"], "Planned")

	def test_da_plans_only_for_own_da_id(self):
		out = visits.plan_visit(
			farmer_id="FR-1", scheduled_at=self.tomorrow, da_id="DA-000003", ctx=_ctx(DA1)
		)
		self.assertEqual(out["da_id"], "DA-000001")

	def test_supervisor_plans_for_own_woreda_and_is_recorded(self):
		out = visits.plan_visit(
			farmer_id="FR-2", scheduled_at=self.tomorrow, da_id="DA-000002", ctx=_ctx(SUP)
		)
		self.assertEqual(out["assigned_by"], SUP)
		with self.assertRaises(frappe.PermissionError):
			visits.plan_visit(farmer_id="FR-3", scheduled_at=self.tomorrow, da_id="DA-000003", ctx=_ctx(SUP))

	def test_plan_requires_schedule_and_is_idempotent(self):
		with self.assertRaises(frappe.ValidationError):
			visits.plan_visit(farmer_id="FR-1", scheduled_at=None, ctx=_ctx(DA1))
		a = visits.plan_visit(
			farmer_id="FR-1", scheduled_at=self.tomorrow, client_op_id="op-v1", ctx=_ctx(DA1)
		)
		b = visits.plan_visit(
			farmer_id="FR-1", scheduled_at=self.tomorrow, client_op_id="op-v1", ctx=_ctx(DA1)
		)
		self.assertEqual(a["name"], b["name"])

	def test_advisory_qa_only_on_advisory_visits(self):
		out = visits.plan_visit(
			farmer_id="FR-1",
			scheduled_at=self.tomorrow,
			visit_type="Advisory",
			advisory_qa=[{"question": "Rust on teff?", "answer": "Spray within 3 days"}],
			ctx=_ctx(DA1),
		)
		self.assertEqual(len(out["advisory_qa"]), 1)
		doc = frappe.get_doc("DA Visit", self.v1["name"])
		doc.append("advisory_qa", {"question": "x"})
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	# -- lifecycle ----------------------------------------------------------------

	def test_start_then_submit_creates_follow_up_task_and_visit(self):
		out = visits.start_visit(
			self.v1["name"], gps={"lat": 9.1236, "lng": 37.0521, "accuracy_m": 12}, ctx=_ctx(DA1)
		)
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
			ctx=_ctx(DA1),
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
			ctx=_ctx(DA1),
		)
		self.assertTrue(out["follow_up_task"])
		self.assertFalse(out["follow_up_visit"])
		self.assertEqual(frappe.db.count("DA Visit", {"da_id": "DA-000001"}), 1)

	def test_closed_visit_cannot_change(self):
		visits.submit_outcome(self.v1["name"], observed="done", ctx=_ctx(DA1))
		with self.assertRaises(frappe.ValidationError):
			visits.submit_outcome(self.v1["name"], observed="again", ctx=_ctx(DA1))
		doc = frappe.get_doc("DA Visit", self.v1["name"])
		doc.physical_status = "Planned"
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_reschedule_supersedes_and_links(self):
		new = visits.reschedule_visit(
			self.v1["name"], scheduled_at=self.tomorrow, reason="Farmer unavailable", ctx=_ctx(DA1)
		)
		old = frappe.get_doc("DA Visit", self.v1["name"])
		self.assertEqual((old.physical_status, old.reschedule_reason), ("Rescheduled", "Farmer unavailable"))
		self.assertEqual((new["physical_status"], new["rescheduled_from"]), ("Planned", self.v1["name"]))
		with self.assertRaises(frappe.ValidationError):
			visits.start_visit(self.v1["name"], ctx=_ctx(DA1))

	def test_derived_statuses(self):
		past = visits.plan_visit(
			farmer_id="FR-9", scheduled_at=add_to_date(now_datetime(), hours=-3), ctx=_ctx(DA1)
		)
		self.assertEqual(past["planner_status"], "overdue")
		doc = frappe.get_doc("DA Visit", past["name"])
		doc.farmer_confirmation = "Confirmed"
		doc.save()
		self.assertEqual(visits.get_visit(past["name"], ctx=_ctx(DA1))["ui_status"], "Confirmed")

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

	def test_single_document_scope_and_executive_read_only(self):
		with self.assertRaises(frappe.PermissionError):
			visits.get_visit(self.v3["name"], ctx=_ctx(DA1))
		visits.get_visit(self.v1["name"], ctx=_ctx(EXEC))
		with self.assertRaises(frappe.PermissionError):
			visits.start_visit(self.v1["name"], ctx=_ctx(EXEC))
		with self.assertRaises(frappe.PermissionError):
			visits.start_visit(self.v3["name"], ctx=_ctx(SUP))

	# -- dashboard ------------------------------------------------------------------

	def test_dashboard_counts_and_today_items(self):
		frappe.set_user(ADMIN)
		for i in range(3):
			visits.plan_visit(
				farmer_id=f"FR-{i}", scheduled_at=f"{add_days(today(), -i - 1)} 10:00:00", ctx=_ctx(DA1)
			)
		visits.submit_outcome(self.v1["name"], observed="ok", ctx=_ctx(DA1))
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
		rows, total = visits.list_visits(_ctx(DA1), date=today())
		self.assertEqual(total, 1)
		self.assertEqual(rows[0]["name"], self.v1["name"])
		rows, total = visits.list_visits(_ctx(DA1), status="Completed")
		self.assertEqual(total, 0)

	def test_list_area_filters_enforce_scope(self):
		frappe.set_user(SUP)
		try:
			rows, total = visits.list_visits(_ctx(SUP), woreda=self.woreda)
			self.assertEqual(total, 1)
			self.assertEqual(rows[0]["name"], self.v1["name"])
			rows, total = visits.list_visits(_ctx(SUP), kebele=self.kebele)
			self.assertEqual(total, 1)
			with self.assertRaises(frappe.PermissionError):
				visits.list_visits(_ctx(SUP), woreda=self.other_woreda)
		finally:
			frappe.set_user(ADMIN)

		frappe.set_user(DA1)
		try:
			rows, total = visits.list_visits(_ctx(DA1), kebele=self.kebele)
			self.assertEqual(total, 1)
			with self.assertRaises(frappe.PermissionError):
				visits.list_visits(_ctx(DA1), kebele=self.kebele_alt)  # same Woreda, not DA1's posting
		finally:
			frappe.set_user(ADMIN)
