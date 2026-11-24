"""DA Task doctype and service layer (Visit-style Role DocPerm + User Permissions)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime, today

from da_services.services import constants as C
from da_services.services import tasks
from da_services.tests.fixtures import (
	as_user,
	ensure_doctype_schema,
	ensure_geo_masters,
	set_da_registry_id,
	stamp_task_geo,
)

DA1 = "task-da1@da.local"  # DA-000001, ET04 / ET04-W01
DA2 = "task-da2@da.local"  # DA-000002, ET04 / ET04-W01
DA3 = "task-da3@da.local"  # DA-000003, ET03
SUP = "task-sup@da.local"  # Supervisor ET04 / ET04-W01
EXEC = "task-exec@da.local"  # Executive ET03
COMMS = "task-comms@da.local"

# Stamp region/zone/woreda only: DA User Permissions from RBAC grants are at those
# levels. Stamping kebele without a matching Effective Kebele Assignment UP hides rows.
GEO_ET04 = {
	"region": "ET04",
	"zone": "ET04-Z01",
	"woreda": "ET04-W01",
	"kebele": None,
}
GEO_ET03 = {
	"region": "ET03",
	"zone": "ET03-Z02",
	"woreda": "ET03-W02",
	"kebele": None,
}


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


def _create(user: str, *, geo: dict, **kwargs) -> dict:
	out = tasks.create_task(user=user, **kwargs)
	stamp_task_geo(out["name"], geo)
	return out


class TestDATask(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		ensure_doctype_schema("DA Task")
		frappe.db.delete("DA Task")
		frappe.db.delete("DA RBAC Assignment")
		ensure_geo_masters()
		_user(DA1, [C.ROLE_DA])
		_user(DA2, [C.ROLE_DA])
		_user(DA3, [C.ROLE_DA])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_user(EXEC, [C.ROLE_EXECUTIVE])
		_user(COMMS, [C.ROLE_COMMS])
		set_da_registry_id(DA1, "DA-000001")
		set_da_registry_id(DA2, "DA-000002")
		set_da_registry_id(DA3, "DA-000003")
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA2, C.ROLE_DA, da_id="DA-000002", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA3, C.ROLE_DA, da_id="DA-000003", region_scope="ET03", woreda_scope="ET03-W02")
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope="ET03")
		_grant(COMMS, C.ROLE_COMMS)
		with as_user("Administrator"):
			self.t1 = _create(
				DA1,
				geo=GEO_ET04,
				title="Submit weekly crop report",
				task_type="Report due",
				due_at=f"{today()} 13:00:00",
			)
			self.t2 = _create(
				DA1,
				geo=GEO_ET04,
				title="Follow-up Lelise",
				task_type="Follow-up",
				priority="Urgent",
				due_at=add_days(today(), 3),
			)
			self.t3 = _create(DA2, geo=GEO_ET04, title="Marta survey", task_type="Survey due")
			self.t4 = _create(DA3, geo=GEO_ET03, title="Other region task")
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA Task")
		frappe.db.delete("DA RBAC Assignment")
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	# -- doctype rules -----------------------------------------------------------

	def test_geo_stamps_and_user_bind_da_id(self):
		doc = frappe.get_doc("DA Task", self.t1["name"])
		self.assertEqual(doc.user, DA1)
		self.assertEqual(doc.da_id, "DA-000001")
		self.assertEqual((doc.region, doc.woreda), ("ET04", "ET04-W01"))
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
		with as_user("Administrator"):
			a = _create(DA1, geo=GEO_ET04, title="dup", client_op_id="op-1")
			b = tasks.create_task(title="dup again", user=DA1, client_op_id="op-1")
		self.assertEqual(a["name"], b["name"])
		self.assertEqual(frappe.db.count("DA Task", {"client_op_id": "op-1"}), 1)
		with self.assertRaises(frappe.DuplicateEntryError):
			frappe.get_doc(
				{
					"doctype": "DA Task",
					"user": DA1,
					"da_id": "DA-000001",
					"title": "x",
					"client_op_id": "op-1",
				}
			).insert(ignore_permissions=True, ignore_links=True)

	# -- scope: lists via User Permissions on DA Region / Zone / Woreda -----------

	def _visible(self, user) -> set[str]:
		frappe.set_user(user)
		return {r.da_id for r in frappe.get_list("DA Task", fields=["da_id"])}

	def test_da_sees_the_tasks_of_their_woreda(self):
		self.assertEqual(self._visible(DA1), {"DA-000001", "DA-000002"})
		self.assertEqual(self._visible(DA2), {"DA-000001", "DA-000002"})
		self.assertEqual(self._visible(DA3), {"DA-000003"})

	def test_supervisor_sees_own_woreda_only(self):
		self.assertEqual(self._visible(SUP), {"DA-000001", "DA-000002"})

	def test_executive_sees_own_region_only(self):
		self.assertEqual(self._visible(EXEC), {"DA-000003"})

	def test_comms_officer_sees_nothing(self):
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

	def test_create_needs_user(self):
		with self.assertRaises(frappe.ValidationError):
			with as_user(DA1):
				tasks.create_task(title="mine")
		with as_user(DA1):
			out = tasks.create_task(title="peer", user=DA2)
		self.assertEqual(out["da_id"], "DA-000002")
		self.assertEqual(out["user"], DA2)

	def test_complete_respects_scope(self):
		with as_user(DA1):
			tasks.complete_task(self.t1["name"])
		self.assertEqual(frappe.db.get_value("DA Task", self.t1["name"], "status"), "Done")
		with as_user(DA1):
			tasks.complete_task(self.t3["name"])
		with self.assertRaises(frappe.PermissionError):
			with as_user(DA1):
				tasks.complete_task(self.t4["name"])
		with self.assertRaises(frappe.PermissionError):
			with as_user(SUP):
				tasks.complete_task(self.t4["name"])

	def test_list_filters_and_scope(self):
		frappe.set_user(DA1)
		with as_user(DA1):
			rows, total = tasks.list_tasks(due="today")
		self.assertEqual(total, 1)
		self.assertEqual(rows[0]["title"], "Submit weekly crop report")
		with as_user(DA1):
			rows, total = tasks.list_tasks(status="Open")
		self.assertEqual(total, 3)
		with as_user(DA1):
			rows, total = tasks.list_tasks(status="Open", da_id="DA-000001")
		self.assertEqual(total, 2)
		frappe.set_user(SUP)
		with as_user(SUP):
			_rows, total = tasks.list_tasks()
		self.assertEqual(total, 3)

	def test_dashboard_counts(self):
		counts = tasks.dashboard_counts("DA-000001", add_days(today(), -30), today())
		self.assertEqual(counts, {"open": 2, "done": 0, "urgent": 1, "due_today": 1})
		with as_user("Administrator"):
			tasks.complete_task(self.t1["name"])
		counts = tasks.dashboard_counts("DA-000001", add_days(today(), -30), add_days(today(), 1))
		self.assertEqual((counts["open"], counts["done"], counts["due_today"]), (1, 1, 0))
		self.assertIsNotNone(now_datetime())
