"""Dashboard summary / today read models and the demo seed (DA-198)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from da_services.services import constants as C
from da_services.services import dashboard, tasks, visits
from da_services.setup import seed_demo
from da_services.utils.permissions import get_scope_context

DA1 = "dash-da1@da.local"  # DA-000001, ET04 / ET04-W01
SUP = "dash-sup@da.local"  # Supervisor ET04-W01
EXEC = "dash-exec@da.local"  # Executive ET03


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


class TestDashboard(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for dt in ("DA Notification", "DA Visit", "DA Task", "DA RBAC Assignment", "DA Integration Event"):
			frappe.db.delete(dt)
		_user(DA1, [C.ROLE_DA])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_user(EXEC, [C.ROLE_EXECUTIVE])
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope="ET03")
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
		for dt in ("DA Notification", "DA Visit", "DA Task", "DA RBAC Assignment", "DA Integration Event"):
			frappe.db.delete(dt)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def test_summary_for_da_counts_own_rows_in_range(self):
		frappe.set_user(DA1)
		out = dashboard.summary(_ctx(DA1), da_id="DA-000009")  # body da_id ignored for a DA
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

	def test_officer_must_name_a_da_inside_scope(self):
		with self.assertRaises(frappe.ValidationError):
			dashboard.summary(_ctx(SUP))
		out = dashboard.summary(_ctx(SUP), da_id="DA-000001")
		self.assertEqual(out["visits"]["completed"], 2)
		with self.assertRaises(frappe.PermissionError):
			dashboard.summary(_ctx(EXEC), da_id="DA-000001")  # ET04 is outside ET03
		with self.assertRaises(frappe.PermissionError):
			dashboard.summary(_ctx(SUP), da_id="DA-000003")

	def test_today_merges_visits_and_tasks_in_clock_order(self):
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
		# Start from an empty site so the counts below are the seed's alone.
		for dt in ("DA Notification", "DA Visit", "DA Task"):
			frappe.db.delete(dt)
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
		self.assertEqual(first["notifications"], 3)  # sync, broadcast, assignment
		out = dashboard.summary(_ctx("da.demo@da.local"))
		self.assertEqual(
			out["visits"], {"planned": 6, "completed": 4, "missed": 0, "rescheduled": 0, "in_progress": 0}
		)  # the tile's "4 of 6 planned"
		self.assertEqual(out["tasks"], {"open": 4, "done": 3, "urgent": 1, "due_today": 2})
