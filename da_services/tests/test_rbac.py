"""Roles, DA RBAC Assignment validation, and the scope rule."""

import frappe
from frappe import _
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from da_services.services import constants as C
from da_services.services import rbac_service
from da_services.tests.fixtures import bind_da_user, cleanup_test_rows, resolve_test_geo
from da_services.utils import permissions
from da_services.utils.permissions import get_scope_context, require_da_access

SUPERVISOR = "test-supervisor@da.local"
ZONE_SUP = "test-zone-sup@da.local"
DA_USER = "test-da@da.local"
EXEC = "test-exec@da.local"
COMMS = "test-comms@da.local"

# Rows this module creates; setUp / tearDown delete only these.
USERS = (SUPERVISOR, ZONE_SUP, DA_USER, EXEC, COMMS)

# Grants are Links to the synced geography masters (and become User Permissions on save),
# so granted codes come from ``resolve_test_geo``. The codes below are only compared in
# memory by scope_decision and need no master row.
UNGRANTED_WOREDA = "W-NOT-GRANTED"
UNGRANTED_ZONE = "Z-NOT-GRANTED"
UNGRANTED_REGION_WOREDA = "W-OTHER-REGION"


def _other_region(region: str) -> str:
	"""Another active Region from the masters, for the out-of-Region Executive grant."""
	rows = frappe.get_all(
		"DA Region",
		filters={"is_active": 1, "name": ("!=", region)},
		pluck="name",
		order_by="name asc",
		limit=1,
	)
	if not rows:
		frappe.throw(
			_("The geography masters have no second active Region besides {0}.").format(region),
			frappe.ValidationError,
		)
	return rows[0]


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
	# ignore_links=True keeps the explicit da_id (the Desk form fetches it from the User);
	# the scope codes themselves come from the synced masters via resolve_test_geo.
	doc.insert(ignore_permissions=True, ignore_links=True)
	return doc


class TestRoles(IntegrationTestCase):
	def test_all_five_roles_are_seeded(self):
		for role in C.DA_SERVICES_ROLES:
			self.assertTrue(frappe.db.exists("Role", role), role)

	def test_da_role_has_no_desk_access(self):
		self.assertEqual(frappe.db.get_value("Role", C.ROLE_DA, "desk_access"), 0)


