"""Audit log: denials and RBAC changes are recorded; rows are immutable."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.services import constants as C
from da_services.services.da_lookup import get_da_reference
from da_services.utils import audit

SUP = "test-sup-audit@da.local"
NO_ROLE = "test-norole-audit@da.local"  # never granted anything


def _user(email):
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = "Audit Sup"
		u.send_welcome_email = 0
		u.insert(ignore_permissions=True, ignore_links=True)
	return email


def _events(**filters):
	return frappe.get_all(
		"DA Audit Event",
		filters=filters,
		fields=["name", "action", "decision", "entity_name", "reason", "details", "user"],
		order_by="creation desc",
	)


class TestAudit(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA RBAC Assignment")
		# The table is append-only (no delete permission, on_trash refuses), so test cleanup
		# goes through SQL; same pattern as test_role_matrix.tearDown.
		frappe.db.sql("delete from `tabDA Audit Event`")
		_user(SUP)
		_user(NO_ROLE)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_assignment_lifecycle_is_audited(self):
		doc = frappe.get_doc(
			{
				"doctype": "DA RBAC Assignment",
				"user": SUP,
				"role": C.ROLE_SUPERVISOR,
				"region_scope": "ET04",
				"woreda_scope": "ET04-W01",
				"effective_from": today(),
			}
		).insert()
		created = _events(action=audit.RBAC_CREATED, entity_name=doc.name)
		self.assertEqual(len(created), 1)
		self.assertEqual(created[0].user, "Administrator")

		doc.woreda_scope = "ET04-W02"
		doc.save()
		updated = _events(action=audit.RBAC_UPDATED, entity_name=doc.name)
		self.assertEqual(len(updated), 1)
		self.assertIn("ET04-W02", updated[0].details)

		doc.active = 0
		doc.save()
		self.assertEqual(len(_events(action=audit.RBAC_REVOKED, entity_name=doc.name)), 1)

	def test_denial_is_audited_with_reason(self):
		frappe.get_doc(
			{
				"doctype": "DA RBAC Assignment",
				"user": SUP,
				"role": C.ROLE_SUPERVISOR,
				"region_scope": "ET04",
				"woreda_scope": "ET04-W01",
				"effective_from": today(),
			}
		).insert()
		frappe.set_user(SUP)
		with self.assertRaises(frappe.PermissionError):
			get_da_reference("DA-000003")  # ET03-W02, outside scope
		frappe.set_user("Administrator")
		denied = _events(action=audit.ACCESS_DENIED, decision="denied", entity_name="DA-000003")
		self.assertEqual(len(denied), 1)
		self.assertEqual(denied[0].user, SUP)
		self.assertEqual(denied[0].reason, "out of scope")

	def test_missing_role_denial_is_audited(self):
		frappe.set_user(NO_ROLE)
		with self.assertRaises(frappe.PermissionError):
			get_da_reference("DA-000001")
		frappe.set_user("Administrator")
		denied = _events(action=audit.ACCESS_DENIED, decision="denied")
		self.assertEqual(denied[0].reason, "missing role")

	def test_audit_rows_cannot_be_edited_or_deleted(self):
		name = audit.record("test.event", entity_type="Test", entity_name="x")
		doc = frappe.get_doc("DA Audit Event", name)
		doc.reason = "tampered"
		with self.assertRaises(frappe.PermissionError):
			doc.save(ignore_permissions=True)
		with self.assertRaises(frappe.PermissionError):
			frappe.delete_doc("DA Audit Event", name, ignore_permissions=True)
		self.assertTrue(frappe.db.exists("DA Audit Event", name))

	def test_no_role_may_create_audit_rows_through_permissions(self):
		for role in (C.ROLE_ADMIN, "System Manager", C.ROLE_SUPERVISOR):
			perms = frappe.get_meta("DA Audit Event").get("permissions", {"role": role})
			for p in perms:
				self.assertFalse(p.create or p.write or p.delete, role)

	def test_denial_inside_a_request_is_deferred_and_flushed(self):
		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		old = getattr(frappe.local, "request", None)
		frappe.local.request = Request(EnvironBuilder(path="/api/v1/da-services/me").get_environ())
		try:
			frappe.set_user(SUP)
			with self.assertRaises(frappe.PermissionError):
				get_da_reference("DA-000001")
			frappe.set_user("Administrator")
			self.assertEqual(_events(action=audit.ACCESS_DENIED), [])  # queued, not written
			self.assertEqual(len(frappe.local.da_audit_pending), 1)
			audit.flush()
			self.assertEqual(len(_events(action=audit.ACCESS_DENIED)), 1)
			self.assertEqual(_events(action=audit.ACCESS_DENIED)[0].get("details") is not None, True)
		finally:
			frappe.local.request = old
