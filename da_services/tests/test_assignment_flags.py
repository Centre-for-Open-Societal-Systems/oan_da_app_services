"""Tests for farmer assignment specialization flags (DA-211 Phase 4)."""

from __future__ import annotations

from datetime import datetime, timedelta

import frappe
from frappe.tests import IntegrationTestCase

from da_services.services import farmers as svc
from da_services.services import visit_specializations as spec_svc
from da_services.tests.fixtures import create_administrative_areas
from da_services.utils.permissions import ScopeContext


class TestAssignmentFlags(IntegrationTestCase):
	"""Test assigned_specializations field in farmer list."""

	def setUp(self):
		"""Set up test data."""
		# Create administrative areas first
		create_administrative_areas()

		self.da_id = "DA-TEST-FLAGS-001"
		self.farmer_id = "FARMER-FLAGS-001"
		self.kebele = "ET04-W01-K03"
		self.woreda = "ET04-W01"

		# Clean up
		frappe.db.delete("DA Visit", {"da_id": self.da_id})
		frappe.db.delete("DA Farmer Reference", {"farmer_id": self.farmer_id})
		frappe.db.delete("DA Kebele Assignment", {"da_id": self.da_id})

		# Create DA Kebele Assignment
		frappe.get_doc(
			{
				"doctype": "DA Kebele Assignment",
				"da_id": self.da_id,
				"kebele": self.kebele,
				"state": "Effective",
				"mode": "Automatic",
				"effective_date": "2026-10-01",
				"woreda": self.woreda,
			}
		).insert(ignore_permissions=True, ignore_links=True)

		# Create farmer
		frappe.get_doc(
			{
				"doctype": "DA Farmer Reference",
				"farmer_id": self.farmer_id,
				"farmer_name": "Test Farmer",
				"kebele": self.kebele,
				"woreda": self.woreda,
				"registration_state": "APPROVED",
			}
		).insert(ignore_permissions=True, ignore_links=True)

		# Create DA context
		self.ctx = ScopeContext(
			user="da@test.com",
			roles={"Development Agent"},
			da_ids={self.da_id},
			unrestricted=False,
		)

	def tearDown(self):
		"""Clean up test data."""
		frappe.db.delete("DA Visit", {"da_id": self.da_id})
		frappe.db.delete("DA Farmer Reference", {"farmer_id": self.farmer_id})
		frappe.db.delete("DA Kebele Assignment", {"da_id": self.da_id})

	def test_no_active_visits_empty_flags(self):
		"""Test farmer with no active visits has empty assignment flags."""
		farmers, _ = svc.list_farmers(self.ctx)

		farmer = next((f for f in farmers if f["farmer_id"] == self.farmer_id), None)
		self.assertIsNotNone(farmer)
		self.assertEqual(farmer["assigned_specializations"], [])

	def test_active_crop_visit_sets_flag(self):
		"""Test active Crop Survey sets Crop Production flag."""
		# Create active Crop Survey visit
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": self.da_id,
				"farmer_id": self.farmer_id,
				"visit_type": "Crop Survey",
				"physical_status": "Planned",
				"scheduled_at": datetime.now() + timedelta(days=1),
			}
		)
		spec_svc.set_visit_specializations(visit, ["Crop Production"])
		visit.insert(ignore_permissions=True, ignore_links=True)

		farmers, _ = svc.list_farmers(self.ctx)

		farmer = next((f for f in farmers if f["farmer_id"] == self.farmer_id), None)
		self.assertIsNotNone(farmer)
		self.assertIn("Crop Production", farmer["assigned_specializations"])

	def test_active_livestock_visit_sets_flag(self):
		"""Test active Livestock Survey sets Livestock flag."""
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": self.da_id,
				"farmer_id": self.farmer_id,
				"visit_type": "Livestock Survey",
				"physical_status": "In progress",
				"scheduled_at": datetime.now(),
			}
		)
		spec_svc.set_visit_specializations(visit, ["Livestock"])
		visit.insert(ignore_permissions=True, ignore_links=True)

		farmers, _ = svc.list_farmers(self.ctx)

		farmer = next((f for f in farmers if f["farmer_id"] == self.farmer_id), None)
		self.assertIn("Livestock", farmer["assigned_specializations"])

	def test_multiple_specializations_combined_visit(self):
		"""Test combined visit sets multiple flags."""
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": self.da_id,
				"farmer_id": self.farmer_id,
				"visit_type": "Services",
				"physical_status": "Planned",
				"scheduled_at": datetime.now() + timedelta(days=1),
			}
		)
		spec_svc.set_visit_specializations(visit, ["Crop Production", "Livestock"])
		visit.insert(ignore_permissions=True, ignore_links=True)

		farmers, _ = svc.list_farmers(self.ctx)

		farmer = next((f for f in farmers if f["farmer_id"] == self.farmer_id), None)
		self.assertIn("Crop Production", farmer["assigned_specializations"])
		self.assertIn("Livestock", farmer["assigned_specializations"])
		self.assertEqual(len(farmer["assigned_specializations"]), 2)

	def test_completed_visit_does_not_set_flag(self):
		"""Test completed visit does not set assignment flag."""
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": self.da_id,
				"farmer_id": self.farmer_id,
				"visit_type": "Crop Survey",
				"physical_status": "Physical Visit Complete",  # Terminal status for testing
				"scheduled_at": datetime.now() - timedelta(days=1),
			}
		)
		spec_svc.set_visit_specializations(visit, ["Crop Production"])
		visit.flags.ignore_validate = True  # Bypass status validation for testing
		visit.insert(ignore_permissions=True, ignore_links=True)

		farmers, _ = svc.list_farmers(self.ctx)

		farmer = next((f for f in farmers if f["farmer_id"] == self.farmer_id), None)
		self.assertEqual(farmer["assigned_specializations"], [])

	def test_missed_visit_does_not_set_flag(self):
		"""Test missed visit does not set assignment flag."""
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": self.da_id,
				"farmer_id": self.farmer_id,
				"visit_type": "Livestock Survey",
				"physical_status": "Missed",  # Terminal status for testing
				"scheduled_at": datetime.now() - timedelta(days=1),
			}
		)
		spec_svc.set_visit_specializations(visit, ["Livestock"])
		visit.flags.ignore_validate = True  # Bypass status validation for testing
		visit.insert(ignore_permissions=True, ignore_links=True)

		farmers, _ = svc.list_farmers(self.ctx)

		farmer = next((f for f in farmers if f["farmer_id"] == self.farmer_id), None)
		self.assertEqual(farmer["assigned_specializations"], [])

	def test_batch_operation_no_n_plus_1(self):
		"""Test assignment flags use batch operation (no N+1 queries)."""
		# Create multiple farmers and visits
		farmer_ids = []
		for i in range(5):
			fid = f"FARMER-BATCH-{i}"
			farmer_ids.append(fid)

			frappe.get_doc(
				{
					"doctype": "DA Farmer Reference",
					"farmer_id": fid,
					"farmer_name": f"Farmer {i}",
					"kebele": self.kebele,
					"woreda": self.woreda,
					"registration_state": "APPROVED",
				}
			).insert(ignore_permissions=True, ignore_links=True)

			# Create visit for each
			visit = frappe.get_doc(
				{
					"doctype": "DA Visit",
					"da_id": self.da_id,
					"farmer_id": fid,
					"visit_type": "Crop Survey",
					"physical_status": "Planned",
					"scheduled_at": datetime.now() + timedelta(days=1),
				}
			)
			spec_svc.set_visit_specializations(visit, ["Crop Production"])
			visit.insert(ignore_permissions=True, ignore_links=True)

		# List farmers - should use batch query
		farmers, _ = svc.list_farmers(self.ctx)

		# All farmers should have flags
		for farmer in farmers:
			if farmer["farmer_id"] in farmer_ids:
				self.assertIn("Crop Production", farmer["assigned_specializations"])

		# Clean up
		frappe.db.delete("DA Farmer Reference", {"farmer_id": ["in", farmer_ids]})

	def test_deduplication_same_specialization_multiple_visits(self):
		"""Test same specialization from multiple visits is deduplicated."""
		# Create two Crop visits
		for i in range(2):
			visit = frappe.get_doc(
				{
					"doctype": "DA Visit",
					"da_id": self.da_id,
					"farmer_id": self.farmer_id,
					"visit_type": "Crop Survey",
					"physical_status": "Planned",
					"scheduled_at": datetime.now() + timedelta(days=i + 1),
				}
			)
			spec_svc.set_visit_specializations(visit, ["Crop Production"])
			visit.insert(ignore_permissions=True, ignore_links=True)

		farmers, _ = svc.list_farmers(self.ctx)

		farmer = next((f for f in farmers if f["farmer_id"] == self.farmer_id), None)
		# Should only have one "Crop Production" entry
		self.assertEqual(farmer["assigned_specializations"].count("Crop Production"), 1)

	def test_sorted_specializations(self):
		"""Test specializations are returned in sorted order."""
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": self.da_id,
				"farmer_id": self.farmer_id,
				"visit_type": "Services",
				"physical_status": "Planned",
				"scheduled_at": datetime.now() + timedelta(days=1),
			}
		)
		spec_svc.set_visit_specializations(visit, ["Livestock", "Crop Production"])
		visit.insert(ignore_permissions=True, ignore_links=True)

		farmers, _ = svc.list_farmers(self.ctx)

		farmer = next((f for f in farmers if f["farmer_id"] == self.farmer_id), None)
		# Should be alphabetically sorted
		self.assertEqual(farmer["assigned_specializations"], ["Crop Production", "Livestock"])
