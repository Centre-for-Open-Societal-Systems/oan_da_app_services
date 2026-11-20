"""Grievance integration: in-memory client fixtures and API endpoints."""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from da_services.integrations.grievance.client import GrievanceNotFound
from da_services.integrations.grievance.mock import MockGrievanceClient
from da_services.integrations.grievance.schemas import (
	GrievanceAction,
	GrievanceSubmission,
)
from da_services.services import constants as C

SUPERVISOR = "grv-test-supervisor@da.local"
DA_USER = "grv-test-da@da.local"


def _user(email, roles=None):
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


def _patch_api_client(test):
	"""API tests use the in-memory client; production get_client is HTTP-only."""
	MockGrievanceClient.reset()
	test._get_client_patch = patch(
		"da_services.api.v1.grievance.get_client",
		return_value=MockGrievanceClient.shared(),
	)
	test._get_client_patch.start()


def _stop_api_client_patch(test):
	patcher = getattr(test, "_get_client_patch", None)
	if patcher is not None:
		patcher.stop()
	MockGrievanceClient.reset()


# ---------------------------------------------------------------------------
# 1. In-memory client (test fixture)
# ---------------------------------------------------------------------------


class TestMockGrievanceClient(IntegrationTestCase):
	def setUp(self):
		MockGrievanceClient.reset()
		self.client = MockGrievanceClient()

	def tearDown(self):
		MockGrievanceClient.reset()

	def test_list_returns_fixtures(self):
		items, total = self.client.list_grievances()
		self.assertEqual(total, 3)
		self.assertEqual(len(items), 3)

	def test_list_filters_by_region(self):
		_items, total = self.client.list_grievances(region="ET04")
		self.assertEqual(total, 3)

	def test_list_filters_by_status(self):
		items, total = self.client.list_grievances(status="Open")
		self.assertEqual(total, 1)
		self.assertEqual(items[0].ticket_id, "GRV-000001")

	def test_get_grievance_detail(self):
		detail = self.client.get_grievance("GRV-000001")
		self.assertEqual(detail.subject, "Fertilizer delivery delayed")
		self.assertEqual(detail.status, "Open")
		self.assertEqual(detail.complainant_id, "DA-000001")

	def test_get_unknown_grievance_raises(self):
		with self.assertRaises(GrievanceNotFound):
			self.client.get_grievance("GRV-999999")

	def test_submit_creates_ticket(self):
		sub = GrievanceSubmission(
			complainant_id="DA-000001",
			complainant_name="Test DA",
			complainant_role="Development Agent",
			subject="Test grievance",
			description="Test description",
			category="Supply Chain",
		)
		result = self.client.submit_grievance(sub)
		self.assertTrue(result.accepted)
		self.assertIsNotNone(result.ticket_id)

		detail = self.client.get_grievance(result.ticket_id)
		self.assertEqual(detail.subject, "Test grievance")
		self.assertEqual(detail.status, "Open")

	def test_take_action_resolves(self):
		action = GrievanceAction(
			ticket_id="GRV-000001",
			action_type="resolve",
			comment="Issue resolved",
			resolution="Delivery rescheduled",
		)
		result = self.client.take_action(action)
		self.assertTrue(result.accepted)

		detail = self.client.get_grievance("GRV-000001")
		self.assertEqual(detail.status, "Resolved")
		self.assertIsNotNone(detail.resolved_at)

	def test_take_action_on_unknown_raises(self):
		action = GrievanceAction(ticket_id="GRV-999999", action_type="resolve")
		with self.assertRaises(GrievanceNotFound):
			self.client.take_action(action)

	def test_timeline_returns_entries(self):
		entries = self.client.get_timeline("GRV-000002")
		self.assertGreaterEqual(len(entries), 2)
		self.assertEqual(entries[0]["action"], "created")

	def test_options_returns_categories(self):
		opts = self.client.get_options()
		self.assertIn("categories", opts)
		self.assertNotIn("priorities", opts)
		self.assertIn("statuses", opts)


# ---------------------------------------------------------------------------
# 2. API endpoints
# ---------------------------------------------------------------------------


