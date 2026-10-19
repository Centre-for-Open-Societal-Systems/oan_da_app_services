"""Tests for visit specialization derivation (DA-211 Phase 3)."""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from da_services.services import visit_specializations as svc
from da_services.integrations.da_registry.mock import MockDARegistryClient


class TestVisitSpecializations(IntegrationTestCase):
	"""Test visit specialization derivation logic."""

	def setUp(self):
		"""Set up test data."""
		self.da_crop = "DA-000001"  # Crop production (from mock)
		self.da_livestock = "DA-000002"  # Livestock (from mock)

		# Reset DA Registry mock
		MockDARegistryClient.reset()

	def test_crop_survey_specialization(self):
		"""Test Crop Survey maps to Crop Production."""
		specs = svc.derive_visit_specializations("Crop Survey", self.da_crop)

		self.assertEqual(len(specs), 1)
		self.assertIn("Crop Production", specs)

	def test_livestock_survey_specialization(self):
		"""Test Livestock Survey maps to Livestock."""
		specs = svc.derive_visit_specializations("Livestock Survey", self.da_livestock)

		self.assertEqual(len(specs), 1)
		self.assertIn("Livestock", specs)

	def test_services_uses_da_specialization(self):
		"""Test Services visit uses DA's specialization from Registry."""
		specs = svc.derive_visit_specializations("Services", self.da_crop)

		self.assertEqual(len(specs), 1)
		self.assertIn("Crop Production", specs)  # DA-000001 has Crop production

	def test_advisory_uses_da_specialization(self):
		"""Test Advisory visit uses DA's specialization."""
		specs = svc.derive_visit_specializations("Advisory", self.da_livestock)

		self.assertEqual(len(specs), 1)
		self.assertIn("Livestock", specs)  # DA-000002 has Livestock

	def test_followup_uses_da_specialization(self):
		"""Test Follow-up visit uses DA's specialization."""
		specs = svc.derive_visit_specializations("Follow-up", self.da_crop)

		self.assertEqual(len(specs), 1)
		self.assertIn("Crop Production", specs)

	def test_normalize_specialization_crop(self):
		"""Test specialization normalization for crop variations."""
		self.assertEqual(svc.normalize_specialization("crop production"), "Crop Production")
		self.assertEqual(svc.normalize_specialization("Crop"), "Crop Production")
		self.assertEqual(svc.normalize_specialization("CROPS"), "Crop Production")

	def test_normalize_specialization_livestock(self):
		"""Test specialization normalization for livestock variations."""
		self.assertEqual(svc.normalize_specialization("livestock"), "Livestock")
		self.assertEqual(svc.normalize_specialization("LIVESTOCK"), "Livestock")
		self.assertEqual(svc.normalize_specialization("Animal Husbandry"), "Livestock")

	def test_normalize_specialization_natural_resources(self):
		"""Test specialization normalization for NRM."""
		self.assertEqual(svc.normalize_specialization("Natural Resources"), "Natural Resources")
		self.assertEqual(svc.normalize_specialization("NRM"), "Natural Resources")

	def test_normalize_specialization_unknown(self):
		"""Test unknown specialization maps to Other."""
		self.assertEqual(svc.normalize_specialization("Unknown Spec"), "Other")
		self.assertEqual(svc.normalize_specialization(""), "Other")
		self.assertEqual(svc.normalize_specialization(None), "Other")

	def test_set_visit_specializations(self):
		"""Test setting specializations on visit document."""
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": self.da_crop,
				"farmer_id": "FARMER-001",
				"visit_type": "Crop Survey",
				"scheduled_at": "2026-10-20 10:00:00",
			}
		)

		svc.set_visit_specializations(visit, ["Crop Production", "Livestock"])

		self.assertEqual(len(visit.specializations), 2)
		spec_values = [r.specialization for r in visit.specializations]
		self.assertIn("Crop Production", spec_values)
		self.assertIn("Livestock", spec_values)

	def test_set_visit_specializations_deduplication(self):
		"""Test specializations are deduplicated."""
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": self.da_crop,
				"farmer_id": "FARMER-001",
				"visit_type": "Crop Survey",
				"scheduled_at": "2026-10-20 10:00:00",
			}
		)

		# Pass duplicate specializations
		svc.set_visit_specializations(visit, ["Crop Production", "Crop Production", "Livestock"])

		self.assertEqual(len(visit.specializations), 2)
		spec_values = [r.specialization for r in visit.specializations]
		self.assertIn("Crop Production", spec_values)
		self.assertIn("Livestock", spec_values)

	def test_set_visit_specializations_clears_existing(self):
		"""Test setting specializations clears existing ones."""
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": self.da_crop,
				"farmer_id": "FARMER-001",
				"visit_type": "Crop Survey",
				"scheduled_at": "2026-10-20 10:00:00",
			}
		)

		# Set initial specializations
		svc.set_visit_specializations(visit, ["Crop Production"])
		self.assertEqual(len(visit.specializations), 1)

		# Set new specializations (should clear old ones)
		svc.set_visit_specializations(visit, ["Livestock"])
		self.assertEqual(len(visit.specializations), 1)
		self.assertEqual(visit.specializations[0].specialization, "Livestock")

	def test_da_not_found_fallback(self):
		"""Test fallback when DA not found in Registry."""
		specs = svc.derive_visit_specializations("Services", "DA-NOTFOUND")

		# Should fallback to "Services"
		self.assertEqual(len(specs), 1)
		self.assertIn("Services", specs)

	def test_empty_visit_type(self):
		"""Test empty visit type returns empty list."""
		specs = svc.derive_visit_specializations("", self.da_crop)

		self.assertEqual(len(specs), 0)

	def test_get_visit_specializations(self):
		"""Test retrieving specializations from saved visit."""
		# Create and save visit
		visit = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": self.da_crop,
				"farmer_id": "FARMER-001",
				"visit_type": "Crop Survey",
				"scheduled_at": "2026-10-20 10:00:00",
			}
		)
		svc.set_visit_specializations(visit, ["Crop Production"])
		visit.insert(ignore_permissions=True)

		# Retrieve specializations
		specs = svc.get_visit_specializations(visit.name)

		self.assertEqual(len(specs), 1)
		self.assertIn("Crop Production", specs)

		# Clean up
		frappe.delete_doc("DA Visit", visit.name, force=True)
