"""Tests for Kebele-based farmer scoping (DA-211 Phase 2)."""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from da_services.services import farmers as svc
from da_services.tests.fixtures import create_administrative_areas
from da_services.utils.permissions import ScopeContext


class TestKebeleFarmerScoping(IntegrationTestCase):
	"""Test Kebele-based farmer visibility."""

	def setUp(self):
		"""Set up test data."""
		# Create administrative areas first
		create_administrative_areas()

		self.da_id = "DA-TEST-SCOPE-001"
		self.da_id_other = "DA-TEST-SCOPE-002"
		self.kebele_a = "ET04-W01-K03"
		self.kebele_b = "ET04-W01-K04"
		self.woreda = "ET04-W01"

		# Clean up
		frappe.db.delete("DA Farmer Reference", {})
		frappe.db.delete("DA Kebele Assignment", {"da_id": ["in", [self.da_id, self.da_id_other]]})

		# Create DA Kebele Assignment for Kebele A
		frappe.get_doc(
			{
				"doctype": "DA Kebele Assignment",
				"da_id": self.da_id,
				"kebele": self.kebele_a,
				"state": "Effective",
				"mode": "Automatic",
				"effective_date": "2026-10-01",
				"woreda": self.woreda,
			}
		).insert(ignore_permissions=True, ignore_links=True)

		# Create DA Kebele Assignment for Kebele B (different DA)
		frappe.get_doc(
			{
				"doctype": "DA Kebele Assignment",
				"da_id": self.da_id_other,
				"kebele": self.kebele_b,
				"state": "Effective",
				"mode": "Automatic",
				"effective_date": "2026-10-01",
				"woreda": self.woreda,
			}
		).insert(ignore_permissions=True, ignore_links=True)

		# Create farmers in different Kebeles
		self.farmer_kebele_a_1 = self._create_farmer("FARMER-A-001", self.kebele_a)
		self.farmer_kebele_a_2 = self._create_farmer("FARMER-A-002", self.kebele_a)
		self.farmer_kebele_b_1 = self._create_farmer("FARMER-B-001", self.kebele_b)

		# Create DA context
		self.ctx_da = ScopeContext(
			user="da@test.com",
			roles={"Development Agent"},
			da_ids={self.da_id},
			unrestricted=False,
		)

	def tearDown(self):
		"""Clean up test data."""
		frappe.db.delete("DA Farmer Reference", {})
		frappe.db.delete("DA Kebele Assignment", {"da_id": ["in", [self.da_id, self.da_id_other]]})

	def _create_farmer(self, farmer_id: str, kebele: str) -> str:
		"""Create test farmer."""
		# Note: In production, this would be in Farmer Registry
		# For testing, we create DA Farmer Reference entries
		frappe.get_doc(
			{
				"doctype": "DA Farmer Reference",
				"farmer_id": farmer_id,
				"farmer_name": f"Test Farmer {farmer_id}",
				"kebele": kebele,
				"woreda": self.woreda,
				"registration_state": "APPROVED",
			}
		).insert(ignore_permissions=True, ignore_links=True)
		return farmer_id

	def test_da_sees_farmers_in_assigned_kebele(self):
		"""Test DA sees all farmers in assigned Kebele."""
		farmers, total = svc.list_farmers(self.ctx_da)

		farmer_ids = [f["farmer_id"] for f in farmers]

		# Should see both farmers in Kebele A
		self.assertIn(self.farmer_kebele_a_1, farmer_ids)
		self.assertIn(self.farmer_kebele_a_2, farmer_ids)

		# Should NOT see farmer in Kebele B
		self.assertNotIn(self.farmer_kebele_b_1, farmer_ids)

		self.assertEqual(total, 2)

	def test_da_does_not_see_farmers_in_other_kebele(self):
		"""Test DA cannot see farmers outside assigned Kebele."""
		farmers, total = svc.list_farmers(self.ctx_da, kebele=self.kebele_b)

		# Even with explicit filter, DA should not see Kebele B farmers
		self.assertEqual(len(farmers), 0)
		self.assertEqual(total, 0)

	def test_da_no_assignment_sees_empty_list(self):
		"""Test DA with no Kebele assignment sees empty list."""
		# Create DA with no assignment
		ctx_no_assignment = ScopeContext(
			user="da-no-assign@test.com",
			roles={"Development Agent"},
			da_ids={"DA-NO-ASSIGN"},
			unrestricted=False,
		)

		farmers, total = svc.list_farmers(ctx_no_assignment)

		self.assertEqual(len(farmers), 0)
		self.assertEqual(total, 0)

	def test_supervisor_sees_all_farmers_in_woreda(self):
		"""Test Supervisor sees all farmers in their Woreda."""
		ctx_supervisor = ScopeContext(
			user="supervisor@test.com",
			roles={"Supervisor"},
			woreda_scopes={self.woreda},
			unrestricted=False,
		)

		farmers, total = svc.list_farmers(ctx_supervisor)

		farmer_ids = [f["farmer_id"] for f in farmers]

		# Should see farmers from both Kebeles in the Woreda
		self.assertIn(self.farmer_kebele_a_1, farmer_ids)
		self.assertIn(self.farmer_kebele_a_2, farmer_ids)
		self.assertIn(self.farmer_kebele_b_1, farmer_ids)

		self.assertEqual(total, 3)

	def test_can_edit_farmer_in_assigned_kebele(self):
		"""Test DA can edit farmers in assigned Kebele."""
		can_edit = svc._is_farmer_in_da_kebele(self.farmer_kebele_a_1, [self.da_id])

		self.assertTrue(can_edit)

	def test_cannot_edit_farmer_in_other_kebele(self):
		"""Test DA cannot edit farmers in other Kebele."""
		can_edit = svc._is_farmer_in_da_kebele(self.farmer_kebele_b_1, [self.da_id])

		self.assertFalse(can_edit)

	def test_kebele_filter_narrows_within_assignment(self):
		"""Test Kebele filter can only narrow within assigned Kebele."""
		# DA with assignment to Kebele A
		farmers_all, _ = svc.list_farmers(self.ctx_da)
		farmers_filtered, _ = svc.list_farmers(self.ctx_da, kebele=self.kebele_a)

		# Both should return same results (DA only has access to Kebele A)
		self.assertEqual(len(farmers_all), len(farmers_filtered))

	def test_get_da_assigned_kebeles(self):
		"""Test helper function returns assigned Kebeles."""
		kebeles = svc._get_da_assigned_kebeles([self.da_id])

		self.assertEqual(len(kebeles), 1)
		self.assertIn(self.kebele_a, kebeles)

	def test_get_da_assigned_kebeles_multiple_das(self):
		"""Test helper function with multiple DAs."""
		kebeles = svc._get_da_assigned_kebeles([self.da_id, self.da_id_other])

		self.assertEqual(len(kebeles), 2)
		self.assertIn(self.kebele_a, kebeles)
		self.assertIn(self.kebele_b, kebeles)

	def test_pagination_with_kebele_scoping(self):
		"""Test pagination works correctly with Kebele scoping."""
		farmers_page1, total = svc.list_farmers(self.ctx_da, limit=1, start=0)
		farmers_page2, _ = svc.list_farmers(self.ctx_da, limit=1, start=1)

		self.assertEqual(total, 2)
		self.assertEqual(len(farmers_page1), 1)
		self.assertEqual(len(farmers_page2), 1)
		self.assertNotEqual(farmers_page1[0]["farmer_id"], farmers_page2[0]["farmer_id"])
