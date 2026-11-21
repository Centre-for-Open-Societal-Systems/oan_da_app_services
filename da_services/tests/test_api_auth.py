"""Auth wiring: routes, JWT middleware, /me profile and the RBAC admin API."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today
from oan_auth_service.api import tokens
from oan_auth_service.api.middleware import validate_jwt_request
from oan_auth_service.tests.utils import configured_keys
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from da_services.api import router
from da_services.api.v1 import me as me_api
from da_services.api.v1 import profile as profile_api
from da_services.api.v1 import rbac as rbac_api
from da_services.services import constants as C
from da_services.tests.fixtures import ensure_geo_masters

ADMIN = "test-oan-admin@da.local"
SUP = "test-sup-auth@da.local"
DA_USER = "test-da-auth@da.local"
PLAIN = "test-plain-auth@da.local"  # a user with no DA Services assignment


def _user(email: str) -> str:
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = email.split("@")[0]
		u.send_welcome_email = 0
		u.enabled = 1
		u.insert(ignore_permissions=True, ignore_links=True)
	return email


def _grant(user, role, **kw):
	doc = frappe.new_doc("DA RBAC Assignment")
	doc.user = user
	doc.role = role
	doc.effective_from = kw.pop("effective_from", today())
	for k, v in kw.items():
		setattr(doc, k, v)
	doc.insert(ignore_permissions=True, ignore_links=True)
	return doc


def _request(path: str, token: str | None) -> Request:
	headers = {"Authorization": f"Bearer {token}"} if token else {}
	return Request(EnvironBuilder(path=path, method="GET", headers=headers).get_environ())


class _Base(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		# The RBAC API validates its scope links; do not rely on another module having made them.
		ensure_geo_masters()
		frappe.db.delete("DA RBAC Assignment")
		for e in (ADMIN, SUP, DA_USER, PLAIN):
			_user(e)
		_grant(ADMIN, C.ROLE_ADMIN)
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA_USER, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		frappe.response.pop("http_status_code", None)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.response.pop("http_status_code", None)


class TestRoutes(_Base):
	def test_routes_are_registered_under_our_prefix(self):
		router.ensure_routes_registered()
		import frappe.api

		paths = {r.rule for r in frappe.api.API_URL_MAP._rules}
		for expected in (
			"/api/v1/da-services/health",
			"/api/v1/da-services/me",
			"/api/v1/da-services/agents/<da_id>",
			"/api/v1/da-services/rbac-assignments",
			"/api/v1/da-services/rbac-assignments/<assignment_id>",
			"/api/v1/da-services/rbac-assignments/<assignment_id>/revoke",
		):
			self.assertIn(expected, paths)
		# The auth app's own routes are there too, so login works on this site.
		self.assertIn("/api/v1/auth/login", paths)
		self.assertIn("/api/v1/auth/me", paths)
		self.assertNotIn("/api/v1/me", paths)  # develop moved it under /auth

	def test_health_is_guest_accessible(self):
		frappe.set_user("Guest")
		out = router.get_health()
		self.assertEqual(out["status"], "success")
		self.assertEqual(out["data"]["service"], "da_services")


class TestJwtMiddleware(_Base):
	def _run(self, path, token):
		old_request = getattr(frappe.local, "request", None)
		frappe.set_user("Guest")
		frappe.local.request = _request(path, token)
		try:
			validate_jwt_request(frappe.local.request)
			return frappe.session.user
		finally:
			frappe.local.request = old_request

	def test_valid_token_sets_the_session_user_on_our_namespace(self):
		router.ensure_routes_registered()
		with configured_keys():
			token, _ttl = tokens.issue_access_token(SUP, tokens.resolve_roles(SUP))
			self.assertEqual(self._run("/api/v1/da-services/me", token), SUP)

	def test_missing_token_is_rejected_on_our_namespace(self):
		router.ensure_routes_registered()
		with configured_keys(), self.assertRaises(Exception):
			self._run("/api/v1/da-services/me", None)

	def test_rpc_form_of_our_methods_is_protected_too(self):
		router.ensure_routes_registered()
		with configured_keys(), self.assertRaises(Exception):
			self._run("/api/method/da_services.api.v1.me.get_me", None)

	def test_health_needs_no_token(self):
		router.ensure_routes_registered()
		with configured_keys():
			self.assertEqual(self._run("/api/v1/da-services/health", None), "Guest")


class TestMe(_Base):
	def test_supervisor_profile(self):
		frappe.set_user(SUP)
		out = me_api.get_me()
		self.assertEqual(out["status"], "success")
		d = out["data"]
		self.assertEqual(d["roles"], [C.ROLE_SUPERVISOR])
		self.assertNotIn("scope", d)
		self.assertEqual(d["role"], "Supervisor")
		self.assertEqual(d["role_label"], "Supervisor (Woreda)")
		self.assertEqual(d["scope_label"], "")
		self.assertEqual(d["user"]["email"], SUP)
		# frontend hrefs (lib/rbac.ts NAV_GROUPS), in nav order
		self.assertEqual(d["menu"][0], "/dashboard")
		self.assertIn("/assignments", d["menu"])
		# level 1 of a profile change: the Woreda's Supervisor decides on the Approvals screen
		self.assertIn("/approvals", d["menu"])
		self.assertNotIn("/admin/users", d["menu"])

	def test_da_profile(self):
		frappe.set_user(DA_USER)
		d = me_api.get_me()["data"]
		self.assertNotIn("agent", d)  # no own DA-ID: the registry record is not looked up
		self.assertEqual(d["role"], "DA")
		self.assertEqual(d["scope_label"], "Own record")
		self.assertIn("/my-teams", d["menu"])
		self.assertNotIn("/approvals", d["menu"])
		self.assertNotIn("/agents", d["menu"])

	def test_administrator_is_admin_with_full_menu(self):
		frappe.set_user("Administrator")
		d = me_api.get_me()["data"]
		self.assertEqual((d["role"], d["scope_label"]), ("Admin", "All regions"))
		self.assertIn("/approvals", d["menu"])
		self.assertIn("/admin/settings", d["menu"])
		# Frappe's Administrator holds every role, so every role's menu items show.
		self.assertIn("/my-teams", d["menu"])

	def test_scope_labels_per_role(self):
		from unittest.mock import patch

		def labels(roles):
			"""(role codes, label per role, menu) for a user with `roles`."""
			with patch.object(me_api.frappe, "get_roles", return_value=list(roles)):
				codes = me_api.role_codes_for()
				return codes, {c: me_api.scope_label_for(c) for c in codes}, me_api.menu_for()

		codes, label, _menu = labels({C.ROLE_EXECUTIVE})
		self.assertEqual((codes, label["Executive"]), (["Executive"], "Read only"))
		_codes, label, _menu = labels({C.ROLE_SUPERVISOR})
		self.assertEqual(label["Supervisor"], "")
		_codes, label, menu = labels({C.ROLE_COMMS})
		self.assertEqual(label["CommsOfficer"], "Broadcast domain")
		self.assertEqual(menu, ["/dashboard", "/knowledge", "/broadcast", "/alerts"])
		# two roles: the widest one wins, the menu is the union
		codes, _label, menu = labels({C.ROLE_DA, C.ROLE_SUPERVISOR})
		self.assertEqual(codes, ["Supervisor", "DA"])
		self.assertTrue({"/my-teams", "/assignments"} <= set(menu))

	def test_menu_hrefs_match_the_portal_nav(self):
		hrefs = [href for href, _roles in me_api.NAV]
		self.assertTrue(all(h.startswith("/") for h in hrefs))
		self.assertEqual(len(hrefs), len(set(hrefs)))
		for _href, roles in me_api.NAV:
			self.assertTrue(set(roles) <= set(me_api.ROLE_LABELS))

	def test_me_needs_a_login_only(self):
		frappe.set_user(PLAIN)
		self.assertEqual(me_api.get_me()["status"], "success")  # Frappe refuses Guest before it

	def test_profile_hook_for_auth_me(self):
		ns, data = profile_api.resolve_user_profile_hook(user_doc=frappe.get_doc("User", SUP))
		self.assertEqual(ns, "da_services")
		self.assertEqual(data, {"roles": [C.ROLE_SUPERVISOR]})

	def test_profile_hook_returns_none_for_outsiders(self):
		ns, data = profile_api.resolve_user_profile_hook(user_doc=frappe.get_doc("User", PLAIN))
		self.assertEqual(ns, "da_services")
		self.assertIsNone(data)


class TestRbacAdminApi(_Base):
	def test_admin_creates_lists_updates_and_revokes(self):
		frappe.set_user(ADMIN)
		created = rbac_api.create_assignment(
			user=PLAIN, role=C.ROLE_EXECUTIVE, region_scope="ET03", notes="regional view"
		)
		self.assertEqual(created["status"], "success", created)
		name = created["data"]["name"]
		self.assertEqual(created["data"]["assigned_by"], ADMIN)

		listed = rbac_api.list_assignments(user=PLAIN)
		self.assertEqual(listed["data"][0]["name"], name)
		self.assertEqual(listed["meta"]["count"], 1)

		updated = rbac_api.update_assignment(name, notes="updated")
		self.assertEqual(updated["data"]["notes"], "updated")

		revoked = rbac_api.revoke_assignment(name, reason="moved teams")
		self.assertFalse(revoked["data"]["active"])
		self.assertEqual(revoked["data"]["effective_to"], today())
		self.assertIn("Revoked: moved teams", revoked["data"]["notes"])

	def test_invalid_grant_is_a_validation_error(self):
		frappe.set_user(ADMIN)
		out = rbac_api.create_assignment(user=PLAIN, role=C.ROLE_SUPERVISOR)  # no Woreda
		self.assertEqual(out["status"], "error")
		self.assertIn(frappe.response.get("http_status_code"), (400, 417))

	def test_supervisor_reads_but_cannot_administer_assignments(self):
		# DA RBAC Assignment gives Supervisor read only (Role Permission Manager).
		frappe.set_user(SUP)
		self.assertEqual(rbac_api.list_assignments()["status"], "success")
		out = rbac_api.create_assignment(
			user=PLAIN, role=C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01"
		)
		self.assertEqual(out["status"], "error")
		self.assertEqual(frappe.response.get("http_status_code"), 403)
		frappe.response.pop("http_status_code", None)
		out = rbac_api.create_assignment(user=PLAIN, role=C.ROLE_DA, da_id="DA-000002")
		self.assertEqual(out["status"], "error")
		self.assertEqual(frappe.response.get("http_status_code"), 403)

	def test_immutable_fields_are_refused(self):
		frappe.set_user(ADMIN)
		name = frappe.get_all("DA RBAC Assignment", filters={"user": SUP}, pluck="name")[0]
		out = rbac_api.update_assignment(name, user=PLAIN)
		self.assertEqual(out["status"], "error")
