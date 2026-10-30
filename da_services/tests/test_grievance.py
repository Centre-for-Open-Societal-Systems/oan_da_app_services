"""Grievance integration: mock client, queue processor, and API endpoints."""

import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.integrations.grievance.client import GrievanceNotFound, get_client
from da_services.integrations.grievance.mock import MockGrievanceClient
from da_services.integrations.grievance.queue import enqueue_event, process_single, retry_failed
from da_services.integrations.grievance.schemas import (
	GrievanceAction,
	GrievanceSubmission,
)
from da_services.services import constants as C

SUPERVISOR = "grv-test-supervisor@da.local"
DA_USER = "grv-test-da@da.local"
COMMS_USER = "grv-test-comms@da.local"
NO_ROLE_USER = "grv-test-norole@da.local"


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


def _grant(user, role, **kw):
	doc = frappe.new_doc("DA RBAC Assignment")
	doc.user = user
	doc.role = role
	doc.effective_from = kw.pop("effective_from", today())
	for k, v in kw.items():
		setattr(doc, k, v)
	doc.insert(ignore_permissions=True, ignore_links=True)
	return doc


def _use_mock_grievance(test):
	"""These tests exercise the in-memory client. A site configured for http must not leak in."""
	test._grievance_backend = frappe.conf.get("grievance_backend")
	frappe.conf.grievance_backend = "mock"


def _restore_grievance_backend(test):
	previous = getattr(test, "_grievance_backend", None)
	if previous is None:
		frappe.conf.pop("grievance_backend", None)
	else:
		frappe.conf.grievance_backend = previous


# ---------------------------------------------------------------------------
# 1. Mock client
# ---------------------------------------------------------------------------


class TestMockGrievanceClient(IntegrationTestCase):
	def setUp(self):
		_use_mock_grievance(self)
		MockGrievanceClient.reset()
		self.client = MockGrievanceClient()

	def tearDown(self):
		_restore_grievance_backend(self)

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

	def test_default_backend_is_mock(self):
		"""An unset grievance_backend selects the mock. A site may still set http."""
		previous = frappe.conf.get("grievance_backend")
		frappe.conf.pop("grievance_backend", None)
		try:
			MockGrievanceClient.reset()
			self.assertIsInstance(get_client(), MockGrievanceClient)
		finally:
			if previous is None:
				frappe.conf.pop("grievance_backend", None)
			else:
				frappe.conf.grievance_backend = previous


# ---------------------------------------------------------------------------
# 2. Queue processor
# ---------------------------------------------------------------------------


