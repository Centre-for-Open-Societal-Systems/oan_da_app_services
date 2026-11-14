"""DA Registry client boundary: mock behaviour, factory, and the v1 read endpoint."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.api.v1 import da as da_api
from da_services.integrations.da_registry.client import (
	DANotFound,
	DARegistryContractPending,
	DARegistryError,
	get_client,
)
from da_services.integrations.da_registry.http import HttpDARegistryClient
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.integrations.da_registry.schemas import LifecycleOutcome
from da_services.services import constants as C
from da_services.services.da_lookup import get_da_reference
from da_services.tests.fixtures import (
	aligned_mock_geo,
	bind_da_user,
	cleanup_test_rows,
	require_geo_masters,
)

SUPERVISOR = "test-supervisor2@da.local"


class TestMockClient(IntegrationTestCase):
	def setUp(self):
		require_geo_masters()
		MockDARegistryClient.reset()
		self.geo = aligned_mock_geo()
		self.client = MockDARegistryClient()

	def test_get_da_maps_registry_fields(self):
		ref = self.client.get_da("DA-000001")
		self.assertEqual(ref.full_name, "Abebe Kebede")
		self.assertEqual(ref.active_status, C.ACTIVE_STATUS_ACTIVE)
		self.assertEqual(ref.woreda, self.geo["woreda"])
		self.assertEqual(ref.kebele, self.geo["kebele"])
		self.assertEqual(ref.source_version, "3")
		self.assertIsNotNone(ref.fetched_at)

	def test_unknown_da_raises_not_found(self):
		with self.assertRaises(DANotFound):
			self.client.get_da("DA-999999")
		self.assertFalse(self.client.validate_da_id("DA-999999"))
		self.assertTrue(self.client.validate_da_id("DA-000001"))

	def test_lifecycle_outcome_updates_status_and_is_idempotent(self):
		outcome = LifecycleOutcome(
			da_id="DA-000001",
			transition_type="Leave",
			new_active_status=C.ACTIVE_STATUS_ON_LEAVE,
			effective_date=today(),
			reason="Annual leave",
			approver="Administrator",
			reference="LT-0001",
		)
		first = self.client.push_lifecycle_outcome(outcome)
		self.assertTrue(first.accepted)
		self.assertEqual(self.client.get_da("DA-000001").active_status, C.ACTIVE_STATUS_ON_LEAVE)
		self.assertEqual(self.client.get_da("DA-000001").source_version, "4")

		replay = self.client.push_lifecycle_outcome(outcome)
		self.assertEqual(replay.external_reference, first.external_reference)
		self.assertEqual(self.client.get_da("DA-000001").source_version, "4")  # not bumped again

	def test_transfer_moves_kebele(self):
		target = self.geo["kebele_alt"]
		outcome = LifecycleOutcome(
			da_id="DA-000001",
			transition_type="Transfer",
			new_active_status=None,
			effective_date=today(),
			reason="Coverage gap",
			approver="Administrator",
			reference="LT-0002",
			target_kebele=target,
		)
		self.client.push_lifecycle_outcome(outcome)
		self.assertEqual(self.client.get_da("DA-000001").kebele, target)


class TestClientFactory(IntegrationTestCase):
	def test_default_backend_is_mock(self):
		MockDARegistryClient.reset()
		self.assertIsInstance(get_client(), MockDARegistryClient)
		self.assertIs(get_client(), get_client())  # shared instance

	def test_http_backend_needs_url_and_service_account(self):
		keys = ("da_registry_backend", "da_registry_auth", "da_registry_base_url", "da_registry_keycloak")
		saved = {k: frappe.conf.get(k) for k in keys}
		try:
			frappe.conf.da_registry_backend = "http"
			frappe.conf.da_registry_auth = "service"
			frappe.conf.da_registry_base_url = "https://registry.example"
			frappe.conf.da_registry_keycloak = {}
			with self.assertRaises(DARegistryError):
				get_client()
			frappe.conf.da_registry_keycloak = {
				"token_url": "https://kc/t",
				"client_id": "c",
				"client_secret": "s",
			}
			self.assertIsInstance(get_client(), HttpDARegistryClient)
		finally:
			for k, v in saved.items():
				frappe.conf[k] = v

	def test_http_backend_has_no_lifecycle_api_yet(self):
		client = HttpDARegistryClient("https://registry.example", keycloak={})
		outcome = LifecycleOutcome("DA-000001", "Leave", "On-leave", today(), "r", "sup", "ref-1")
		with self.assertRaises(DARegistryContractPending):
			client.push_lifecycle_outcome(outcome)


class TestGetDaEndpoint(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		require_geo_masters()
		MockDARegistryClient.reset()
		self.geo = aligned_mock_geo()
		cleanup_test_rows(users=(SUPERVISOR,))
		if not frappe.db.exists("User", SUPERVISOR):
			u = frappe.new_doc("User")
			u.email = SUPERVISOR
			u.first_name = "Supervisor Two"
			u.send_welcome_email = 0
			u.insert(ignore_permissions=True, ignore_links=True)
		grant = frappe.new_doc("DA RBAC Assignment")
		grant.user = SUPERVISOR
		grant.role = C.ROLE_SUPERVISOR
		grant.region_scope = self.geo["region"]
		grant.woreda_scope = self.geo["woreda"]
		grant.effective_from = today()
		grant.insert(ignore_permissions=True, ignore_links=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		cleanup_test_rows(users=(SUPERVISOR,))

	def test_supervisor_reads_da_in_own_woreda(self):
		frappe.set_user(SUPERVISOR)
		ref = get_da_reference("DA-000001")
		self.assertEqual(ref.da_id, "DA-000001")
		self.assertEqual(ref.woreda, self.geo["woreda"])

	def test_da_id_with_surrounding_spaces_still_resolves(self):
		frappe.set_user(SUPERVISOR)
		ref = get_da_reference("  DA-000001 \t")
		self.assertEqual(ref.da_id, "DA-000001")

	def test_assignment_strips_pasted_scope(self):
		g = self.geo
		# Desk fetch_from fills da_id from User; programmatic inserts set it from the bound User.
		bind_da_user(SUPERVISOR, "DA-000001")
		grant = frappe.new_doc("DA RBAC Assignment")
		grant.user = SUPERVISOR
		grant.role = C.ROLE_DA
		grant.da_id = frappe.db.get_value("User", SUPERVISOR, "da_registry_id")
		grant.region_scope = f" {g['region']}"
		grant.woreda_scope = f"{g['woreda']} "
		grant.effective_from = today()
		grant.insert(ignore_permissions=True, ignore_links=True)
		self.assertEqual(grant.da_id, "DA-000001")
		self.assertEqual(grant.region_scope, g["region"])
		self.assertEqual(grant.woreda_scope, g["woreda"])

	def test_supervisor_is_denied_outside_woreda(self):
		frappe.set_user(SUPERVISOR)
		with self.assertRaises(frappe.PermissionError):
			get_da_reference("DA-000003")  # other chain (aligned mock)

	def test_guest_is_denied(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			get_da_reference("DA-000001")

	def test_unknown_da_is_not_found(self):
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.DoesNotExistError):
			get_da_reference("DA-424242")

	def test_endpoint_wraps_success_in_envelope(self):
		frappe.set_user(SUPERVISOR)
		out = da_api.get_da("DA-000001")
		self.assertEqual(out["status"], "success")
		self.assertEqual(out["data"]["da_id"], "DA-000001")

	def test_endpoint_wraps_denial_in_error_envelope(self):
		frappe.set_user(SUPERVISOR)
		out = da_api.get_da("DA-000003")
		self.assertEqual(out["status"], "error")
		self.assertEqual(frappe.response.get("http_status_code"), 403)
