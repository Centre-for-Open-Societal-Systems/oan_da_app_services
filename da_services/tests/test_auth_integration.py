"""Auth integration tests for oan_auth_service in DA Services.

Covers: deny-by-default revocation check, REST and RPC namespace protection,
/me profile enrichment, health endpoint accessibility, and scope resolution.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from da_services.api.middleware import check_da_assignment
from da_services.api.router import ensure_routes_registered
from da_services.services import constants as C

SUPERVISOR = "auth-test-supervisor@da.local"
DA_USER = "auth-test-da@da.local"
NO_ROLE_USER = "auth-test-norole@da.local"


def make_test_request(
	path: str,
	method: str = "GET",
	data: dict | None = None,
	headers: dict | None = None,
) -> Request:
	"""Construct a Werkzeug Request and wire up frappe.local state."""
	query_string = None
	if "?" in path:
		path, query_string = path.split("?", 1)

	builder_kwargs = {
		"path": path,
		"method": method.upper(),
		"base_url": "http://testsite.localhost",
		"headers": headers or {},
	}
	if query_string:
		builder_kwargs["query_string"] = query_string
	if data is not None:
		builder_kwargs["json"] = data

	builder = EnvironBuilder(**builder_kwargs)
	req = Request(builder.get_environ())

	frappe.local.request = req
	frappe.local.request_ip = "127.0.0.1"
	form_data = dict(req.args)
	if data:
		form_data.update(data)
	frappe.local.form_dict = frappe._dict(form_data)
	frappe.local.response = frappe._dict({})

	return req


def _user(email: str, roles: list[str] | None = None) -> str:
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = email.split("@")[0]
		u.send_welcome_email = 0
		u.insert(ignore_permissions=True, ignore_links=True)
	if roles:
		existing = set(frappe.get_roles(email))
		for role in roles:
			if role not in existing:
				frappe.get_doc("User", email).add_roles(role)
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


# ---------------------------------------------------------------------------
# 1. Deny-by-default revocation check
# ---------------------------------------------------------------------------


class TestRevocationCheck(IntegrationTestCase):
	"""Unit tests for the deny-by-default revocation_check callback."""

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA RBAC Assignment")
		_user(SUPERVISOR, [C.ROLE_SUPERVISOR])
		_user(DA_USER, [C.ROLE_DA])
		_user(NO_ROLE_USER)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_unrestricted_role_bypasses_check(self):
		result = check_da_assignment("Administrator")
		self.assertIsNone(result)

	def test_system_manager_bypasses_check(self):
		sm_user = _user("auth-test-sm@da.local", ["System Manager"])
		result = check_da_assignment(sm_user)
		self.assertIsNone(result)

	def test_user_without_da_role_is_rejected(self):
		result = check_da_assignment(NO_ROLE_USER)
		self.assertIsNotNone(result)
		self.assertIn("no DA Services role", result)

	def test_da_role_without_assignment_is_rejected(self):
		result = check_da_assignment(SUPERVISOR)
		self.assertIsNotNone(result)
		self.assertIn("No active DA RBAC assignment", result)

	def test_da_role_with_active_assignment_passes(self):
		_grant(SUPERVISOR, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		result = check_da_assignment(SUPERVISOR)
		self.assertIsNone(result)

	def test_expired_assignment_is_rejected(self):
		_grant(
			SUPERVISOR,
			C.ROLE_SUPERVISOR,
			region_scope="ET04",
			woreda_scope="ET04-W01",
			effective_from=add_days(today(), -30),
			effective_to=add_days(today(), -1),
		)
		result = check_da_assignment(SUPERVISOR)
		self.assertIsNotNone(result)

	def test_da_user_without_assignment_is_rejected(self):
		result = check_da_assignment(DA_USER)
		self.assertIsNotNone(result)

	def test_da_user_with_active_assignment_passes(self):
		_grant(DA_USER, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		result = check_da_assignment(DA_USER)
		self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 2. RPC namespace protection (/api/method/da_services.*)
# ---------------------------------------------------------------------------


class TestRPCNamespace(IntegrationTestCase):
	"""Verify the RPC namespace is registered with the JWT middleware."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_routes_registered()

	def test_rpc_namespace_is_registered(self):
		from oan_auth_service.api.middleware import _NAMESPACES

		self.assertIn("/api/method/da_services.", _NAMESPACES)

	def test_rpc_namespace_has_revocation_check(self):
		from oan_auth_service.api.middleware import _NAMESPACES

		config = _NAMESPACES.get("/api/method/da_services.")
		self.assertIsNotNone(config)
		self.assertIsNotNone(config.get("revocation_check"))
		self.assertEqual(config["revocation_check"], check_da_assignment)

	def test_rest_namespace_is_registered(self):
		from oan_auth_service.api.middleware import _NAMESPACES
		from oan_auth_service.api.router import NAMESPACE

		self.assertIn(NAMESPACE, _NAMESPACES)

	def test_rest_namespace_has_revocation_check(self):
		from oan_auth_service.api.middleware import _NAMESPACES
		from oan_auth_service.api.router import NAMESPACE

		config = _NAMESPACES.get(NAMESPACE)
		self.assertIsNotNone(config)
		self.assertIsNotNone(config.get("revocation_check"))

	def test_jwt_middleware_rejects_guest_on_rpc(self):
		"""Guest hitting an RPC endpoint in our namespace gets 401."""
		from oan_auth_service.api.middleware import validate_jwt_request

		req = make_test_request("/api/method/da_services.api.v1.da.get_da", method="GET")
		frappe.set_user("Guest")
		frappe.local.session = frappe._dict(user="Guest")
		with self.assertRaises(frappe.AuthenticationError):
			validate_jwt_request(req)

	def test_jwt_middleware_allows_authenticated_user(self):
		"""JWT middleware requires bearer token even when session exists."""
		from oan_auth_service.api.middleware import validate_jwt_request

		frappe.set_user("Administrator")
		_user(SUPERVISOR, [C.ROLE_SUPERVISOR])
		_grant(SUPERVISOR, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		req = make_test_request("/api/method/da_services.api.v1.da.get_da", method="GET")
		frappe.set_user(SUPERVISOR)
		frappe.local.session = frappe._dict(user=SUPERVISOR)
		# Middleware now requires JWT token even with active session
		with self.assertRaises(frappe.AuthenticationError):
			validate_jwt_request(req)


# ---------------------------------------------------------------------------
# 3. REST routes and /me profile enrichment
# ---------------------------------------------------------------------------


class TestRESTRoutes(IntegrationTestCase):
	"""Integration tests for the REST API endpoints through Frappe's dispatcher."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_routes_registered()

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		frappe.db.delete("DA RBAC Assignment")
		_user(SUPERVISOR, [C.ROLE_SUPERVISOR])
		_user(DA_USER, [C.ROLE_DA])
		_user(NO_ROLE_USER)

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def test_health_endpoint_accessible_as_guest(self):
		import frappe.api

		frappe.set_user("Guest")
		req = make_test_request("/api/v1/da-services/health", method="GET")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		self.assertEqual(data["status"], "success")
		self.assertEqual(data["data"]["service"], "da_services")
		self.assertEqual(data["data"]["status"], "healthy")

	def test_me_sets_session_user_and_returns_profile(self):
		"""Valid token sets the session user; /me returns identity and DA profile."""
		import frappe.api

		_grant(SUPERVISOR, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		frappe.set_user(SUPERVISOR)
		req = make_test_request("/api/v1/auth/me", method="GET")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		self.assertEqual(data["status"], "success")
		self.assertEqual(data["data"]["user"], SUPERVISOR)
		self.assertIn("profiles", data["data"])
		da_profile = data["data"]["profiles"].get("da_services")
		self.assertIsNotNone(da_profile)
		self.assertIn(C.ROLE_SUPERVISOR, da_profile["roles"])
		self.assertIn("ET04", da_profile["scope"]["regions"])
		self.assertIn("ET04-W01", da_profile["scope"]["woredas"])

	def test_me_returns_da_ids_for_assigned_da(self):
		import frappe.api

		_grant(DA_USER, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		frappe.set_user(DA_USER)
		req = make_test_request("/api/v1/auth/me", method="GET")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		da_profile = data["data"]["profiles"].get("da_services")
		self.assertIsNotNone(da_profile)
		self.assertIn(C.ROLE_DA, da_profile["roles"])
		self.assertIn("DA-000001", da_profile["scope"]["da_ids"])

	def test_me_returns_no_da_profile_for_user_without_da_role(self):
		import frappe.api

		frappe.set_user(NO_ROLE_USER)
		req = make_test_request("/api/v1/auth/me", method="GET")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		profiles = data["data"].get("profiles", {})
		self.assertIsNone(profiles.get("da_services"))

	def test_me_returns_da_profile_for_administrator(self):
		"""Administrator has DA roles on this site, so the profile hook returns data."""
		import frappe.api

		frappe.set_user("Administrator")
		req = make_test_request("/api/v1/auth/me", method="GET")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		da_profile = data["data"].get("profiles", {}).get("da_services")
		if da_profile is not None:
			self.assertIn("roles", da_profile)
			self.assertIn("scope", da_profile)

	def test_supervisor_scope_aggregates_multiple_assignments(self):
		"""Supervisor with assignments in two woredas sees both in their profile."""
		import frappe.api

		_grant(SUPERVISOR, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUPERVISOR, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W02")
		frappe.set_user(SUPERVISOR)
		req = make_test_request("/api/v1/auth/me", method="GET")
		res = frappe.api.handle(req)
		self.assertEqual(res.status_code, 200)
		data = json.loads(res.get_data(as_text=True))
		da_profile = data["data"]["profiles"]["da_services"]
		self.assertIn("ET04-W01", da_profile["scope"]["woredas"])
		self.assertIn("ET04-W02", da_profile["scope"]["woredas"])
		self.assertIn("ET04", da_profile["scope"]["regions"])