class TestRBACAssignment(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		cleanup_test_rows(users=USERS)
		self.geo = resolve_test_geo()
		for e in (SUPERVISOR, DA_USER, EXEC, COMMS):
			_user(e)

	def tearDown(self):
		frappe.set_user("Administrator")
		cleanup_test_rows(users=USERS)

	def test_supervisor_needs_woreda(self):
		with self.assertRaises(frappe.ValidationError):
			_grant(SUPERVISOR, C.ROLE_SUPERVISOR, region_scope=self.geo["region"])

	def test_da_needs_da_id(self):
		with self.assertRaises(frappe.ValidationError):
			_grant(DA_USER, C.ROLE_DA)

	def test_woreda_needs_region(self):
		with self.assertRaises(frappe.ValidationError):
			_grant(SUPERVISOR, C.ROLE_SUPERVISOR, woreda_scope=self.geo["woreda"])

	def test_window_cannot_be_inverted(self):
		with self.assertRaises(frappe.ValidationError):
			_grant(
				SUPERVISOR,
				C.ROLE_SUPERVISOR,
				region_scope=self.geo["region"],
				woreda_scope=self.geo["woreda"],
				effective_to=add_days(today(), -1),
			)

	def test_non_da_services_role_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			_grant(SUPERVISOR, "System Manager")

	def test_grant_attaches_role_to_user_and_records_assigner(self):
		doc = _grant(
			SUPERVISOR, C.ROLE_SUPERVISOR, region_scope=self.geo["region"], woreda_scope=self.geo["woreda"]
		)
		self.assertEqual(doc.assigned_by, "Administrator")
		self.assertIn(C.ROLE_SUPERVISOR, frappe.get_roles(SUPERVISOR))

	def test_list_page_is_clamped(self):
		self.assertEqual(rbac_service.clamp_page(999999, -5), (rbac_service.MAX_PAGE_LENGTH, 0))
		self.assertEqual(rbac_service.clamp_page(0, 10), (1, 10))
		self.assertEqual(rbac_service.clamp_page("20", "0"), (20, 0))
		# An oversized page is served capped, not rejected.
		_grant(
			SUPERVISOR, C.ROLE_SUPERVISOR, region_scope=self.geo["region"], woreda_scope=self.geo["woreda"]
		)
		self.assertTrue(rbac_service.list_assignments(limit=999999))


class TestScopeRule(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		cleanup_test_rows(users=USERS)
		self.geo = resolve_test_geo()
		self.region, self.zone, self.woreda = self.geo["region"], self.geo["zone"], self.geo["woreda"]
		self.other_region = _other_region(self.region)
		for e in (SUPERVISOR, ZONE_SUP, DA_USER, EXEC, COMMS):
			_user(e)
		bind_da_user(DA_USER, "DA-000001")  # the DA-ID a DA owns is read from User.da_registry_id
		_grant(SUPERVISOR, C.ROLE_SUPERVISOR, region_scope=self.region, woreda_scope=self.woreda)
		_grant(ZONE_SUP, C.ROLE_EXECUTIVE, region_scope=self.region, zone_scope=self.zone)
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope=self.other_region)
		_grant(DA_USER, C.ROLE_DA, da_id="DA-000001", region_scope=self.region, woreda_scope=self.woreda)
		_grant(COMMS, C.ROLE_COMMS)

	def tearDown(self):
		frappe.set_user("Administrator")
		cleanup_test_rows(users=USERS)

	def test_supervisor_covers_own_woreda_only(self):
		ctx = get_scope_context(SUPERVISOR)
		self.assertTrue(ctx.covers(self.region, self.woreda))
		self.assertFalse(ctx.covers(self.region, UNGRANTED_WOREDA))
		self.assertFalse(ctx.covers(self.other_region, UNGRANTED_REGION_WOREDA))

	def test_executive_covers_whole_region(self):
		ctx = get_scope_context(EXEC)
		self.assertTrue(ctx.covers(self.other_region, UNGRANTED_REGION_WOREDA))
		self.assertFalse(ctx.covers(self.region, self.woreda))

	def test_region_only_grant_covers_record_without_zone_or_woreda(self):
		ctx = get_scope_context(EXEC)
		self.assertEqual(ctx.scope_decision(self.other_region, None, None), (True, "region"))
		self.assertEqual(
			ctx.scope_decision(self.other_region, UNGRANTED_REGION_WOREDA, None), (True, "region")
		)

	def test_zone_grant_covers_its_zone_only(self):
		ctx = get_scope_context(ZONE_SUP)
		self.assertEqual(ctx.scope_decision(self.region, self.woreda, self.zone), (True, "zone"))
		self.assertEqual(
			ctx.scope_decision(self.region, UNGRANTED_WOREDA, UNGRANTED_ZONE),
			(False, permissions.OUT_OF_SCOPE),
		)
		self.assertEqual(
			ctx.scope_decision(self.other_region, UNGRANTED_REGION_WOREDA, None),
			(False, permissions.OUT_OF_SCOPE),
		)

	def test_zone_grant_is_not_widened_when_registry_has_no_zone(self):
		# The registry record predates the Zone field: deny, but say why.
		ctx = get_scope_context(ZONE_SUP)
		self.assertEqual(
			ctx.scope_decision(self.region, self.woreda, None), (False, permissions.SCOPE_UNRESOLVED)
		)
		with self.assertRaises(frappe.PermissionError):
			require_da_access("DA-000009", self.region, self.woreda, ctx, zone=None)
		rows = frappe.get_all("DA Audit Event", filters={"entity_name": "DA-000009"}, fields=["reason"])
		self.assertIn(permissions.SCOPE_UNRESOLVED, {r.reason for r in rows})

	def test_woreda_grant_is_not_widened_when_registry_has_no_woreda(self):
		ctx = get_scope_context(SUPERVISOR)
		self.assertEqual(ctx.scope_decision(self.region, None, None), (False, permissions.SCOPE_UNRESOLVED))
		# A known, different Woreda is a plain scope miss, not missing data.
		self.assertEqual(
			ctx.scope_decision(self.region, UNGRANTED_WOREDA, None), (False, permissions.OUT_OF_SCOPE)
		)

	def test_da_sees_only_own_record(self):
		ctx = get_scope_context(DA_USER)
		require_da_access("DA-000001", self.region, self.woreda, ctx)
		with self.assertRaises(frappe.PermissionError):
			require_da_access("DA-000002", self.region, self.woreda, ctx)

	def test_comms_officer_never_reaches_da_identity(self):
		ctx = get_scope_context(COMMS)
		with self.assertRaises(frappe.PermissionError):
			require_da_access("DA-000001", self.region, self.woreda, ctx)

	def test_expired_grant_gives_no_scope(self):
		frappe.db.delete("DA RBAC Assignment", {"user": SUPERVISOR})
		_grant(
			SUPERVISOR,
			C.ROLE_SUPERVISOR,
			region_scope=self.region,
			woreda_scope=self.woreda,
			effective_from=add_days(today(), -30),
			effective_to=add_days(today(), -1),
		)
		ctx = get_scope_context(SUPERVISOR)
		self.assertFalse(ctx.covers(self.region, self.woreda))

	def test_administrator_is_unrestricted(self):
		ctx = get_scope_context("Administrator")
		self.assertTrue(ctx.unrestricted)
		self.assertTrue(ctx.covers("ET99", "ET99-W99"))

	def test_non_admin_sees_only_own_assignments_in_list(self):
		frappe.set_user(SUPERVISOR)
		rows = frappe.get_list("DA RBAC Assignment", fields=["user"])
		self.assertTrue(rows)
		self.assertTrue(all(r.user == SUPERVISOR for r in rows))
