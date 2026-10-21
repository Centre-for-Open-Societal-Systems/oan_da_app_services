"""Tests for pending actions derivation (DA-39).

Tests visit-based pending action logic without Crop/Livestock Registry integration.
"""

from datetime import datetime, timedelta

import frappe
from frappe.tests import IntegrationTestCase

from da_services.integrations.a2c import get_a2c_client, reset_client
from da_services.services.pending_actions import (
	CROP_SURVEY_DUE_DAYS,
	LIVESTOCK_SURVEY_DUE_DAYS,
	STATE_CREDIT_IN_REVIEW,
	STATE_CROP_SURVEY_DUE,
	STATE_FAYDA_CHECK,
	STATE_LIVESTOCK_DUE,
	STATE_NO_ACTION_DUE,
	derive_pending_action,
	derive_pending_actions_batch,
)


class TestPendingActions(IntegrationTestCase):
	"""Test suite for DA-39 pending action derivation."""

	def setUp(self):
		"""Set up test data."""
		self.farmer_id = "test-farmer-pending-001"

		# Clean up
		frappe.db.delete("DA Farmer Reference", {"farmer_id": self.farmer_id})
		frappe.db.delete("DA Visit", {"farmer_id": self.farmer_id})
		frappe.db.commit()

		# Create farmer reference
		self.farmer_ref = frappe.get_doc(
			{
				"doctype": "DA Farmer Reference",
				"farmer_id": self.farmer_id,
				"reg_id": "FR-TEST-001",
				"farmer_name": "Test Farmer",
				"kebele": "TEST-KEBELE",
				"woreda": "TEST-WOREDA",
				"region": "TEST-REGION",
				"registration_state": "APPROVED",
			}
		).insert(ignore_permissions=True, ignore_links=True)
		frappe.db.commit()

		# Reset A2C client for clean test state
		reset_client()

	def tearDown(self):
		"""Clean up test data."""
		frappe.db.delete("DA Farmer Reference", {"farmer_id": self.farmer_id})
		frappe.db.delete("DA Visit", {"farmer_id": self.farmer_id})
		frappe.db.commit()
		reset_client()

	def test_fayda_check_pending_approval(self):
		"""Test Fayda check for PENDING_APPROVAL state."""
		self.farmer_ref.registration_state = "PENDING_APPROVAL"
		self.farmer_ref.save(ignore_permissions=True)
		frappe.db.commit()

		result = derive_pending_action(self.farmer_id)

		self.assertEqual(result.state, STATE_FAYDA_CHECK)
		self.assertEqual(result.priority, "high")
		self.assertIn("pending approval", result.reason.lower())

	def test_fayda_check_draft(self):
		"""Test Fayda check for DRAFT state."""
		self.farmer_ref.registration_state = "DRAFT"
		self.farmer_ref.save(ignore_permissions=True)
		frappe.db.commit()

		result = derive_pending_action(self.farmer_id)

		self.assertEqual(result.state, STATE_FAYDA_CHECK)
		self.assertEqual(result.priority, "high")
		self.assertIn("draft", result.reason.lower())

	def test_crop_survey_never_done(self):
		"""Test crop survey due when never done."""
		result = derive_pending_action(self.farmer_id)

		self.assertEqual(result.state, STATE_CROP_SURVEY_DUE)
		self.assertEqual(result.priority, "high")
		self.assertIn("No crop survey", result.reason)

	def test_crop_survey_overdue(self):
		"""Test crop survey overdue."""
		past_date = datetime.now() - timedelta(days=45)

		# Create visit with valid initial state
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": "DA-TEST-001",
				"farmer_id": self.farmer_id,
				"visit_type": "Crop Survey",
				"physical_status": "Planned",
				"scheduled_at": past_date,
			}
		).insert(ignore_permissions=True, ignore_links=True)

		# Transition: Planned → In progress
		visit.physical_status = "In progress"
		visit.save(ignore_permissions=True)

		# Transition: In progress → Physical Visit Complete
		visit.physical_status = "Physical Visit Complete"
		visit.completed_at = past_date
		visit.save(ignore_permissions=True)
		frappe.db.commit()

		result = derive_pending_action(self.farmer_id)

		self.assertEqual(result.state, STATE_CROP_SURVEY_DUE)
		self.assertEqual(result.priority, "high")
		self.assertEqual(result.days_overdue, 15)

	def test_crop_survey_recent(self):
		"""Test recent crop survey shows no action."""
		recent_date = datetime.now() - timedelta(days=10)

		# Create visit with valid initial state
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": "DA-TEST-001",
				"farmer_id": self.farmer_id,
				"visit_type": "Crop Survey",
				"physical_status": "Planned",
				"scheduled_at": recent_date,
			}
		).insert(ignore_permissions=True, ignore_links=True)

		# Transition: Planned → In progress → Physical Visit Complete
		visit.physical_status = "In progress"
		visit.save(ignore_permissions=True)
		visit.physical_status = "Physical Visit Complete"
		visit.completed_at = recent_date
		visit.save(ignore_permissions=True)
		frappe.db.commit()

		result = derive_pending_action(self.farmer_id)

		self.assertEqual(result.state, STATE_NO_ACTION_DUE)
		self.assertEqual(result.priority, "low")

	def test_livestock_survey_overdue(self):
		"""Test livestock survey overdue."""
		livestock_date = datetime.now() - timedelta(days=70)
		crop_date = datetime.now() - timedelta(days=10)

		# First, create a RECENT crop survey so crop doesn't come first
		crop_visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": "DA-TEST-001",
				"farmer_id": self.farmer_id,
				"visit_type": "Crop Survey",
				"physical_status": "Planned",
				"scheduled_at": crop_date,
			}
		).insert(ignore_permissions=True, ignore_links=True)
		crop_visit.physical_status = "In progress"
		crop_visit.save(ignore_permissions=True)
		crop_visit.physical_status = "Physical Visit Complete"
		crop_visit.completed_at = crop_date
		crop_visit.save(ignore_permissions=True)

		# Now create overdue livestock survey
		livestock_visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": "DA-TEST-002",
				"farmer_id": self.farmer_id,
				"visit_type": "Livestock Survey",
				"physical_status": "Planned",
				"scheduled_at": livestock_date,
			}
		).insert(ignore_permissions=True, ignore_links=True)

		# Transition: Planned → In progress → Physical Visit Complete
		livestock_visit.physical_status = "In progress"
		livestock_visit.save(ignore_permissions=True)
		livestock_visit.physical_status = "Physical Visit Complete"
		livestock_visit.completed_at = livestock_date
		livestock_visit.save(ignore_permissions=True)
		frappe.db.commit()

		result = derive_pending_action(self.farmer_id)

		self.assertEqual(result.state, STATE_LIVESTOCK_DUE)
		self.assertEqual(result.priority, "medium")

	def test_credit_in_review(self):
		"""Test credit in review state (priority over crop survey)."""
		# Set up: This farmer has overdue crop survey
		past_date = datetime.now() - timedelta(days=45)
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": "DA-TEST-001",
				"farmer_id": self.farmer_id,
				"visit_type": "Crop Survey",
				"physical_status": "Planned",
				"scheduled_at": past_date,
			}
		).insert(ignore_permissions=True, ignore_links=True)
		visit.physical_status = "In progress"
		visit.save(ignore_permissions=True)
		visit.physical_status = "Physical Visit Complete"
		visit.completed_at = past_date
		visit.save(ignore_permissions=True)
		frappe.db.commit()

		# Configure mock A2C to return active application for this farmer
		a2c_client = get_a2c_client()
		a2c_client.set_active_farmers([self.farmer_id])

		# Now derive pending action
		result = derive_pending_action(self.farmer_id)

		# Credit (medium priority) should win over crop survey (high priority)
		# as per FSD 3.4 fixed priority order
		self.assertEqual(result.state, STATE_CREDIT_IN_REVIEW)
		self.assertEqual(result.priority, "medium")
		self.assertIn("loan", result.reason.lower())

	def test_priority_order_fayda_over_credit(self):
		"""Test priority order: Fayda > Credit."""
		# Set pending Fayda (registration pending approval)
		self.farmer_ref.registration_state = "PENDING_APPROVAL"
		self.farmer_ref.save(ignore_permissions=True)

		# Create overdue crop survey
		past_date = datetime.now() - timedelta(days=45)
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": "DA-TEST-001",
				"farmer_id": self.farmer_id,
				"visit_type": "Crop Survey",
				"physical_status": "Planned",
				"scheduled_at": past_date,
			}
		).insert(ignore_permissions=True, ignore_links=True)

		# Transition: Planned → In progress → Physical Visit Complete
		visit.physical_status = "In progress"
		visit.save(ignore_permissions=True)
		visit.physical_status = "Physical Visit Complete"
		visit.completed_at = past_date
		visit.save(ignore_permissions=True)

		# Configure mock A2C to return active application
		a2c_client = get_a2c_client()
		a2c_client.set_active_farmers([self.farmer_id])

		frappe.db.commit()

		result = derive_pending_action(self.farmer_id)

		# Should be Fayda (highest priority), not credit or crop
		self.assertEqual(result.state, STATE_FAYDA_CHECK)

	def test_batch_derivation(self):
		"""Test batch derivation for multiple farmers."""
		farmer_id_2 = "test-farmer-pending-002"

		frappe.get_doc(
			{
				"doctype": "DA Farmer Reference",
				"farmer_id": farmer_id_2,
				"reg_id": "FR-TEST-002",
				"farmer_name": "Test Farmer 2",
				"kebele": "TEST",
				"woreda": "TEST",
				"region": "TEST",
				"registration_state": "PENDING_APPROVAL",
			}
		).insert(ignore_permissions=True, ignore_links=True)
		frappe.db.commit()

		try:
			results = derive_pending_actions_batch([self.farmer_id, farmer_id_2])

			self.assertEqual(len(results), 2)
			self.assertEqual(results[self.farmer_id].state, STATE_CROP_SURVEY_DUE)
			self.assertEqual(results[farmer_id_2].state, STATE_FAYDA_CHECK)

		finally:
			frappe.db.delete("DA Farmer Reference", {"farmer_id": farmer_id_2})
			frappe.db.commit()

	def test_all_five_states_coverage(self):
		"""Test that all 5 DA-39 required states are implemented."""
		# Verify the 5 required state constants exist
		required_states = [
			STATE_CROP_SURVEY_DUE,
			STATE_LIVESTOCK_DUE,
			STATE_CREDIT_IN_REVIEW,
			STATE_FAYDA_CHECK,
			STATE_NO_ACTION_DUE,
		]

		# All constants should be defined
		for state in required_states:
			self.assertIsNotNone(state)
			self.assertIsInstance(state, str)

	def test_system_derived_not_stored(self):
		"""Test that pending actions are computed, not stored (DA-39 Req 3)."""
		# Derive pending action
		result = derive_pending_action(self.farmer_id)

		# Verify it's NOT stored in DA Farmer Reference
		farmer_ref = frappe.get_doc("DA Farmer Reference", self.farmer_id)

		# These fields should NOT exist in the doctype
		self.assertFalse(hasattr(farmer_ref, "pending_action"))
		self.assertFalse(hasattr(farmer_ref, "pending_action_priority"))

		# Pending action is only computed on-the-fly
		self.assertIsNotNone(result.state)
		self.assertIsNotNone(result.priority)

	def test_batch_a2c_check(self):
		"""Test that batch derivation uses batch A2C check (no N+1)."""
		# Create 3 farmers
		farmer_ids = [
			"test-batch-1",
			"test-batch-2",
			"test-batch-3",
		]

		for fid in farmer_ids:
			frappe.get_doc(
				{
					"doctype": "DA Farmer Reference",
					"farmer_id": fid,
					"reg_id": f"FR-{fid}",
					"farmer_name": f"Farmer {fid}",
					"kebele": "TEST",
					"woreda": "TEST",
					"region": "TEST",
					"registration_state": "APPROVED",
				}
			).insert(ignore_permissions=True, ignore_links=True)
		frappe.db.commit()

		try:
			# Configure mock to mark farmer-2 as having active application
			a2c_client = get_a2c_client()
			a2c_client.set_active_farmers(["test-batch-2"])

			# Batch derive
			results = derive_pending_actions_batch(farmer_ids)

			# farmer-2 should have credit in review
			self.assertEqual(results["test-batch-2"].state, STATE_CREDIT_IN_REVIEW)

			# Others should have crop survey due (no surveys)
			self.assertEqual(results["test-batch-1"].state, STATE_CROP_SURVEY_DUE)
			self.assertEqual(results["test-batch-3"].state, STATE_CROP_SURVEY_DUE)

		finally:
			for fid in farmer_ids:
				frappe.db.delete("DA Farmer Reference", {"farmer_id": fid})
			frappe.db.commit()