class TestQueueProcessor(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		_use_mock_grievance(self)
		MockGrievanceClient.reset()
		frappe.db.delete("DA Integration Event")

	def tearDown(self):
		_restore_grievance_backend(self)
		frappe.set_user("Administrator")

	def test_enqueue_creates_pending_event(self):
		name = enqueue_event(
			event_type="Grievance Submit",
			payload={
				"complainant_id": "DA-000001",
				"complainant_name": "Test",
				"complainant_role": "Development Agent",
				"subject": "Queue test",
			},
			reference_id="test-ref-001",
		)
		doc = frappe.get_doc("DA Integration Event", name)
		self.assertEqual(doc.event_type, "Grievance Submit")
		self.assertEqual(doc.reference_id, "test-ref-001")
		self.assertIn(doc.status, ("Pending", "Processing", "Completed"))

	def test_process_single_completes_submit(self):
		name = enqueue_event(
			event_type="Grievance Submit",
			payload={
				"complainant_id": "DA-000001",
				"complainant_name": "Test",
				"complainant_role": "Development Agent",
				"subject": "Process test",
				"description": "",
				"category": "",
				"subcategory": "",
				"region": None,
				"woreda": None,
				"attachments": [],
			},
		)
		process_single(name)
		doc = frappe.get_doc("DA Integration Event", name)
		self.assertEqual(doc.status, "Completed")
		self.assertIsNotNone(doc.external_reference)
		self.assertIsNotNone(doc.completed_at)

	def test_process_single_completes_action(self):
		name = enqueue_event(
			event_type="Grievance Action",
			payload={
				"ticket_id": "GRV-000001",
				"action_type": "resolve",
				"comment": "Fixed",
				"resolution": "Done",
				"assigned_to": None,
			},
		)
		process_single(name)
		doc = frappe.get_doc("DA Integration Event", name)
		self.assertEqual(doc.status, "Completed")

	def test_retry_failed_resets_status(self):
		name = enqueue_event(
			event_type="Grievance Submit",
			payload={
				"complainant_id": "DA-000001",
				"complainant_name": "Test",
				"complainant_role": "Development Agent",
				"subject": "Retry test",
			},
		)
		doc = frappe.get_doc("DA Integration Event", name)
		doc.db_set("status", "Failed")
		doc.db_set("attempts", 5)
		frappe.db.commit()

		retried = retry_failed(name)
		self.assertEqual(retried, [name])

		doc.reload()
		self.assertEqual(doc.status, "Pending")
		self.assertEqual(doc.attempts, 0)

	def test_completed_event_is_skipped(self):
		name = enqueue_event(
			event_type="Grievance Submit",
			payload={
				"complainant_id": "DA-000001",
				"complainant_name": "Test",
				"complainant_role": "Development Agent",
				"subject": "Skip test",
			},
		)
		doc = frappe.get_doc("DA Integration Event", name)
		doc.db_set("status", "Completed")
		frappe.db.commit()

		process_single(name)
		doc.reload()
		self.assertEqual(doc.status, "Completed")


# ---------------------------------------------------------------------------
# 3. API endpoints
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
		_use_mock_grievance(self)
		MockGrievanceClient.reset()
		frappe.db.delete("DA RBAC Assignment")
		frappe.db.delete("DA Integration Event")
		_user(SUPERVISOR, [C.ROLE_SUPERVISOR])
		_user(DA_USER, [C.ROLE_DA])
		_user(COMMS_USER, [C.ROLE_COMMS])
		_user(NO_ROLE_USER)
		_grant(SUPERVISOR, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA_USER, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(COMMS_USER, C.ROLE_COMMS, region_scope="ET04")

	def tearDown(self):
		_restore_grievance_backend(self)
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

	def test_list_grievances_guest_denied(self):
		res = self._request("/api/v1/da-services/grievances", user="Guest")
		self.assertIn(res.status_code, (403, 401))

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

	def test_retry_sync_admin_only(self):
		res = self._request(
			"/api/v1/da-services/grievances/sync/retry",
			method="POST",
			data={},
			user=SUPERVISOR,
		)
		self.assertIn(res.status_code, (403, 401))

	def test_retry_sync_as_admin(self):
		res = self._request(
			"/api/v1/da-services/grievances/sync/retry",
			method="POST",
			data={},
			user="Administrator",
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
			self.assertEqual(detail["data"]["complainant_id"], "DA-000001")
			self.assertNotEqual(detail["data"]["complainant_name"], "Evil Actor")

	def test_comms_officer_denied(self):
		res = self._request("/api/v1/da-services/grievances", user=COMMS_USER)
		self.assertIn(res.status_code, (403, 401))

	def test_upstream_unavailable_creates_queue_row(self):
		from unittest.mock import patch

		from da_services.integrations.grievance.client import GrievanceServiceUnavailable

		with patch(
			"da_services.integrations.grievance.mock.MockGrievanceClient.submit_grievance",
			side_effect=GrievanceServiceUnavailable("test"),
		):
			res = self._request(
				"/api/v1/da-services/grievances",
				method="POST",
				data={"subject": "Queue fallback test"},
				user=DA_USER,
			)
			self.assertIn(res.status_code, (200, 201))
			body = json.loads(res.get_data(as_text=True))
			self.assertEqual(body["data"]["sync_state"], "pending")
			self.assertIsNotNone(body["data"]["queue_ref"])

			doc = frappe.get_doc("DA Integration Event", body["data"]["queue_ref"])
			self.assertEqual(doc.event_type, "Grievance Submit")
			self.assertIn(doc.status, ("Pending", "Processing", "Completed"))

	def test_list_upstream_fault_is_bad_gateway(self):
		from unittest.mock import Mock, patch

		from da_services.integrations.grievance.client import GrievanceServiceError

		client = Mock()
		client.list_grievances.side_effect = GrievanceServiceError("IAM login refused: HTTP 401")
		with patch("da_services.api.v1.grievance.get_client", return_value=client):
			res = self._request("/api/v1/da-services/grievances", user=DA_USER)

		self.assertEqual(res.status_code, 502)
		body = json.loads(res.get_data(as_text=True))
		self.assertEqual(body["status"], "error")
		self.assertEqual(body["code"], "BAD_GATEWAY")
		self.assertNotIn("401", body["message"])
		self.assertNotIn("IAM", body["message"])
