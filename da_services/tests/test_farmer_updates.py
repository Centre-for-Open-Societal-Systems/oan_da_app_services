"""Tests for farmer profile update API (DA-41).

Tests offline-first update workflow with:
- Permission validation
- Field validation
- Queue creation
- Version conflict detection
- Retry logic
"""

from __future__ import annotations

import json

import frappe
from frappe.tests import IntegrationTestCase

from da_services.services import constants as C

# Test helper functions


def _user(email, roles):
	"""Create or get user with roles."""
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"roles": [{"role": r} for r in roles],
			}
		)
		user.insert(ignore_permissions=True, ignore_links=True)
	return email


def _grant(user, role, **kw):
	"""Grant RBAC assignment."""
	from frappe.utils import today

	doc = frappe.get_doc(
		{
			"doctype": "DA RBAC Assignment",
			"user": user,
			"role": role,
			"active": 1,
			"effective_from": kw.pop("effective_from", today()),
			**kw,
		}
	)
	doc.insert(ignore_permissions=True, ignore_links=True)
	frappe.db.commit()


# Test users
DA1 = "da1@da.local"
DA2 = "da2@da.local"
SUP = "sup@da.local"


class TestFarmerUpdates(IntegrationTestCase):
	"""Test cases for DA-41 farmer profile updates."""

	@classmethod
	def setUpClass(cls):
		"""Set up test data once for all tests."""
		super().setUpClass()

		# Create test users
		cls.da1_user = _user(DA1, [C.ROLE_DA])
		cls.da2_user = _user(DA2, [C.ROLE_DA])
		cls.sup_user = _user(SUP, [C.ROLE_SUPERVISOR])

		# Grant scopes (need region_scope with woreda_scope)
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA2, C.ROLE_DA, da_id="DA-000002", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")

	def setUp(self):
		"""Set up test data for each test."""
		frappe.set_user("Administrator")
		# These cases use the in-memory registry (farmer-uuid-001 and the rest). A site
		# configured for the live Farmer Registry must not leak into them.
		self._farmer_backend = frappe.conf.get("farmer_registry_backend")
		frappe.conf.farmer_registry_backend = "mock"

		# Create test farmers
		self._create_test_farmers()

		# Link farmers to DAs
		self._create_farmer_links()

	def tearDown(self):
		"""Clean up after each test."""
		if self._farmer_backend is None:
			frappe.conf.pop("farmer_registry_backend", None)
		else:
			frappe.conf.farmer_registry_backend = self._farmer_backend
		frappe.set_user("Administrator")
		saved = getattr(self, "_farmer_registry_backend", None)
		if saved is None:
			frappe.conf.pop("farmer_registry_backend", None)
		else:
			frappe.conf.farmer_registry_backend = saved
		# The tests assign a plain dict to frappe.form_dict, which replaces Frappe's module-level
		# LocalProxy for the rest of the process; later tests then break (File.validate reads
		# frappe.form_dict.file_size as an attribute). Restore the proxy and an empty form.
		frappe.form_dict = frappe.local("form_dict")
		frappe.local.form_dict = frappe._dict()

		# Clean up integration events
		frappe.db.delete("DA Integration Event", {"event_type": "Farmer Update"})

		# Clean up test data
		frappe.db.delete("DA Farmer Link")
		frappe.db.delete("DA Farmer Reference")

		frappe.db.commit()

	def _create_test_farmers(self):
		"""Create test farmers in DA Farmer Reference."""
		farmers = [
			{
				"farmer_id": "farmer-uuid-001",
				"reg_id": "FR-000101",
				"farmer_name": "Abebe Kebede",
				"phone": "911234567",
				"email": "abebe@example.com",
				"kebele": "Bako 01",
				"woreda": "ET04-W01",
				"region": "ET04",
				"household_size": 4,
				"total_land_hectares": 2.5,
				"registration_state": "APPROVED",
			},
			{
				"farmer_id": "farmer-uuid-002",
				"reg_id": "FR-000102",
				"farmer_name": "Almaz Tesfaye",
				"phone": "911234568",
				"email": "almaz@example.com",
				"kebele": "Bako 02",
				"woreda": "ET04-W01",
				"region": "ET04",
				"household_size": 5,
				"total_land_hectares": 3.0,
				"registration_state": "APPROVED",
			},
			{
				"farmer_id": "farmer-uuid-003",
				"reg_id": "FR-000103",
				"farmer_name": "Chaltu Daba",
				"phone": "911234569",
				"email": "chaltu@example.com",
				"kebele": "Bole 01",
				"woreda": "ET03-W02",
				"region": "ET03",
				"household_size": 6,
				"total_land_hectares": 1.5,
				"registration_state": "APPROVED",
			},
		]

		for farmer_data in farmers:
			if not frappe.db.exists("DA Farmer Reference", farmer_data["farmer_id"]):
				doc = frappe.get_doc({"doctype": "DA Farmer Reference", **farmer_data})
				doc.insert(ignore_permissions=True, ignore_links=True)

		frappe.db.commit()

	def _create_farmer_links(self):
		"""Create farmer-DA links."""
		links = [
			{
				"da_id": "DA-000001",
				"farmer_id": "farmer-uuid-001",
				"farmer_name": "Abebe Kebede",
				"kebele": "Bako 01",
				"woreda": "ET04-W01",
				"region": "ET04",
				"link_type": "Automatic",
				"is_active": 1,
			},
			{
				"da_id": "DA-000001",
				"farmer_id": "farmer-uuid-002",
				"farmer_name": "Almaz Tesfaye",
				"kebele": "Bako 02",
				"woreda": "ET04-W01",
				"region": "ET04",
				"link_type": "Manual",
				"is_active": 1,
			},
			{
				"da_id": "DA-000002",
				"farmer_id": "farmer-uuid-003",
				"farmer_name": "Chaltu Daba",
				"kebele": "Bole 01",
				"woreda": "ET03-W02",
				"region": "ET03",
				"link_type": "Automatic",
				"is_active": 1,
			},
		]

		for link_data in links:
			doc = frappe.get_doc({"doctype": "DA Farmer Link", **link_data})
			doc.insert(ignore_permissions=True, ignore_links=True)

		frappe.db.commit()

	# ========================================================================
	# SUCCESS CASES
	# ========================================================================

	def test_update_linked_farmer_success(self):
		"""Test DA can update linked farmer - creates queue event."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers_update import update_farmer_profile

		# Update phone and email using frappe.form_dict
		frappe.form_dict = {
			"phone": "912345678",
			"email": "newemail@example.com",
		}

		result = update_farmer_profile("farmer-uuid-001")

		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"]["farmer_id"], "farmer-uuid-001")
		self.assertEqual(result["data"]["status"], "queued")
		self.assertIn("event_id", result["data"])

		# Verify event was created
		event_id = result["data"]["event_id"]
		self.assertTrue(frappe.db.exists("DA Integration Event", event_id))

		event = frappe.get_doc("DA Integration Event", event_id)
		self.assertEqual(event.event_type, "Farmer Update")
		self.assertEqual(event.status, "Pending")
		# reference_id now stores client_op_id for idempotency (Blocker #2 fix)
		self.assertTrue(event.reference_id)  # Should have a generated op_id
		self.assertNotEqual(event.reference_id, "farmer-uuid-001")  # Not the farmer_id

		# Verify payload
		payload = json.loads(event.payload)
		self.assertEqual(payload["farmer_id"], "farmer-uuid-001")  # farmer_id in payload
		self.assertEqual(payload["updates"]["phone"], "912345678")
		self.assertEqual(payload["updates"]["email"], "newemail@example.com")

	def test_update_with_client_op_id_idempotency(self):
		"""Test idempotency - same client_op_id returns same result."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers_update import update_farmer_profile

		client_op_id = "test-update-123"

		# First request
		frappe.form_dict = {
			"phone": "912345678",
			"client_op_id": client_op_id,
		}
		result1 = update_farmer_profile("farmer-uuid-001")
		event_id1 = result1["data"]["event_id"]

		# Second request with same client_op_id
		frappe.form_dict = {
			"phone": "912345678",
			"client_op_id": client_op_id,
		}
		result2 = update_farmer_profile("farmer-uuid-001")
		event_id2 = result2["data"]["event_id"]

		# Should return same event (idempotent)
		self.assertEqual(event_id1, event_id2)

		# Should only have one event for this client_op_id
		# reference_id now stores client_op_id, not farmer_id (Blocker #2 fix)
		events = frappe.get_all(
			"DA Integration Event",
			filters={"event_type": "Farmer Update", "reference_id": client_op_id},
		)
		self.assertEqual(len(events), 1)

	def test_different_client_op_ids_both_queued(self):
		"""Test two different updates with different client_op_ids both succeed.

		Addresses PR review blocker #2: Ensures idempotency is based on client_op_id,
		not farmer_id, so multiple distinct updates to the same farmer are all queued.
		"""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers_update import update_farmer_profile

		# First update with client_op_id_1
		frappe.form_dict = {
			"phone": "912345678",
			"client_op_id": "update-1-phone",
		}
		result1 = update_farmer_profile("farmer-uuid-001")
		event_id1 = result1["data"]["event_id"]

		# Second update with different client_op_id (different field)
		frappe.form_dict = {
			"email": "newemail@example.com",
			"client_op_id": "update-2-email",
		}
		result2 = update_farmer_profile("farmer-uuid-001")
		event_id2 = result2["data"]["event_id"]

		# Should create two separate events (not deduplicated)
		self.assertNotEqual(event_id1, event_id2)

		# Verify both events exist and are distinct
		event1 = frappe.get_doc("DA Integration Event", event_id1)
		event2 = frappe.get_doc("DA Integration Event", event_id2)

		self.assertEqual(event1.reference_id, "update-1-phone")
		self.assertEqual(event2.reference_id, "update-2-email")

		# Verify payloads are different
		payload1 = json.loads(event1.payload)
		payload2 = json.loads(event2.payload)

		self.assertIn("phone", payload1["updates"])
		self.assertIn("email", payload2["updates"])

		# Both should be for the same farmer
		self.assertEqual(payload1["farmer_id"], "farmer-uuid-001")
		self.assertEqual(payload2["farmer_id"], "farmer-uuid-001")

	def test_update_multiple_fields(self):
		"""Test updating multiple fields at once."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers_update import update_farmer_profile

		frappe.form_dict = {
			"phone": "912345678",
			"email": "new@example.com",
			"household_size": 6,
			"total_land_hectares": 3.5,
		}

		result = update_farmer_profile("farmer-uuid-001")

		self.assertEqual(result["status"], "success")

		# Verify all fields in payload
		event = frappe.get_doc("DA Integration Event", result["data"]["event_id"])
		payload = json.loads(event.payload)
		updates = payload["updates"]

		self.assertEqual(updates["phone"], "912345678")
		self.assertEqual(updates["email"], "new@example.com")
		self.assertEqual(updates["household_size"], 6)
		self.assertEqual(updates["total_land_hectares"], 3.5)

	# ========================================================================
	# PERMISSION ERRORS
	# ========================================================================

	def test_update_non_linked_farmer_denied(self):
		"""Test DA cannot update farmer not linked to them."""
		frappe.set_user(DA1)  # DA1 is NOT linked to farmer-uuid-003

		from da_services.api.v1.farmers_update import update_farmer_profile

		frappe.form_dict = {
			"phone": "912345678",
		}

		result = update_farmer_profile("farmer-uuid-003")

		# Should return error response
		self.assertEqual(result["status"], "error")
		self.assertIn("permission", result["message"].lower())

	def test_supervisor_cannot_update(self):
		"""Test Supervisor role cannot update farmers (read-only)."""
		frappe.set_user(SUP)

		from da_services.api.v1.farmers_update import update_farmer_profile

		frappe.form_dict = {
			"phone": "912345678",
		}

		result = update_farmer_profile("farmer-uuid-001")

		# Should return error response
		self.assertEqual(result["status"], "error")
		self.assertIn("permission", result["message"].lower())

	# ========================================================================
	# VALIDATION ERRORS
	# ========================================================================

	def test_update_readonly_field_denied(self):
		"""Test cannot update readonly fields like farmer_name."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers_update import update_farmer_profile

		frappe.form_dict = {
			"farmer_name": "New Name",  # Readonly!
		}

		result = update_farmer_profile("farmer-uuid-001")

		# Should return error response
		self.assertEqual(result["status"], "error")
		self.assertIn("readonly", result["message"].lower())

	def test_update_unknown_field_denied(self):
		"""Test cannot update unknown/invalid fields."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers_update import update_farmer_profile

		frappe.form_dict = {
			"invalid_field": "some value",
		}

		result = update_farmer_profile("farmer-uuid-001")

		# Should return error response
		self.assertEqual(result["status"], "error")
		self.assertIn("unknown", result["message"].lower())

	def test_invalid_phone_format(self):
		"""Test phone number validation."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers_update import update_farmer_profile

		# Invalid format (should be 9XXXXXXXX)
		frappe.form_dict = {
			"phone": "12345",
		}

		result = update_farmer_profile("farmer-uuid-001")

		# Should return error response
		self.assertEqual(result["status"], "error")
		self.assertIn("phone", result["message"].lower())

	def test_invalid_email_format(self):
		"""Test email validation."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers_update import update_farmer_profile

		frappe.form_dict = {
			"email": "invalid-email",
		}

		result = update_farmer_profile("farmer-uuid-001")

		# Should return error response
		self.assertEqual(result["status"], "error")
		self.assertIn("email", result["message"].lower())

	def test_negative_numeric_field(self):
		"""Test cannot set negative values for numeric fields."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers_update import update_farmer_profile

		frappe.form_dict = {
			"household_size": -5,
		}

		result = update_farmer_profile("farmer-uuid-001")

		# Should return error response
		self.assertEqual(result["status"], "error")
		self.assertIn("negative", result["message"].lower())

	def test_empty_update_denied(self):
		"""Test must provide at least one field to update."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers_update import update_farmer_profile

		# Provide only client_op_id (no actual update fields)
		frappe.form_dict = {"client_op_id": "test-123"}

		result = update_farmer_profile("farmer-uuid-001")

		# Should return error response
		self.assertEqual(result["status"], "error")
		self.assertIn("no fields", result["message"].lower())

	# ========================================================================
	# SYNC PROCESSING
	# ========================================================================

	def test_process_farmer_update_success(self):
		"""Test successful sync processing updates cache."""
		frappe.set_user("Administrator")

		from da_services.services.farmer_updates import process_farmer_update

		# Create test event
		event = frappe.get_doc(
			{
				"doctype": "DA Integration Event",
				"event_type": "Farmer Update",
				"status": "Pending",
				"reference_id": "farmer-uuid-001",
				"payload": json.dumps(
					{
						"farmer_id": "farmer-uuid-001",
						"updates": {"phone": "999999999"},
						"expected_version": 1,
						"da_id": "DA-000001",
					}
				),
				"attempts": 0,
				"max_attempts": 5,
			}
		)
		event.insert(ignore_permissions=True, ignore_links=True)
		frappe.db.commit()

		# Process the event
		process_farmer_update(event.name)

		# Reload event
		event.reload()

		# Should be completed
		self.assertEqual(event.status, "Completed")
		self.assertIsNotNone(event.completed_at)

		# Cache should be updated
		farmer = frappe.get_doc("DA Farmer Reference", "farmer-uuid-001")
		self.assertEqual(farmer.phone, "999999999")

	def test_process_farmer_update_version_conflict(self):
		"""Test version conflict detection."""
		frappe.set_user("Administrator")

		from da_services.services.farmer_updates import process_farmer_update

		# Create event with wrong expected version
		event = frappe.get_doc(
			{
				"doctype": "DA Integration Event",
				"event_type": "Farmer Update",
				"status": "Pending",
				"reference_id": "farmer-uuid-001",
				"payload": json.dumps(
					{
						"farmer_id": "farmer-uuid-001",
						"updates": {"phone": "999999999"},
						"expected_version": 99,  # Wrong version!
						"da_id": "DA-000001",
					}
				),
				"attempts": 0,
				"max_attempts": 5,
			}
		)
		event.insert(ignore_permissions=True, ignore_links=True)
		frappe.db.commit()

		# Process the event
		process_farmer_update(event.name)

		# Reload event
		event.reload()

		# Should be marked as Conflict
		self.assertEqual(event.status, "Conflict")
		self.assertIn("version", event.last_error.lower())

	def test_retry_sweep_with_backoff_and_max_attempts(self):
		"""Test scheduler sweep processes pending updates with retry and eventual failure.

		Addresses PR review blocker A: Tests that process_pending_farmer_updates()
		correctly sweeps pending events, increments attempts, applies backoff, and
		marks events as Failed after MAX_RETRY_ATTEMPTS.

		Flow:
		1. Create Pending event with next_retry_at in the past
		2. Mock Farmer Registry client to always fail
		3. Call process_pending_farmer_updates() repeatedly
		4. Verify: attempts increment, next_retry_at updates with backoff, status becomes Failed
		"""
		frappe.set_user("Administrator")

		from datetime import datetime
		from unittest.mock import patch

		from da_services.services.farmer_updates import (
			MAX_RETRY_ATTEMPTS,
			process_pending_farmer_updates,
		)

		# Create event ready for retry (next_retry_at in the past)
		past_time = datetime(2026, 10, 14, 10, 0, 0)
		event = frappe.get_doc(
			{
				"doctype": "DA Integration Event",
				"event_type": "Farmer Update",
				"status": "Pending",
				"reference_id": "test-retry-sweep-001",
				"payload": json.dumps(
					{
						"farmer_id": "farmer-uuid-001",
						"updates": {"phone": "999999999"},
						"expected_version": 1,
						"da_id": "DA-000001",
					}
				),
				"attempts": 0,
				"max_attempts": MAX_RETRY_ATTEMPTS,
				"next_retry_at": past_time,  # Ready for immediate retry
			}
		)
		event.insert(ignore_permissions=True, ignore_links=True)
		frappe.db.commit()

		# Mock Farmer Registry client to always fail (transient error)
		from da_services.integrations.farmer_registry.client import FarmerRegistryError

		# Patch where the function is used, not where it's defined
		with patch("da_services.services.farmer_updates.get_farmer_registry_client") as mock_get_client:
			mock_client = mock_get_client.return_value
			mock_client.update_farmer.side_effect = FarmerRegistryError("Connection timeout")

			# Sweep 1: Should process and schedule retry
			process_pending_farmer_updates()
			event.reload()

			self.assertEqual(event.status, "Pending")  # Still retryable
			self.assertEqual(event.attempts, 1)
			self.assertIsNotNone(event.next_retry_at)
			self.assertIn("Connection timeout", event.last_error)

			# Sweep 2-4: Continue retrying
			for expected_attempt in range(2, MAX_RETRY_ATTEMPTS):
				# Set next_retry_at to past to trigger sweep
				event.db_set("next_retry_at", past_time, update_modified=False)
				frappe.db.commit()

				process_pending_farmer_updates()
				event.reload()

				self.assertEqual(event.status, "Pending")
				self.assertEqual(event.attempts, expected_attempt)

			# Final sweep: Should reach MAX_RETRY_ATTEMPTS and mark as Failed
			event.db_set("next_retry_at", past_time, update_modified=False)
			frappe.db.commit()

			process_pending_farmer_updates()
			event.reload()

			self.assertEqual(event.status, "Failed")
			self.assertEqual(event.attempts, MAX_RETRY_ATTEMPTS)
			self.assertIn("Max retries", event.last_error)

	def test_sweep_recovers_stale_processing_events(self):
		"""Test scheduler recovers events stuck in Processing state.

		Addresses PR review blocker A: Tests that _recover_stale_processing_farmer_updates()
		resets events that have been stuck in Processing for >10 minutes (e.g., server crash).
		"""
		frappe.set_user("Administrator")

		from datetime import datetime, timedelta
		from unittest.mock import patch

		from da_services.integrations.farmer_registry.client import FarmerRegistryError
		from da_services.services.farmer_updates import process_pending_farmer_updates

		# Create event stuck in Processing (modified 15 minutes ago)
		stale_time = datetime.now() - timedelta(minutes=15)
		event = frappe.get_doc(
			{
				"doctype": "DA Integration Event",
				"event_type": "Farmer Update",
				"status": "Processing",
				"reference_id": "test-stale-001",
				"payload": json.dumps(
					{
						"farmer_id": "farmer-uuid-001",
						"updates": {"phone": "999999999"},
					}
				),
				"attempts": 2,
				"max_attempts": 5,
			}
		)
		event.insert(ignore_permissions=True, ignore_links=True)
		frappe.db.commit()

		# Manually set modified time to the past (simulate stuck event)
		frappe.db.sql(
			"""
			UPDATE `tabDA Integration Event`
			SET modified = %s
			WHERE name = %s
			""",
			(stale_time, event.name),
		)
		frappe.db.commit()

		# Mock client to fail so event stays Pending after recovery
		with patch("da_services.services.farmer_updates.get_farmer_registry_client") as mock_get_client:
			mock_client = mock_get_client.return_value
			mock_client.update_farmer.side_effect = FarmerRegistryError("Connection timeout")

			# Run sweep
			process_pending_farmer_updates()

			# Reload event
			event.reload()

			# Should be reset to Pending with updated next_retry_at
			self.assertEqual(event.status, "Pending")
			self.assertIsNotNone(event.next_retry_at)

	def test_sweep_ignores_conflict_events(self):
		"""Test scheduler does not recover events in Conflict status.

		Conflict events require manual resolution, not automatic retry.
		"""
		frappe.set_user("Administrator")

		from datetime import datetime, timedelta

		from da_services.services.farmer_updates import process_pending_farmer_updates

		# Create event with Failed status first, then change to Conflict
		# (avoids doctype validation issues during test setup)
		stale_time = datetime.now() - timedelta(minutes=15)
		event = frappe.get_doc(
			{
				"doctype": "DA Integration Event",
				"event_type": "Farmer Update",
				"status": "Failed",  # Create as Failed first
				"reference_id": "test-conflict-001",
				"payload": json.dumps(
					{
						"farmer_id": "farmer-uuid-001",
						"updates": {"phone": "999999999"},
					}
				),
				"attempts": 1,
				"max_attempts": 5,
			}
		)
		event.insert(ignore_permissions=True, ignore_links=True)

		# Now set to Conflict using db_set (bypasses validation)
		event.db_set("status", "Conflict", update_modified=False)
		frappe.db.commit()

		# Manually set modified time to the past
		frappe.db.sql(
			"""
			UPDATE `tabDA Integration Event`
			SET modified = %s
			WHERE name = %s
			""",
			(stale_time, event.name),
		)
		frappe.db.commit()

		# Run sweep
		process_pending_farmer_updates()

		# Reload event
		event.reload()

		# Should remain in Conflict status (not recovered)
		self.assertEqual(event.status, "Conflict")

	# ========================================================================
	# STATUS CHECK
	# ========================================================================

	def test_get_farmer_update_status(self):
		"""Test getting pending update status for a farmer."""
		frappe.set_user(DA1)

		from da_services.api.v1.farmers_update import get_farmer_update_status

		# Create pending event
		event = frappe.get_doc(
			{
				"doctype": "DA Integration Event",
				"event_type": "Farmer Update",
				"status": "Pending",
				"reference_id": "farmer-uuid-001",
				"payload": json.dumps(
					{
						"farmer_id": "farmer-uuid-001",
						"updates": {"phone": "999999999"},
					}
				),
				"attempts": 1,
				"max_attempts": 5,
			}
		)
		event.insert(ignore_permissions=True, ignore_links=True)
		frappe.db.commit()

		# Get status
		result = get_farmer_update_status("farmer-uuid-001")

		self.assertEqual(result["status"], "success")
		self.assertEqual(result["data"]["farmer_id"], "farmer-uuid-001")
		self.assertEqual(result["data"]["total_pending"], 1)
		self.assertEqual(len(result["data"]["pending_updates"]), 1)

		pending = result["data"]["pending_updates"][0]
		self.assertEqual(pending["event_id"], event.name)
		self.assertEqual(pending["status"], "Pending")
		self.assertEqual(pending["attempts"], 1)
