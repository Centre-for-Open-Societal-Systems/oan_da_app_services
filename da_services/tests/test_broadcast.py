"""Broadcast notifications: Communications Officer -> DAs in a scope (T2)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.api.v1 import notifications as api
from da_services.services import constants as C
from da_services.services import notifications as svc
from da_services.utils.permissions import get_scope_context

DA_W01 = "bc-da-w01@da.local"  # ET04 / ET04-W01
DA_W02 = "bc-da-w02@da.local"  # ET04 / ET04-W02
DA_ET03 = "bc-da-et03@da.local"  # ET03 / ET03-W01
COMMS_NATIONAL = "bc-comms@da.local"  # no scope on grant
COMMS_W01 = "bc-comms-w01@da.local"  # woreda ET04-W01 only
SUP = "bc-sup@da.local"

ALL_USERS = (DA_W01, DA_W02, DA_ET03, COMMS_NATIONAL, COMMS_W01, SUP)


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


class TestBroadcast(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA Notification", {"user": ("in", ALL_USERS)})
		frappe.db.delete("DA RBAC Assignment", {"user": ("in", ALL_USERS)})
		_user(DA_W01, [C.ROLE_DA])
		_user(DA_W02, [C.ROLE_DA])
		_user(DA_ET03, [C.ROLE_DA])
		_user(COMMS_NATIONAL, [C.ROLE_COMMS])
		_user(COMMS_W01, [C.ROLE_COMMS])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_grant(DA_W01, C.ROLE_DA, da_id="DA-BC0001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA_W02, C.ROLE_DA, da_id="DA-BC0002", region_scope="ET04", woreda_scope="ET04-W02")
		_grant(DA_ET03, C.ROLE_DA, da_id="DA-BC0003", region_scope="ET03", woreda_scope="ET03-W01")
		_grant(COMMS_NATIONAL, C.ROLE_COMMS)
		_grant(COMMS_W01, C.ROLE_COMMS, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA Notification", {"user": ("in", ALL_USERS)})
		frappe.db.delete("DA RBAC Assignment", {"user": ("in", ALL_USERS)})

	def _rows(self, user):
		return frappe.get_all(
			"DA Notification", filters={"user": user, "kind": "Broadcast"}, fields=["title", "event", "read"]
		)

	# -- scope resolution ---------------------------------------------------

	def test_national_comms_broadcast_all(self):
		ctx = get_scope_context(COMMS_NATIONAL)
		out = svc.broadcast(
			ctx, title="Fertiliser vouchers", body="Collect at the Woreda office.", scope="all"
		)
		self.assertEqual(out["recipients"], 3)
		self.assertFalse(out["duplicate"])
		for u in (DA_W01, DA_W02, DA_ET03):
			rows = self._rows(u)
			self.assertEqual(len(rows), 1)
			self.assertEqual(rows[0].title, "Fertiliser vouchers")
			self.assertEqual(rows[0].event, f"broadcast:{out['broadcast_id']}")
			self.assertFalse(rows[0].read)
		self.assertEqual(self._rows(SUP), [])  # only DAs receive broadcasts

	def test_woreda_scope_reaches_only_that_woreda(self):
		ctx = get_scope_context(COMMS_NATIONAL)
		out = svc.broadcast(ctx, title="W01 meeting", scope="woreda", scope_value="ET04-W01")
		self.assertEqual(out["recipients"], 1)
		self.assertEqual(len(self._rows(DA_W01)), 1)
		self.assertEqual(self._rows(DA_W02), [])
		self.assertEqual(self._rows(DA_ET03), [])

	def test_region_scope(self):
		ctx = get_scope_context(COMMS_NATIONAL)
		out = svc.broadcast(ctx, title="ET04 notice", scope="region", scope_value="ET04")
		self.assertEqual(out["recipients"], 2)
		self.assertEqual(self._rows(DA_ET03), [])

	def test_expired_grant_is_not_a_recipient(self):
		frappe.db.set_value(
			"DA RBAC Assignment",
			{"user": DA_W02, "role": C.ROLE_DA},
			"effective_to",
			"2020-01-01",
			update_modified=False,
		)
		ctx = get_scope_context(COMMS_NATIONAL)
		out = svc.broadcast(ctx, title="Active only", scope="region", scope_value="ET04")
		self.assertEqual(out["recipients"], 1)
		self.assertEqual(self._rows(DA_W02), [])

	# -- sender scope rule (deny, never widen) -------------------------------

	def test_scoped_comms_cannot_broadcast_all(self):
		ctx = get_scope_context(COMMS_W01)
		with self.assertRaises(frappe.PermissionError):
			svc.broadcast(ctx, title="x", scope="all")

	def test_scoped_comms_cannot_target_other_woreda(self):
		ctx = get_scope_context(COMMS_W01)
		with self.assertRaises(frappe.PermissionError):
			svc.broadcast(ctx, title="x", scope="woreda", scope_value="ET04-W02")

	def test_scoped_comms_can_target_own_woreda(self):
		ctx = get_scope_context(COMMS_W01)
		out = svc.broadcast(ctx, title="own woreda", scope="woreda", scope_value="ET04-W01")
		self.assertEqual(out["recipients"], 1)

	def test_admin_is_unrestricted(self):
		ctx = get_scope_context("Administrator")
		out = svc.broadcast(ctx, title="admin all", scope="all")
		self.assertEqual(out["recipients"], 3)

	# -- idempotency ----------------------------------------------------------

	def test_same_client_op_id_does_not_resend(self):
		ctx = get_scope_context(COMMS_NATIONAL)
		first = svc.broadcast(ctx, title="once", scope="all", client_op_id="bc-op-0001")
		second = svc.broadcast(ctx, title="once", scope="all", client_op_id="bc-op-0001")
		self.assertFalse(first["duplicate"])
		self.assertTrue(second["duplicate"])
		self.assertEqual(first["broadcast_id"], "bc-op-0001")
		self.assertEqual(second["recipients"], 3)
		self.assertEqual(len(self._rows(DA_W01)), 1)

	# -- validation + audit ---------------------------------------------------

	def test_validation(self):
		ctx = get_scope_context(COMMS_NATIONAL)
		with self.assertRaises(frappe.ValidationError):
			svc.broadcast(ctx, title="", scope="all")
		with self.assertRaises(frappe.ValidationError):
			svc.broadcast(ctx, title="x", scope="woreda")  # scope_value missing
		with self.assertRaises(frappe.ValidationError):
			svc.broadcast(ctx, title="x", scope="planet")

	def test_audit_event_written(self):
		ctx = get_scope_context(COMMS_NATIONAL)
		out = svc.broadcast(ctx, title="audited", scope="woreda", scope_value="ET04-W01")
		rows = frappe.get_all(
			"DA Audit Event",
			filters={"action": svc.BROADCAST_SENT, "entity_name": out["broadcast_id"]},
			fields=["user", "decision", "details"],
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].user, COMMS_NATIONAL)
		self.assertEqual(rows[0].decision, "allowed")
		self.assertIn('"recipients": 1', rows[0].details)

	# -- API ------------------------------------------------------------------

	def test_api_requires_comms_or_admin(self):
		frappe.set_user(DA_W01)
		frappe.form_dict.update({"title": "nope", "scope": "all"})
		out = api.broadcast()
		self.assertEqual(out["status"], "error")
		self.assertEqual(frappe.response.get("http_status_code"), 403)

	def test_api_sends_and_shows_in_bell(self):
		frappe.set_user(COMMS_NATIONAL)
		frappe.form_dict.update({"title": "Bell check", "body": "hello", "scope": "all"})
		out = api.broadcast()
		self.assertEqual(out["data"]["recipients"], 3)

		frappe.set_user(DA_W01)
		frappe.form_dict.clear()
		listed = api.list_notifications(unread="1", limit=10)
		titles = [r["title"] for r in listed["data"]]
		self.assertIn("Bell check", titles)
		self.assertGreaterEqual(listed["meta"]["unread_count"], 1)
