"""FSD Appendix D: verify each role by what it CANNOT do.

One user per role, then every forbidden action in the appendix that exists in the
backend today is attempted and must be refused. Positive checks confirm the same action
is allowed for the role that owns it, so a test cannot pass by denying everyone.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.api.v1 import me as me_api
from da_services.api.v1 import rbac as rbac_api
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import constants as C
from da_services.services.da_lookup import get_da_reference
from da_services.tests.fixtures import (
	aligned_mock_geo,
	bind_da_user,
	cleanup_test_rows,
	require_geo_masters,
)

USERS = {
	C.ROLE_DA: "matrix-da@da.local",
	C.ROLE_SUPERVISOR: "matrix-sup@da.local",
	C.ROLE_EXECUTIVE: "matrix-exec@da.local",
	C.ROLE_ADMIN: "matrix-admin@da.local",
	C.ROLE_COMMS: "matrix-comms@da.local",
}
OTHER_DA = "matrix-other-da@da.local"
ZONE_SUP = "matrix-zone@da.local"  # created inside one test
ZONE_EXEC = "matrix-zone-exec@da.local"  # created inside one test

# Rows this module creates (grants and the audit trail of its denials); setUp / tearDown
# delete only these.
ALL_USERS = (*USERS.values(), OTHER_DA, ZONE_SUP, ZONE_EXEC)


def _user(email):
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
	doc.effective_from = today()
	for k, v in kw.items():
		setattr(doc, k, v)
	doc.insert(ignore_permissions=True, ignore_links=True)
	return doc


class TestRoleMatrix(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		cleanup_test_rows(users=ALL_USERS)
		# Grants follow the mock DA Registry postings, rewritten from the synced masters:
		# DA-000001/2 in the primary Woreda, DA-000003 in another Region.
		require_geo_masters()
		MockDARegistryClient.reset()
		g = aligned_mock_geo()
		self.geo = g
		for e in ALL_USERS:
			_user(e)
		# A DA's own DA-ID is read from User.da_registry_id.
		bind_da_user(USERS[C.ROLE_DA], "DA-000001")
		bind_da_user(OTHER_DA, "DA-000002")
		_grant(
			USERS[C.ROLE_DA], C.ROLE_DA, da_id="DA-000001", region_scope=g["region"], woreda_scope=g["woreda"]
		)
		_grant(OTHER_DA, C.ROLE_DA, da_id="DA-000002", region_scope=g["region"], woreda_scope=g["woreda"])
		_grant(
			USERS[C.ROLE_SUPERVISOR], C.ROLE_SUPERVISOR, region_scope=g["region"], woreda_scope=g["woreda"]
		)
		_grant(USERS[C.ROLE_EXECUTIVE], C.ROLE_EXECUTIVE, region_scope=g["region"])
		_grant(USERS[C.ROLE_ADMIN], C.ROLE_ADMIN)
		_grant(USERS[C.ROLE_COMMS], C.ROLE_COMMS)
		# The API layer rolls the transaction back on a denial, which would erase these
		# grants mid-test. Commit them; setUp clears the table again next time.
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		frappe.response.pop("http_status_code", None)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.response.pop("http_status_code", None)
		# Grants and the audit rows these users produced; the audit table has no delete
		# permission but SQL cleanup in tests is fine.
		cleanup_test_rows(users=ALL_USERS)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def _as(self, role):
		frappe.set_user(USERS[role])
		frappe.response.pop("http_status_code", None)

	def _denied(self, out):
		self.assertEqual(out["status"], "error", out)
		self.assertEqual(frappe.response.get("http_status_code"), 403, out)
		frappe.response.pop("http_status_code", None)

	# --- Development Agent -------------------------------------------------------

	def test_da_cannot_manage_rbac_or_see_other_das(self):
		self._as(C.ROLE_DA)
		self._denied(rbac_api.list_assignments())
		self._denied(rbac_api.create_assignment(user=OTHER_DA, role=C.ROLE_DA, da_id="DA-000009"))
		with self.assertRaises(frappe.PermissionError):
			get_da_reference("DA-000002")  # same Woreda, different DA
		self.assertFalse(frappe.has_permission("DA RBAC Assignment", "create", user=USERS[C.ROLE_DA]))
		self.assertFalse(frappe.has_permission("DA Audit Event", "read", user=USERS[C.ROLE_DA]))
		# and the positive side
		self.assertEqual(get_da_reference("DA-000001").da_id, "DA-000001")

	# --- Supervisor --------------------------------------------------------------

	def test_supervisor_cannot_act_outside_own_woreda_or_administer(self):
		self._as(C.ROLE_SUPERVISOR)
		with self.assertRaises(frappe.PermissionError):
			get_da_reference("DA-000003")  # other Region's Woreda
		self._denied(rbac_api.list_assignments())
		self._denied(
			rbac_api.create_assignment(
				user=OTHER_DA,
				role=C.ROLE_SUPERVISOR,
				region_scope=self.geo["region"],
				woreda_scope=self.geo["woreda"],
			)
		)
		self.assertFalse(frappe.has_permission("DA RBAC Assignment", "create", user=USERS[C.ROLE_SUPERVISOR]))
		self.assertFalse(frappe.has_permission("DA RBAC Assignment", "write", user=USERS[C.ROLE_SUPERVISOR]))
		self.assertEqual(get_da_reference("DA-000001").woreda, self.geo["woreda"])

	# --- Executive ---------------------------------------------------------------

	def test_executive_is_read_only_within_region(self):
		self._as(C.ROLE_EXECUTIVE)
		self.assertEqual(get_da_reference("DA-000001").region, self.geo["region"])  # may read inside Region
		with self.assertRaises(frappe.PermissionError):
			get_da_reference("DA-000003")  # other Region
		self._denied(rbac_api.create_assignment(user=OTHER_DA, role=C.ROLE_DA, da_id="DA-000009"))
		self._denied(rbac_api.list_assignments())
		self.assertFalse(frappe.has_permission("DA RBAC Assignment", "write", user=USERS[C.ROLE_EXECUTIVE]))
		self.assertNotIn("/approvals", me_api.get_me()["data"]["menu"])

	# --- Communications Officer --------------------------------------------------

	def test_comms_officer_never_touches_da_identity_or_approvals(self):
		self._as(C.ROLE_COMMS)
		with self.assertRaises(frappe.PermissionError):
			get_da_reference("DA-000001")
		self._denied(rbac_api.list_assignments())
		self.assertFalse(frappe.has_permission("DA RBAC Assignment", "read", user=USERS[C.ROLE_COMMS]))
		menu = me_api.get_me()["data"]["menu"]
		for forbidden in ("/agents", "/approvals", "/assignments", "/farmers"):
			self.assertNotIn(forbidden, menu)
		self.assertIn("/broadcast", menu)

	# --- Administrator -----------------------------------------------------------

	def test_admin_can_administer_but_cannot_delete_audit(self):
		self._as(C.ROLE_ADMIN)
		out = rbac_api.create_assignment(
			user=OTHER_DA, role=C.ROLE_EXECUTIVE, region_scope=self.geo["other_region"]
		)
		self.assertEqual(out["status"], "success", out)
		self.assertEqual(get_da_reference("DA-000003").da_id, "DA-000003")
		self.assertFalse(frappe.has_permission("DA Audit Event", "delete", user=USERS[C.ROLE_ADMIN]))
		self.assertFalse(frappe.has_permission("DA Audit Event", "write", user=USERS[C.ROLE_ADMIN]))

	# --- Guest -------------------------------------------------------------------

	def test_guest_is_denied_everything(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			get_da_reference("DA-000001")
		self._denied(me_api.get_me())
		self._denied(rbac_api.list_assignments())

	# --- Zone scope --------------------------------------------------------------

	def test_zone_grant_covers_its_woredas_only(self):
		frappe.set_user("Administrator")
		g = self.geo
		zone_user = _user(ZONE_SUP)
		_grant(
			zone_user,
			C.ROLE_SUPERVISOR,
			region_scope=g["region"],
			zone_scope=g["zone"],
			woreda_scope=g["woreda"],
		)
		# a second grant with only Region + Zone (no Woreda) is allowed for non-Supervisor roles
		exec_user = _user(ZONE_EXEC)
		_grant(exec_user, C.ROLE_EXECUTIVE, region_scope=g["region"], zone_scope=g["zone"])
		frappe.set_user(exec_user)
		self.assertEqual(get_da_reference("DA-000001").zone, g["zone"])
		with self.assertRaises(frappe.PermissionError):
			get_da_reference("DA-000003")  # other Region's Zone
		frappe.set_user("Administrator")
		# a Zone without its Region is refused
		with self.assertRaises(frappe.ValidationError):
			_grant(exec_user, C.ROLE_COMMS, zone_scope=g["zone"])