class TestGrievanceAPI(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from da_services.api.router import ensure_routes_registered

		ensure_routes_registered()

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		_patch_api_client(self)
		# Only the users this module creates — do not wipe shared RBAC rows.
		_user(SUPERVISOR, [C.ROLE_SUPERVISOR])
		_user(DA_USER, [C.ROLE_DA])

	def tearDown(self):
		_stop_api_client_patch(self)
		frappe.set_user("Administrator")
		super().tearDown()

	def _request(self, path, method="GET", data=None, user=None):
		import frappe.api
		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request as WerkzeugRequest

		if user:
			frappe.set_user(user)

		query_string = None
		if "?" in path:
			path, query_string = path.split("?", 1)

		builder_kwargs = {
			"path": path,
			"method": method.upper(),
			"base_url": "http://testsite.localhost",
		}
		if query_string:
			builder_kwargs["query_string"] = query_string
		if data is not None:
			builder_kwargs["json"] = data

		builder = EnvironBuilder(**builder_kwargs)
		req = WerkzeugRequest(builder.get_environ())

		frappe.local.request = req
		frappe.local.request_ip = "127.0.0.1"
		form_data = dict(req.args)
		if data:
			form_data.update(data)
		frappe.local.form_dict = frappe._dict(form_data)
		frappe.local.response = frappe._dict({})

		return frappe.api.handle(req)

	def test_list_grievances_as_supervisor(self):
		res = self._request("/api/v1/da-services/grievances", user=SUPERVISOR)
		self.assertEqual(res.status_code, 200)
		body = json.loads(res.get_data(as_text=True))
		self.assertEqual(body["status"], "success")
		self.assertIsInstance(body["data"], list)

	def test_get_grievance_detail(self):
		res = self._request("/api/v1/da-services/grievances/GRV-000001", user=SUPERVISOR)
		self.assertEqual(res.status_code, 200)
		body = json.loads(res.get_data(as_text=True))
		self.assertEqual(body["data"]["ticket_id"], "GRV-000001")

	def test_get_unknown_grievance_404(self):
		res = self._request("/api/v1/da-services/grievances/GRV-999999", user=SUPERVISOR)
		self.assertIn(res.status_code, (404, 417))

	def test_submit_grievance(self):
		res = self._request(
			"/api/v1/da-services/grievances",
			method="POST",
			data={
				"subject": "API test grievance",
				"description": "Test via API",
				"category": "Supply Chain",
				"subcategory": "Input Delivery",
			},
			user=SUPERVISOR,
		)
		self.assertIn(res.status_code, (200, 201))
		body = json.loads(res.get_data(as_text=True))
		self.assertEqual(body["data"]["sync_state"], "synced")
		self.assertIsNotNone(body["data"]["ticket_id"])

	def test_submit_grievance_requires_subject(self):
		res = self._request(
			"/api/v1/da-services/grievances",
			method="POST",
			data={"description": "No subject"},
			user=SUPERVISOR,
		)
		self.assertNotEqual(res.status_code, 201)

	def test_timeline(self):
		res = self._request("/api/v1/da-services/grievances/GRV-000002/timeline", user=SUPERVISOR)
		self.assertEqual(res.status_code, 200)
		body = json.loads(res.get_data(as_text=True))
		self.assertIsInstance(body["data"], list)
		self.assertGreaterEqual(len(body["data"]), 1)

	def test_options(self):
		res = self._request("/api/v1/da-services/grievances/options", user=SUPERVISOR)
		self.assertEqual(res.status_code, 200)
		body = json.loads(res.get_data(as_text=True))
		self.assertIn("categories", body["data"])

	def test_retry_sync(self):
		res = self._request(
			"/api/v1/da-services/grievances/sync/retry",
			method="POST",
			data={},
			user=SUPERVISOR,
		)
		self.assertEqual(res.status_code, 200)
		body = json.loads(res.get_data(as_text=True))
		self.assertEqual(body["data"]["count"], 0)

	def test_da_sees_own_ticket_after_submit(self):
		res = self._request(
			"/api/v1/da-services/grievances",
			method="POST",
			data={"subject": "DA own ticket test", "category": "Supply Chain"},
			user=DA_USER,
		)
		self.assertIn(res.status_code, (200, 201))

		res = self._request("/api/v1/da-services/grievances", user=DA_USER)
		self.assertEqual(res.status_code, 200)
		body = json.loads(res.get_data(as_text=True))
		subjects = [g["subject"] for g in body["data"]]
		self.assertIn("DA own ticket test", subjects)

	def test_body_complainant_id_ignored(self):
		"""Body complainant fields are not trusted; session user is the local fallback only."""
		res = self._request(
			"/api/v1/da-services/grievances",
			method="POST",
			data={
				"subject": "Spoofed identity test",
				"complainant_id": "DA-FAKE-999",
				"complainant_name": "Evil Actor",
			},
			user=DA_USER,
		)
		self.assertIn(res.status_code, (200, 201))
		body = json.loads(res.get_data(as_text=True))
		ticket_id = body["data"]["ticket_id"]
		if ticket_id:
			res = self._request(f"/api/v1/da-services/grievances/{ticket_id}", user=DA_USER)
			detail = json.loads(res.get_data(as_text=True))
			self.assertEqual(detail["data"]["complainant_id"], DA_USER)
			self.assertNotEqual(detail["data"]["complainant_name"], "Evil Actor")

	def test_upstream_unavailable_returns_503(self):
		from da_services.integrations.grievance.client import GrievanceServiceUnavailable

		with patch(
			"da_services.integrations.grievance.mock.MockGrievanceClient.submit_grievance",
			side_effect=GrievanceServiceUnavailable("test"),
		):
			res = self._request(
				"/api/v1/da-services/grievances",
				method="POST",
				data={"subject": "Unavailable fallback test"},
				user=DA_USER,
			)
		self.assertEqual(res.status_code, 503)
		body = json.loads(res.get_data(as_text=True))
		self.assertEqual(body["status"], "error")
		self.assertEqual(body["code"], "SERVICE_UNAVAILABLE")

	def test_list_upstream_fault_is_bad_gateway(self):
		from unittest.mock import Mock

		from da_services.integrations.grievance.client import GrievanceServiceError

		client = Mock()
		client.list_grievances.side_effect = GrievanceServiceError("upstream refused: HTTP 401")
		with patch("da_services.api.v1.grievance.get_client", return_value=client):
			res = self._request("/api/v1/da-services/grievances", user=DA_USER)

		self.assertEqual(res.status_code, 502)
		body = json.loads(res.get_data(as_text=True))
		self.assertEqual(body["status"], "error")
		self.assertEqual(body["code"], "BAD_GATEWAY")
		self.assertNotIn("401", body["message"])
