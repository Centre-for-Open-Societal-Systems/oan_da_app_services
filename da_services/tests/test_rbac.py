"""Roles, DA RBAC Assignment validation, and the scope rule."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from da_services.services import constants as C
from da_services.services import rbac_service
from da_services.tests.fixtures import create_administrative_areas

SUPERVISOR = "test-supervisor@da.local"
ZONE_SUP = "test-zone-sup@da.local"
DA_USER = "test-da@da.local"
EXEC = "test-exec@da.local"
COMMS = "test-comms@da.local"


def _user(email: str) -> str:
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = email.split("@")[0]
		u.send_welcome_email = 0
		u.insert(ignore_permissions=True, ignore_links=True)
	return email


def _grant(user, role, **kw):
	doc = frappe.new_doc("DA RBAC Assignment")
	doc.user = user
	doc.role = role
	doc.effective_from = kw.pop("effective_from", today())
	for k, v in kw.items():
		setattr(doc, k, v)
	# ignore_links=True: Administrative areas must exist for scope validation.
	# Tests call create_administrative_areas() in setUp to provide valid references.
	doc.insert(ignore_permissions=True, ignore_links=True)
	return doc


class TestRoles(IntegrationTestCase):
	def test_all_five_roles_are_seeded(self):
		roles = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN, C.ROLE_COMMS)
		for role in roles:
			self.assertTrue(frappe.db.exists("Role", role), role)

	def test_da_role_has_no_desk_access(self):
		self.assertEqual(frappe.db.get_value("Role", C.ROLE_DA, "desk_access"), 0)


class TestRBACAssignment(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		create_administrative_areas()  # Create admin areas for scope validation
		frappe.db.delete("DA RBAC Assignment")
		for e in (SUPERVISOR, DA_USER, EXEC, COMMS):
			_user(e)

	def test_supervisor_needs_woreda(self):
		with self.assertRaises(frappe.ValidationError):
			_grant(SUPERVISOR, C.ROLE_SUPERVISOR, region_scope="ET04")

	def test_da_needs_da_id(self):
		with self.assertRaises(frappe.ValidationError):
			_grant(DA_USER, C.ROLE_DA)

	def test_woreda_needs_region(self):
		with self.assertRaises(frappe.ValidationError):
			_grant(SUPERVISOR, C.ROLE_SUPERVISOR, woreda_scope="ET04-W01")

	def test_window_cannot_be_inverted(self):
		with self.assertRaises(frappe.ValidationError):
			_grant(
				SUPERVISOR,
				C.ROLE_SUPERVISOR,
				region_scope="ET04",
				woreda_scope="ET04-W01",
				effective_to=add_days(today(), -1),
			)

	def test_grant_attaches_role_to_user_and_records_assigner(self):
		doc = _grant(SUPERVISOR, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		self.assertEqual(doc.assigned_by, "Administrator")
		self.assertIn(C.ROLE_SUPERVISOR, frappe.get_roles(SUPERVISOR))

	def test_list_page_is_clamped(self):
		self.assertEqual(rbac_service.clamp_page(999999, -5), (rbac_service.MAX_PAGE_LENGTH, 0))
		self.assertEqual(rbac_service.clamp_page(0, 10), (1, 10))
		self.assertEqual(rbac_service.clamp_page("20", "0"), (20, 0))
		# An oversized page is served capped, not rejected.
		_grant(SUPERVISOR, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		self.assertTrue(rbac_service.list_assignments(limit=999999))


class TestScopeRule(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		create_administrative_areas()  # Create admin areas for scope validation
		frappe.db.delete("DA RBAC Assignment")
		for e in (SUPERVISOR, ZONE_SUP, DA_USER, EXEC, COMMS):
			_user(e)
		_grant(SUPERVISOR, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(ZONE_SUP, C.ROLE_EXECUTIVE, region_scope="ET04", zone_scope="ET04-Z01")
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope="ET03")
		_grant(DA_USER, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(COMMS, C.ROLE_COMMS)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_non_admin_lists_assignments_within_their_user_permissions(self):
		# The Supervisor's User Permissions (Region ET04, Woreda ET04-W01) filter the scope
		# Links: assignments in their Woreda are listed, another Region's are not.
		frappe.set_user(SUPERVISOR)
		users = {r.user for r in frappe.get_list("DA RBAC Assignment", fields=["user"])}
		self.assertIn(SUPERVISOR, users)
		self.assertIn(DA_USER, users)
		self.assertNotIn(EXEC, users)
