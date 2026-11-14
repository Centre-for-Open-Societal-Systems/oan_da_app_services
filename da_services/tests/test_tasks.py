"""DA Task doctype, scope hooks and service layer (DA-194)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime, today

from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import constants as C
from da_services.services import tasks
from da_services.tests.fixtures import (
	aligned_mock_geo,
	bind_da_user,
	cleanup_test_rows,
	require_geo_masters,
)
from da_services.utils.permissions import get_scope_context

# Geography is the mock DA Registry's, rewritten from the synced masters (aligned_mock_geo).
DA1 = "task-da1@da.local"  # DA-000001, primary Woreda
DA2 = "task-da2@da.local"  # DA-000002, primary Woreda
DA3 = "task-da3@da.local"  # DA-000003, other Region
SUP = "task-sup@da.local"  # Supervisor, primary Woreda
EXEC = "task-exec@da.local"  # Executive, other Region
COMMS = "task-comms@da.local"

# Rows this module creates; setUp / tearDown delete only these.
USERS = (DA1, DA2, DA3, SUP, EXEC, COMMS)
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


class TestDATask(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		cleanup_test_rows(users=USERS, da_ids=DA_IDS)
		require_geo_masters()
		MockDARegistryClient.reset()
		g = aligned_mock_geo()
		self.geo = g
		_user(DA1, [C.ROLE_DA])
		_user(DA2, [C.ROLE_DA])
		_user(DA3, [C.ROLE_DA])
		# A DA's own DA-ID is read from User.da_registry_id.
		bind_da_user(DA1, "DA-000001")
		bind_da_user(DA2, "DA-000002")
		bind_da_user(DA3, "DA-000003")
		_user(SUP, [C.ROLE_SUPERVISOR])
		_user(EXEC, [C.ROLE_EXECUTIVE])
		_user(COMMS, [C.ROLE_COMMS])
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope=g["region"], woreda_scope=g["woreda"])
		_grant(DA2, C.ROLE_DA, da_id="DA-000002", region_scope=g["region"], woreda_scope=g["woreda"])
		_grant(
			DA3, C.ROLE_DA, da_id="DA-000003", region_scope=g["other_region"], woreda_scope=g["other_woreda"]
		)
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope=g["region"], woreda_scope=g["woreda"])
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope=g["other_region"])
		_grant(COMMS, C.ROLE_COMMS)
		# Seed: two tasks for DA-000001 (one urgent, due today), one for DA-000002, one for DA-000003
		self.t1 = tasks.create_task(
			title="Submit weekly crop report",
			da_id="DA-000001",
			task_type="Report due",
			due_at=f"{today()} 13:00:00",
			ctx=get_scope_context("Administrator"),
		)
		self.t2 = tasks.create_task(
			title="Follow-up Lelise",
			da_id="DA-000001",
			task_type="Follow-up",
			priority="Urgent",
			due_at=add_days(today(), 3),
			ctx=get_scope_context("Administrator"),
		)
		self.t3 = tasks.create_task(
			title="Marta survey",
			da_id="DA-000002",
			task_type="Survey due",
			ctx=get_scope_context("Administrator"),
		)
		self.t4 = tasks.create_task(
			title="Other region task", da_id="DA-000003", ctx=get_scope_context("Administrator")
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def tearDown(self):
		frappe.set_user("Administrator")
		cleanup_test_rows(users=USERS, da_ids=DA_IDS)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	# -- doctype rules -----------------------------------------------------------

	def test_scope_codes_are_filled_from_registry_posting(self):
		doc = frappe.get_doc("DA Task", self.t1["name"])
		self.assertEqual((doc.region, doc.woreda), (self.geo["region"], self.geo["woreda"]))
		self.assertEqual(doc.status, "Open")
		self.assertEqual(doc.created_by_user, "Administrator")
		self.assertTrue(doc.name.startswith("DA-TSK-"))

	def test_done_sets_completed_at_and_cancelled_is_final(self):
		doc = frappe.get_doc("DA Task", self.t1["name"])
		doc.mark_done()
		self.assertEqual(doc.status, "Done")
		self.assertIsNotNone(doc.completed_at)
		doc.cancel_task("no longer needed")
		self.assertEqual(doc.status, "Cancelled")
		self.assertIsNone(doc.completed_at)
		doc.status = "Open"
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_client_op_id_is_idempotent(self):
		ctx = get_scope_context("Administrator")
		a = tasks.create_task(title="dup", da_id="DA-000001", client_op_id="op-1", ctx=ctx)
		b = tasks.create_task(title="dup again", da_id="DA-000001", client_op_id="op-1", ctx=ctx)
		self.assertEqual(a["name"], b["name"])
		self.assertEqual(frappe.db.count("DA Task", {"client_op_id": "op-1"}), 1)
		# Direct insert with the same key is refused by the controller too.
		with self.assertRaises(frappe.DuplicateEntryError):
			frappe.get_doc(
				{"doctype": "DA Task", "da_id": "DA-000001", "title": "x", "client_op_id": "op-1"}
			).insert(ignore_permissions=True, ignore_links=True)

	# -- scope: lists via permission_query_conditions ---------------------------

	def _visible(self, user) -> set[str]:
		frappe.set_user(user)
		return {r.da_id for r in frappe.get_list("DA Task", fields=["da_id"])}

	def test_da_sees_only_own_tasks(self):
		self.assertEqual(self._visible(DA1), {"DA-000001"})
		self.assertEqual(self._visible(DA2), {"DA-000002"})

	def test_supervisor_sees_own_woreda_only(self):
		self.assertEqual(self._visible(SUP), {"DA-000001", "DA-000002"})

	def test_executive_sees_own_region_only(self):
		self.assertEqual(self._visible(EXEC), {"DA-000003"})

	def test_comms_officer_sees_nothing(self):
		# No DocPerm at all for Communications Officer: Frappe refuses the list outright,
		# which is stricter than an empty result (Appendix D: no access to DA identity).
		frappe.set_user(COMMS)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_list("DA Task", fields=["da_id"])

	def test_administrator_sees_everything(self):
		self.assertEqual(self._visible("Administrator"), {"DA-000001", "DA-000002", "DA-000003"})

	# -- scope: single document --------------------------------------------------

	def test_has_permission_mirrors_the_list_rule(self):
		t1 = frappe.get_doc("DA Task", self.t1["name"])
		t4 = frappe.get_doc("DA Task", self.t4["name"])
		self.assertTrue(frappe.has_permission("DA Task", "read", t1, user=DA1))
		self.assertFalse(frappe.has_permission("DA Task", "read", t4, user=DA1))
		self.assertTrue(frappe.has_permission("DA Task", "write", t1, user=SUP))
		self.assertFalse(frappe.has_permission("DA Task", "write", t4, user=SUP))
		self.assertTrue(frappe.has_permission("DA Task", "read", t4, user=EXEC))
		self.assertFalse(frappe.has_permission("DA Task", "write", t4, user=EXEC))
		self.assertFalse(frappe.has_permission("DA Task", "read", t1, user=COMMS))

	# -- service layer -----------------------------------------------------------

	def test_da_creates_only_for_own_da_id(self):
		ctx = get_scope_context(DA1)
		out = tasks.create_task(title="mine", da_id="DA-000002", ctx=ctx)  # body da_id ignored
		self.assertEqual(out["da_id"], "DA-000001")

	def test_supervisor_cannot_create_outside_woreda(self):
		ctx = get_scope_context(SUP)
		tasks.create_task(title="ok", da_id="DA-000002", ctx=ctx)
		with self.assertRaises(frappe.PermissionError):
			tasks.create_task(title="not ok", da_id="DA-000003", ctx=ctx)

	def test_complete_respects_scope(self):
		tasks.complete_task(self.t1["name"], ctx=get_scope_context(DA1))
		self.assertEqual(frappe.db.get_value("DA Task", self.t1["name"], "status"), "Done")
		with self.assertRaises(frappe.PermissionError):
			tasks.complete_task(self.t3["name"], ctx=get_scope_context(DA1))
		with self.assertRaises(frappe.PermissionError):
			tasks.complete_task(self.t4["name"], ctx=get_scope_context(SUP))

	def test_list_filters_and_scope(self):
		frappe.set_user(DA1)
		rows, total = tasks.list_tasks(get_scope_context(DA1), due="today")
		self.assertEqual(total, 1)
		self.assertEqual(rows[0]["title"], "Submit weekly crop report")
		rows, total = tasks.list_tasks(get_scope_context(DA1), status="Open")
		self.assertEqual(total, 2)
		frappe.set_user(SUP)
		_rows, total = tasks.list_tasks(get_scope_context(SUP))
		self.assertEqual(total, 3)

	def test_dashboard_counts(self):
		counts = tasks.dashboard_counts("DA-000001", add_days(today(), -30), today())
		self.assertEqual(counts, {"open": 2, "done": 0, "urgent": 1, "due_today": 1})
		tasks.complete_task(self.t1["name"], ctx=get_scope_context("Administrator"))
		counts = tasks.dashboard_counts("DA-000001", add_days(today(), -30), add_days(today(), 1))
		self.assertEqual((counts["open"], counts["done"], counts["due_today"]), (1, 1, 0))
		self.assertIsNotNone(now_datetime())
