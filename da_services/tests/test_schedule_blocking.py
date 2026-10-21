"""Tests for specialization-aware schedule blocking (DA-211 Phase 5)."""

from __future__ import annotations

from datetime import datetime, timedelta

import frappe
from frappe.tests import IntegrationTestCase

from da_services.services import visit_specializations as spec_svc
from da_services.services import visits as svc
from da_services.utils.permissions import ScopeContext


class TestScheduleBlocking(IntegrationTestCase):
	"""Test specialization-based visit scheduling conflicts."""

	def setUp(self):
		"""Set up test data."""
		self.da_crop = "DA-BLOCK-CROP"
		self.da_livestock = "DA-BLOCK-LIVESTOCK"
		self.farmer_id = "FARMER-BLOCK-001"

		# Clean up
		frappe.db.delete("DA Visit", {"farmer_id": self.farmer_id})

		# Create DA context
		self.ctx_crop = ScopeContext(
			user="da-crop@test.com",
			roles={"Development Agent"},
			da_ids={self.da_crop},
			unrestricted=False,
		)

		self.ctx_livestock = ScopeContext(
			user="da-livestock@test.com",
			roles={"Development Agent"},
			da_ids={self.da_livestock},
			unrestricted=False,
		)

	def tearDown(self):
		"""Clean up test data."""
		frappe.db.delete("DA Visit", {"farmer_id": self.farmer_id})

	def test_no_conflict_first_visit(self):
		"""Test first visit has no conflict."""
		result = svc.plan_visit(
			farmer_id=self.farmer_id,
			scheduled_at=datetime.now() + timedelta(days=1),
			visit_type="Crop Survey",
			da_id=self.da_crop,
			ctx=self.ctx_crop,
		)

		self.assertIsNotNone(result)
		self.assertEqual(result["visit_type"], "Crop Survey")

	def test_same_specialization_blocks(self):
		"""Test same specialization blocks scheduling."""
		# Create first Crop visit
		svc.plan_visit(
			farmer_id=self.farmer_id,
			scheduled_at=datetime.now() + timedelta(days=1),
			visit_type="Crop Survey",
			da_id=self.da_crop,
			ctx=self.ctx_crop,
		)

		# Try to create second Crop visit (should fail)
		with self.assertRaises(frappe.ValidationError) as cm:
			svc.plan_visit(
				farmer_id=self.farmer_id,
				scheduled_at=datetime.now() + timedelta(days=2),
				visit_type="Crop Survey",
				da_id=self.da_crop,
				ctx=self.ctx_crop,
			)

		self.assertIn("Crop Production", str(cm.exception))

	def test_different_specialization_allowed(self):
		"""Test different specialization is allowed."""
		# Create Crop visit
		svc.plan_visit(
			farmer_id=self.farmer_id,
			scheduled_at=datetime.now() + timedelta(days=1),
			visit_type="Crop Survey",
			da_id=self.da_crop,
			ctx=self.ctx_crop,
		)

		# Create Livestock visit (should succeed)
		result = svc.plan_visit(
			farmer_id=self.farmer_id,
			scheduled_at=datetime.now() + timedelta(days=2),
			visit_type="Livestock Survey",
			da_id=self.da_livestock,
			ctx=self.ctx_livestock,
		)

		self.assertIsNotNone(result)
		self.assertEqual(result["visit_type"], "Livestock Survey")

	def _insert_terminal_visit(self, status: str):
		"""Insert a visit already in a terminal status.

		A new visit may only start as Planned, Confirmed, or In progress.
		These cases need the stored status itself, so validation is skipped.
		"""
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": self.da_crop,
				"farmer_id": self.farmer_id,
				"visit_type": "Crop Survey",
				"physical_status": status,
				"scheduled_at": datetime.now() - timedelta(days=1),
			}
		)
		spec_svc.set_visit_specializations(visit, ["Crop Production"])
		visit.flags.ignore_validate = True
		visit.insert(ignore_permissions=True, ignore_links=True)
		return visit

	def test_completed_visit_does_not_block(self):
		"""Test completed visit does not block new visit."""
		self._insert_terminal_visit("Physical Visit Complete")

		# Should be able to schedule new Crop visit
		result = svc.plan_visit(
			farmer_id=self.farmer_id,
			scheduled_at=datetime.now() + timedelta(days=1),
			visit_type="Crop Survey",
			da_id=self.da_crop,
			ctx=self.ctx_crop,
		)

		self.assertIsNotNone(result)

	def test_missed_visit_does_not_block(self):
		"""Test missed visit does not block new visit."""
		self._insert_terminal_visit("Missed")

		result = svc.plan_visit(
			farmer_id=self.farmer_id,
			scheduled_at=datetime.now() + timedelta(days=1),
			visit_type="Crop Survey",
			da_id=self.da_crop,
			ctx=self.ctx_crop,
		)

		self.assertIsNotNone(result)

	def test_cancelled_visit_does_not_block(self):
		"""Test cancelled visit does not block."""
		self._insert_terminal_visit("Cancelled")

		result = svc.plan_visit(
			farmer_id=self.farmer_id,
			scheduled_at=datetime.now() + timedelta(days=1),
			visit_type="Crop Survey",
			da_id=self.da_crop,
			ctx=self.ctx_crop,
		)

		self.assertIsNotNone(result)

	def test_in_progress_visit_blocks(self):
		"""Test in-progress visit blocks scheduling."""
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": self.da_crop,
				"farmer_id": self.farmer_id,
				"visit_type": "Crop Survey",
				"physical_status": "In progress",
				"scheduled_at": datetime.now(),
			}
		)
		spec_svc.set_visit_specializations(visit, ["Crop Production"])
		visit.insert(ignore_permissions=True, ignore_links=True)

		with self.assertRaises(frappe.ValidationError) as cm:
			svc.plan_visit(
				farmer_id=self.farmer_id,
				scheduled_at=datetime.now() + timedelta(days=1),
				visit_type="Crop Survey",
				da_id=self.da_crop,
				ctx=self.ctx_crop,
			)

		self.assertIn("Crop Production", str(cm.exception))

	def test_physical_visit_complete_does_not_block(self):
		"""A finished physical visit does not block the next survey."""
		self._insert_terminal_visit("Physical Visit Complete")

		result = svc.plan_visit(
			farmer_id=self.farmer_id,
			scheduled_at=datetime.now() + timedelta(days=1),
			visit_type="Crop Survey",
			da_id=self.da_crop,
			ctx=self.ctx_crop,
		)

		self.assertIsNotNone(result)

	def test_combined_visit_blocks_all_specializations(self):
		"""Test combined visit blocks all its specializations."""
		# Create combined Crop + Livestock visit
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": self.da_crop,
				"farmer_id": self.farmer_id,
				"visit_type": "Services",
				"physical_status": "Planned",
				"scheduled_at": datetime.now() + timedelta(days=1),
			}
		)
		spec_svc.set_visit_specializations(visit, ["Crop Production", "Livestock"])
		visit.insert(ignore_permissions=True, ignore_links=True)

		# Try Crop visit (should fail)
		with self.assertRaises(frappe.ValidationError) as cm:
			svc.plan_visit(
				farmer_id=self.farmer_id,
				scheduled_at=datetime.now() + timedelta(days=2),
				visit_type="Crop Survey",
				da_id=self.da_crop,
				ctx=self.ctx_crop,
			)

		self.assertIn("Crop Production", str(cm.exception))

		# Try Livestock visit (should also fail)
		with self.assertRaises(frappe.ValidationError) as cm:
			svc.plan_visit(
				farmer_id=self.farmer_id,
				scheduled_at=datetime.now() + timedelta(days=2),
				visit_type="Livestock Survey",
				da_id=self.da_livestock,
				ctx=self.ctx_livestock,
			)

		self.assertIn("Livestock", str(cm.exception))

	def test_idempotent_retry_with_conflict(self):
		"""Test idempotent retry returns existing visit despite conflict."""
		client_op_id = "test-op-unique-123"

		# Create first visit
		result1 = svc.plan_visit(
			farmer_id=self.farmer_id,
			scheduled_at=datetime.now() + timedelta(days=1),
			visit_type="Crop Survey",
			da_id=self.da_crop,
			client_op_id=client_op_id,
			ctx=self.ctx_crop,
		)

		# Retry with same client_op_id (should return existing, not error)
		result2 = svc.plan_visit(
			farmer_id=self.farmer_id,
			scheduled_at=datetime.now() + timedelta(days=2),
			visit_type="Crop Survey",
			da_id=self.da_crop,
			client_op_id=client_op_id,
			ctx=self.ctx_crop,
		)

		self.assertEqual(result1["name"], result2["name"])

	def test_cross_da_blocking(self):
		"""Test blocking works across different DAs."""
		# DA-1 creates Crop visit
		svc.plan_visit(
			farmer_id=self.farmer_id,
			scheduled_at=datetime.now() + timedelta(days=1),
			visit_type="Crop Survey",
			da_id=self.da_crop,
			ctx=self.ctx_crop,
		)

		# DA-2 tries to create Crop visit (should fail)
		ctx_other = ScopeContext(
			user="da-other@test.com",
			roles={"Development Agent"},
			da_ids={"DA-OTHER-CROP"},
			unrestricted=False,
		)

		with self.assertRaises(frappe.ValidationError) as cm:
			svc.plan_visit(
				farmer_id=self.farmer_id,
				scheduled_at=datetime.now() + timedelta(days=2),
				visit_type="Crop Survey",
				da_id="DA-OTHER-CROP",
				ctx=ctx_other,
			)

		self.assertIn("Crop Production", str(cm.exception))

	def test_conflict_error_details(self):
		"""Test conflict error includes helpful details."""
		# Create blocking visit
		svc.plan_visit(
			farmer_id=self.farmer_id,
			scheduled_at=datetime.now() + timedelta(days=1),
			visit_type="Livestock Survey",
			da_id=self.da_livestock,
			ctx=self.ctx_livestock,
		)

		# Try conflicting visit
		with self.assertRaises(frappe.ValidationError) as cm:
			svc.plan_visit(
				farmer_id=self.farmer_id,
				scheduled_at=datetime.now() + timedelta(days=2),
				visit_type="Livestock Survey",
				da_id=self.da_livestock,
				ctx=self.ctx_livestock,
			)

		error_msg = str(cm.exception)
		self.assertIn("Livestock", error_msg)
		self.assertIn("active", error_msg.lower())
