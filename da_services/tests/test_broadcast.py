"""Broadcast notifications: Communications Officer -> DAs in a scope (T2)."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.api.v1 import notifications as api
from da_services.services import constants as C
from da_services.services import notifications as svc
from da_services.tests.fixtures import aligned_mock_geo, bind_da_user, require_geo_masters
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


SENDERS = (COMMS_NATIONAL, COMMS_W01, SUP, "Administrator")


class TestBroadcast(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA Notification", {"user": ("in", ALL_USERS)})
		frappe.db.delete("DA Broadcast", {"sent_by": ("in", SENDERS)})
		frappe.db.delete("DA RBAC Assignment", {"user": ("in", ALL_USERS)})
		# "all" broadcasts count every active DA grant on the site; pause foreign ones
		# so this module's recipient counts stay deterministic across the suite.
		self._paused_da_grants = frappe.get_all(
			"DA RBAC Assignment",
			filters={"role": C.ROLE_DA, "active": 1, "user": ("not in", ALL_USERS)},
			pluck="name",
		)
		for name in self._paused_da_grants:
			frappe.db.set_value("DA RBAC Assignment", name, "active", 0, update_modified=False)
		_user(DA_W01, [C.ROLE_DA])
		_user(DA_W02, [C.ROLE_DA])
		_user(DA_ET03, [C.ROLE_DA])
		_user(COMMS_NATIONAL, [C.ROLE_COMMS])
		_user(COMMS_W01, [C.ROLE_COMMS])
		_user(SUP, [C.ROLE_SUPERVISOR])
		require_geo_masters()
		g = aligned_mock_geo()
		# Second Woreda under the same Region (for region vs woreda broadcast cases).
		zones = frappe.get_all("DA Zone", filters={"is_active": 1, "region": g["region"]}, pluck="name")
		sibling = frappe.get_all(
			"DA Woreda",
			filters={"is_active": 1, "zone": ("in", zones), "name": ("!=", g["woreda"])},
			pluck="name",
			order_by="name asc",
			limit=1,
		)
		self.woreda_b = sibling[0] if sibling else g["other_woreda"]
		self.geo = g
		_grant(DA_W01, C.ROLE_DA, da_id="DA-BC0001", region_scope=g["region"], woreda_scope=g["woreda"])
		_grant(DA_W02, C.ROLE_DA, da_id="DA-BC0002", region_scope=g["region"], woreda_scope=self.woreda_b)
		_grant(
			DA_ET03,
			C.ROLE_DA,
			da_id="DA-BC0003",
			region_scope=g["other_region"],
			woreda_scope=g["other_woreda"],
		)
		_grant(COMMS_NATIONAL, C.ROLE_COMMS)
		_grant(COMMS_W01, C.ROLE_COMMS, region_scope=g["region"], woreda_scope=g["woreda"])
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope=g["region"], woreda_scope=g["woreda"])
		bind_da_user(DA_W01, "DA-BC0001")
		bind_da_user(DA_W02, "DA-BC0002")
		bind_da_user(DA_ET03, "DA-BC0003")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA Notification", {"user": ("in", ALL_USERS)})
		frappe.db.delete("DA Broadcast", {"sent_by": ("in", SENDERS)})
		frappe.db.delete("DA RBAC Assignment", {"user": ("in", ALL_USERS)})
		for name in getattr(self, "_paused_da_grants", []):
			if frappe.db.exists("DA RBAC Assignment", name):
				frappe.db.set_value("DA RBAC Assignment", name, "active", 1, update_modified=False)

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
		out = svc.broadcast(ctx, title="W01 meeting", scope="woreda", scope_value=self.geo["woreda"])
		self.assertEqual(out["recipients"], 1)
		self.assertEqual(len(self._rows(DA_W01)), 1)
		self.assertEqual(self._rows(DA_W02), [])
		self.assertEqual(self._rows(DA_ET03), [])

	def test_region_scope(self):
		ctx = get_scope_context(COMMS_NATIONAL)
		out = svc.broadcast(ctx, title="region notice", scope="region", scope_value=self.geo["region"])
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
		out = svc.broadcast(ctx, title="Active only", scope="region", scope_value=self.geo["region"])
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
			svc.broadcast(ctx, title="x", scope="woreda", scope_value=self.woreda_b)

	def test_scoped_comms_can_target_own_woreda(self):
		ctx = get_scope_context(COMMS_W01)
		out = svc.broadcast(ctx, title="own woreda", scope="woreda", scope_value=self.geo["woreda"])
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
		# One record, guarded by the unique client_op_id (the database-level check)
		self.assertEqual(frappe.db.count("DA Broadcast", {"client_op_id": "bc-op-0001"}), 1)

	def test_broadcast_record_written(self):
		ctx = get_scope_context(COMMS_NATIONAL)
		out = svc.broadcast(
			ctx,
			title="recorded",
			body="b",
			href="/notifications",
			scope="region",
			scope_value=self.geo["region"],
		)
		row = frappe.db.get_value(
			"DA Broadcast",
			{"client_op_id": ("is", "not set"), "title": "recorded"},
			["name", "sent_by", "scope", "scope_value", "recipients", "href"],
			as_dict=True,
		)
		self.assertEqual(out["broadcast_id"], row.name)  # no client_op_id: the record name is the id
		self.assertEqual(row.sent_by, COMMS_NATIONAL)
		self.assertEqual(
			(row.scope, row.scope_value, row.recipients, row.href),
			("region", self.geo["region"], 2, "/notifications"),
		)

	def test_failed_send_leaves_nothing_behind(self):
		"""Atomic: if sending stops half-way, neither the record nor partial notifications remain."""
		ctx = get_scope_context(COMMS_NATIONAL)
		real_notify = svc.notify
		calls = []

		def flaky(user, **kw):
			calls.append(user)
			if len(calls) == 2:
				raise RuntimeError("boom")
			return real_notify(user, **kw)

		with patch.object(svc, "notify", side_effect=flaky), self.assertRaises(RuntimeError):
			svc.broadcast(ctx, title="partial", scope="all", client_op_id="bc-op-atomic")
		self.assertEqual(frappe.db.count("DA Broadcast", {"client_op_id": "bc-op-atomic"}), 0)
		for u in (DA_W01, DA_W02, DA_ET03):
			self.assertEqual(self._rows(u), [])

	# -- validation + audit ---------------------------------------------------

	def test_validation(self):
		ctx = get_scope_context(COMMS_NATIONAL)
		with self.assertRaises(frappe.ValidationError):
			svc.broadcast(ctx, title="", scope="all")
		with self.assertRaises(frappe.ValidationError):
			svc.broadcast(ctx, title="x", scope="woreda")  # scope_value missing
		with self.assertRaises(frappe.ValidationError):
			svc.broadcast(ctx, title="x", scope="planet")

	def test_href_must_be_a_portal_path(self):
		ctx = get_scope_context(COMMS_NATIONAL)
		for bad in ("https://evil.example/x", "//evil.example", "notifications", "/a b"):
			with self.assertRaises(frappe.ValidationError, msg=bad):
				svc.broadcast(ctx, title="x", scope="all", href=bad)
		out = svc.broadcast(ctx, title="ok", scope="all", href=" /notifications ")
		self.assertEqual(out["recipients"], 3)

	def test_scope_with_no_active_da_is_rejected(self):
		"""A typo in scope_value must not silently send to nobody."""
		ctx = get_scope_context(COMMS_NATIONAL)
		with self.assertRaises(frappe.ValidationError):
			svc.broadcast(ctx, title="x", scope="woreda", scope_value="ET99-W99")
		self.assertEqual(frappe.db.count("DA Broadcast", {"scope_value": "ET99-W99"}), 0)

	def test_audit_event_written(self):
		ctx = get_scope_context(COMMS_NATIONAL)
		out = svc.broadcast(ctx, title="audited", scope="woreda", scope_value=self.geo["woreda"])
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
