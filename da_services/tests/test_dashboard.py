"""Dashboard summary / today read models and the demo seed (DA-198 / DA-373)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, get_datetime, today

from da_services.da_services.doctype.da_visit.da_visit import COMPLETE, IN_PROGRESS, PLANNED
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import constants as C
from da_services.services import dashboard, geo, tasks, visits
from da_services.setup import seed_demo
from da_services.tests.fixtures import (
	aligned_mock_geo,
	bind_da_user,
	cleanup_test_rows,
	ensure_da_user,
	require_geo_masters,
)
from da_services.utils.permissions import get_scope_context

DA1 = "dash-da1@da.local"  # DA-000001, primary Region / Woreda
SUP = "dash-sup@da.local"  # Supervisor, primary Woreda
SUP2 = "dash-sup2@da.local"  # Supervisor with two Woredas
EXEC = "dash-exec@da.local"  # Executive, other Region

# Rows this module creates; setUp / tearDown delete only these.
USERS = (DA1, SUP, SUP2, EXEC)
DA_IDS = ("DA-000001", "DA-000003")
FARMER_IDS = ("FR-DASH-K03-1", "FR-DASH-K03-2", "FR-DASH-OTHER")
# seed_demo writes its own DA user and the DA-000001 rows it counts.
DEMO_USER = "da.demo@da.local"

# Mock DA Registry postings come from the synced geography masters (aligned_mock_geo):
# DA-000001 → primary Woreda (kebele), DA-000002 → kebele_alt, DA-000003 → other Woreda.


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


def _aligned_geo() -> dict:
	"""Rebuild the mock registry from the live masters and return the geography it uses."""
	require_geo_masters()
	MockDARegistryClient.reset()
	return aligned_mock_geo()


class TestDashboard(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		cleanup_test_rows(users=(*USERS, DEMO_USER), da_ids=DA_IDS)
		g = _aligned_geo()
		self.geo = g
		self.woreda = g["woreda"]
		_user(DA1, [C.ROLE_DA])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_user(EXEC, [C.ROLE_EXECUTIVE])
		bind_da_user(DA1, "DA-000001")  # plan_visit and the DA tiles resolve the DA through the User
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope=g["region"], woreda_scope=g["woreda"])
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope=g["region"], woreda_scope=g["woreda"])
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope=g["other_region"])
		admin = _ctx("Administrator")
		t = today()
		# 3 visits in range: 2 complete, 1 still open today; 1 outside range (40 days ago)
		# Finish earlier visits before leaving one open. An open Crop Survey
		# blocks another Crop Survey for the same farmer.
		for op, when, complete in (
			("d-v4", f"{add_days(t, -40)} 09:00:00", True),
			("d-v1", f"{add_days(t, -10)} 09:00:00", True),
			("d-v2", f"{add_days(t, -3)} 11:00:00", True),
			("d-v3", f"{t} 23:30:00", False),  # late enough to stay "Upcoming" during a test run
		):
			v = visits.plan_visit(
				farmer_id="FRM-1", scheduled_at=when, da_id="DA-000001", client_op_id=op, ctx=admin
			)
			if complete:
				visits.submit_outcome(v["name"], observed="ok", ctx=admin)
		# tasks: 2 open due today (1 urgent), 1 open later, 1 done in range
		tasks.create_task(
			title="Report", da_id="DA-000001", due_at=f"{t} 13:00:00", priority="Urgent", ctx=admin
		)
		tasks.create_task(title="Sample", da_id="DA-000001", due_at=f"{t} 08:30:00", ctx=admin)
		tasks.create_task(title="Later", da_id="DA-000001", due_at=f"{add_days(t, 4)} 09:00:00", ctx=admin)
		done = tasks.create_task(
			title="Old", da_id="DA-000001", due_at=f"{add_days(t, -5)} 09:00:00", ctx=admin
		)
		tasks.complete_task(done["name"], ctx=admin)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def tearDown(self):
		frappe.set_user("Administrator")
		cleanup_test_rows(users=(*USERS, DEMO_USER), da_ids=DA_IDS)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def test_summary_for_da_counts_own_rows_in_range(self):
		frappe.set_user(DA1)
		out = dashboard.summary(_ctx(DA1), da_id="DA-000009")  # body da_id ignored for a DA
		self.assertEqual(out["view"], "agent")
		self.assertEqual(out["da_id"], "DA-000001")
		self.assertEqual(out["range"]["to"], today())
		self.assertEqual(out["range"]["from"], add_days(today(), -29))
		self.assertEqual(
			out["visits"], {"planned": 3, "completed": 2, "missed": 0, "rescheduled": 0, "in_progress": 0}
		)  # planned includes completed
		self.assertEqual(out["tasks"], {"open": 3, "done": 1, "urgent": 1, "due_today": 2})
		self.assertEqual(out["sync"]["server_pending"], 0)
		self.assertEqual(out["sync"]["conflicts"], 0)
		self.assertIsNotNone(out["sync"]["last_received_at"])

	def test_summary_custom_range(self):
		out = dashboard.summary(_ctx(DA1), from_date=add_days(today(), -45), to_date=today())
		self.assertEqual(out["visits"]["completed"], 3)
		with self.assertRaises(frappe.ValidationError):
			dashboard.summary(_ctx(DA1), from_date=today(), to_date=add_days(today(), -1))

	def test_officer_da_id_is_scope_checked_for_agent_view(self):
		# Supervisor with da_id → agent tiles for that DA (still scope-checked).
		out = dashboard.summary(_ctx(SUP), da_id="DA-000001")
		self.assertEqual(out["view"], "agent")
		self.assertEqual(out["visits"]["completed"], 2)
		with self.assertRaises(frappe.PermissionError):
			dashboard.summary(_ctx(EXEC), da_id="DA-000001")  # DA-000001 is outside EXEC's Region
		with self.assertRaises(frappe.PermissionError):
			dashboard.summary(_ctx(SUP), da_id="DA-000003")
		# Executive without da_id still needs a DA (no region roll-up on summary yet).
		with self.assertRaises(frappe.ValidationError):
			dashboard.summary(_ctx(EXEC))

	def test_supervisor_summary_without_da_id_is_woreda_view(self):
		out = dashboard.summary(_ctx(SUP))
		self.assertEqual(out["view"], "woreda")
		self.assertEqual(out["woreda"]["code"], self.woreda)
		self.assertIn("kebeles", out)
		self.assertIn("stats", out)

	def test_today_merges_visits_and_tasks_in_clock_order(self):
		# The open visit is at 23:30 today; pin "now" to the morning so the
		# planner status is "Upcoming" no matter when the suite runs.
		with patch(
			"da_services.services.visits.now_datetime",
			return_value=get_datetime(f"{today()} 08:00:00"),
		):
			out = dashboard.today(_ctx(DA1))
		self.assertEqual(out["date"], today())
		self.assertEqual(
			[(i["kind"], i["time"]) for i in out["items"]],
			[("task", "08:30"), ("task", "13:00"), ("visit", "23:30")],
		)
		visit = out["items"][2]
		self.assertEqual((visit["status"], visit["href"]), ("Upcoming", f"/visits/{visit['id']}"))
		task = out["items"][1]
		self.assertEqual((task["status"], task["priority"]), ("Due today", "Urgent"))
		self.assertTrue(task["href"].startswith("/tasks/"))

	def test_today_past_date_marks_open_tasks_overdue(self):
		d = add_days(today(), -5)
		out = dashboard.today(_ctx(DA1), date=d)
		self.assertEqual([i["status"] for i in out["items"]], ["Done"])
		tasks.create_task(
			title="Missed", da_id="DA-000001", due_at=f"{d} 10:00:00", ctx=_ctx("Administrator")
		)
		out = dashboard.today(_ctx(DA1), date=d)
		self.assertEqual(sorted(i["status"] for i in out["items"]), ["Done", "Overdue"])

	def test_seed_demo_is_idempotent_and_guarded(self):
		# Start without DA-000001 / demo-user rows so the counts below are the seed's alone.
		# Also clear its kebele assignments: seed_demo.assign uses client_op_id seed-kas-1 and
		# skips the assignment notification on replay if that row already exists.
		cleanup_test_rows(users=(*USERS, DEMO_USER), da_ids=DA_IDS)
		saved = frappe.conf.get("developer_mode")
		frappe.conf.developer_mode = 0
		try:
			with self.assertRaises(frappe.ValidationError):
				seed_demo.run(force=False)
		finally:
			frappe.conf.developer_mode = saved
		first = seed_demo.run(force=True)
		second = seed_demo.run(force=True)
		self.assertEqual(first, second)
		self.assertEqual(first["visits"], 6)
		self.assertEqual(first["tasks"], 7)
		# assignment_created (from assign) + sync + broadcast
		self.assertEqual(first["notifications"], 3)
		out = dashboard.summary(_ctx("da.demo@da.local"))
		self.assertEqual(
			out["visits"], {"planned": 6, "completed": 4, "missed": 0, "rescheduled": 0, "in_progress": 0}
		)  # the tile's "4 of 6 planned"
		self.assertEqual(out["tasks"], {"open": 4, "done": 3, "urgent": 1, "due_today": 2})


class TestWoredaHealth(IntegrationTestCase):
	"""Supervisor Programme Health — assigned Woreda only (DA-373)."""

	def setUp(self):
		frappe.set_user("Administrator")
		# Woreda / Kebele display names come from the synced masters (DA Woreda / DA Kebele).
		g = _aligned_geo()
		self.geo = g
		self.woreda, self.other_woreda = g["woreda"], g["other_woreda"]
		self.k03, self.k07, self.other_kebele = g["kebele"], g["kebele_alt"], g["other_kebele"]
		cleanup_test_rows(users=USERS, da_ids=DA_IDS, farmer_ids=FARMER_IDS)
		_user(DA1, [C.ROLE_DA])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_user(SUP2, [C.ROLE_SUPERVISOR])
		_user(EXEC, [C.ROLE_EXECUTIVE])
		bind_da_user(DA1, "DA-000001")
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope=g["region"], woreda_scope=self.woreda)
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope=g["region"], woreda_scope=self.woreda)
		_grant(SUP2, C.ROLE_SUPERVISOR, region_scope=g["region"], woreda_scope=self.woreda)
		_grant(SUP2, C.ROLE_SUPERVISOR, region_scope=g["other_region"], woreda_scope=self.other_woreda)
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope=g["other_region"])

		frappe.get_doc(
			{
				"doctype": "DA Farmer Reference",
				"farmer_id": "FR-DASH-K03-1",
				"farmer_name": "Farmer K03",
				"kebele": self.k03,
				"woreda": self.woreda,
				"region": g["region"],
				"registration_state": "APPROVED",
			}
		).insert(ignore_permissions=True, ignore_links=True)
		frappe.get_doc(
			{
				"doctype": "DA Farmer Reference",
				"farmer_id": "FR-DASH-K03-2",
				"farmer_name": "Farmer K03b",
				"kebele": self.k03,
				"woreda": self.woreda,
				"region": g["region"],
				"registration_state": "APPROVED",
			}
		).insert(ignore_permissions=True, ignore_links=True)
		frappe.get_doc(
			{
				"doctype": "DA Farmer Reference",
				"farmer_id": "FR-DASH-OTHER",
				"farmer_name": "Other Woreda Farmer",
				"kebele": self.other_kebele,
				"woreda": self.other_woreda,
				"region": g["other_region"],
				"registration_state": "APPROVED",
			}
		).insert(ignore_permissions=True, ignore_links=True)

		t = today()
		# 2 planned in K03 (1 complete) → 50% At risk; 1 complete in K07 → 100% On track
		self._insert_visit(self.k03, f"{add_days(t, -2)} 09:00:00", COMPLETE, "wh-v1")
		self._insert_visit(self.k03, f"{add_days(t, -1)} 10:00:00", PLANNED, "wh-v2")
		self._insert_visit(self.k07, f"{add_days(t, -1)} 11:00:00", COMPLETE, "wh-v3")
		# Foreign woreda visit must never appear for SUP
		self._insert_visit(
			self.other_kebele, f"{add_days(t, -1)} 12:00:00", COMPLETE, "wh-v4", woreda=self.other_woreda
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def tearDown(self):
		frappe.set_user("Administrator")
		cleanup_test_rows(users=USERS, da_ids=DA_IDS, farmer_ids=FARMER_IDS)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def _insert_visit(self, kebele, scheduled_at, status, client_op_id, *, woreda=None):
		"""Insert a visit respecting DA Visit status transitions (new rows cannot be Complete)."""
		woreda = woreda or self.woreda
		in_primary = woreda == self.woreda
		da_id = "DA-000001" if in_primary else "DA-000003"
		doc = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"user": ensure_da_user(da_id),
				"da_id": da_id,
				"farmer_id": "FRM-1",
				"visit_type": "Crop Survey",
				"scheduled_at": scheduled_at,
				"physical_status": PLANNED,
				"kebele": kebele,
				"woreda": woreda,
				"region": self.geo["region"] if in_primary else self.geo["other_region"],
				"source": "Planned",
				"client_op_id": client_op_id,
			}
		)
		doc.insert(ignore_permissions=True, ignore_links=True)
		if status == COMPLETE:
			doc.reload()
			doc.physical_status = IN_PROGRESS
			doc.save(ignore_permissions=True)
			doc.reload()
			doc.physical_status = COMPLETE
			doc.completed_at = get_datetime(scheduled_at)
			doc.outcome = "Dashboard test outcome"
			doc.save(ignore_permissions=True)
		elif status != PLANNED:
			doc.reload()
			doc.physical_status = status
			doc.save(ignore_permissions=True)
		return doc.name

	def test_status_thresholds(self):
		self.assertEqual(dashboard.status_for_visit_pct(80), dashboard.STATUS_ON_TRACK)
		self.assertEqual(dashboard.status_for_visit_pct(60), dashboard.STATUS_WATCH)
		self.assertEqual(dashboard.status_for_visit_pct(59), dashboard.STATUS_AT_RISK)

	def test_supervisor_sees_only_assigned_woreda(self):
		out = dashboard.summary(_ctx(SUP))
		self.assertEqual(out["view"], "woreda")
		self.assertEqual(out["woreda"]["code"], self.woreda)
		self.assertEqual(out["woreda"]["name"], geo.display_name("DA Woreda", self.woreda))
		codes = {row["kebele_code"] for row in out["kebeles"]}
		self.assertTrue(codes)
		master_codes = {k["code"] for k in geo.kebeles_for_woreda(self.woreda)}
		self.assertTrue(codes <= master_codes)  # one page of the Woreda's own Kebeles
		self.assertEqual(out["total"], len(master_codes))
		self.assertNotIn(self.other_kebele, codes)
		self.assertEqual(out["stats"]["farmers"], 2)  # only the primary Woreda's farmers
		self.assertGreaterEqual(out["stats"]["active_agents"], 1)

	def test_foreign_woreda_denied(self):
		with self.assertRaises(frappe.PermissionError):
			dashboard.summary(_ctx(SUP), woreda=self.other_woreda)

	def test_multi_woreda_requires_woreda_param(self):
		with self.assertRaises(frappe.ValidationError):
			dashboard.summary(_ctx(SUP2))
		out = dashboard.summary(_ctx(SUP2), woreda=self.woreda)
		self.assertEqual(out["woreda"]["code"], self.woreda)
		out_other = dashboard.summary(_ctx(SUP2), woreda=self.other_woreda)
		self.assertEqual(out_other["woreda"]["code"], self.other_woreda)
		other_codes = {k["code"] for k in geo.kebeles_for_woreda(self.other_woreda)}
		self.assertTrue(all(r["kebele_code"] in other_codes for r in out_other["kebeles"]))

	def test_kebele_rows_visit_pct_and_status(self):
		out = dashboard.summary(_ctx(SUP))
		by_code = {r["kebele_code"]: r for r in out["kebeles"]}
		k03 = by_code[self.k03]
		self.assertEqual(k03["farmers"], 2)
		self.assertEqual(k03["visit_pct"], 50)
		self.assertEqual(k03["status"], dashboard.STATUS_AT_RISK)
		self.assertIsNotNone(k03["last_sync"])
		k07 = by_code[self.k07]
		self.assertEqual(k07["visit_pct"], 100)
		self.assertEqual(k07["status"], dashboard.STATUS_ON_TRACK)

	def test_filters_status_kebele_agents_and_search(self):
		out = dashboard.summary(_ctx(SUP), status=dashboard.STATUS_AT_RISK)
		self.assertTrue(out["kebeles"])
		self.assertTrue(all(r["status"] == dashboard.STATUS_AT_RISK for r in out["kebeles"]))

		out_k = dashboard.summary(_ctx(SUP), kebele=self.k03)
		self.assertEqual(out_k["total"], 1)
		self.assertEqual(out_k["kebeles"][0]["kebele_code"], self.k03)

		with self.assertRaises(frappe.ValidationError):
			dashboard.summary(_ctx(SUP), kebele=self.other_kebele)

		# Free-text search matches the Kebele display name from the masters.
		needle = geo.display_name("DA Kebele", self.k03)[:5]
		out_q = dashboard.summary(_ctx(SUP), q=needle)
		self.assertIn(self.k03, {r["kebele_code"] for r in out_q["kebeles"]})
		self.assertTrue(all(needle.lower() in r["kebele"].lower() for r in out_q["kebeles"]))

		# Stats stay full-woreda even when the table is filtered
		self.assertEqual(out["stats"]["farmers"], 2)

	def test_invalid_status_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			dashboard.summary(_ctx(SUP), status="Needs support")

	def test_executive_without_da_id_cannot_get_woreda_summary(self):
		with self.assertRaises(frappe.ValidationError):
			dashboard.summary(_ctx(EXEC), woreda=self.other_woreda)
