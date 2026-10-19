"""Tests for manual farmer-to-DA assignment (DA-211 Phase 1)."""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from da_services.services import farmer_links as svc
from da_services.utils.permissions import ScopeContext


class TestFarmerLinks(IntegrationTestCase):
	"""Test manual farmer assignment API and service."""

	def setUp(self):
		"""Set up test data."""
		self.da_id = "DA-TEST-001"
		self.farmer_id = "FARMER-TEST-001"
		self.kebele = "ET04-W01-K03"
		self.woreda = "ET04-W01"
		self.supervisor_user = "supervisor@test.com"

		# Clean up any existing test data
		frappe.db.delete("DA Farmer Link", {"da_id": self.da_id})
		frappe.db.delete("DA Kebele Assignment", {"da_id": self.da_id})

		# Create DA Kebele Assignment with Manual mode
		self.assignment = frappe.get_doc(
			{
				"doctype": "DA Kebele Assignment",
				"da_id": self.da_id,
				"kebele": self.kebele,
				"state": "Effective",
				"mode": "Manual",
				"effective_date": "2026-10-01",
				"woreda": self.woreda,
			}
		).insert(ignore_permissions=True)

		# Create Supervisor context
		self.ctx = ScopeContext(
			user=self.supervisor_user,
			roles={"Supervisor"},
			woreda_scopes={self.woreda},
			unrestricted=False,
		)

	def tearDown(self):
		"""Clean up test data."""
		frappe.db.delete("DA Farmer Link", {"da_id": self.da_id})
		frappe.db.delete("DA Kebele Assignment", {"da_id": self.da_id})

	def test_create_manual_farmer_link_success(self):
		"""Test successful manual farmer link creation."""
		result = svc.create_farmer_link(
			da_id=self.da_id,
			farmer_id=self.farmer_id,
			ctx=self.ctx,
		)

		self.assertEqual(result["da_id"], self.da_id)
		self.assertEqual(result["farmer_id"], self.farmer_id)
		self.assertEqual(result["link_type"], "Manual")
		self.assertEqual(result["kebele"], self.kebele)
		self.assertIsNotNone(result["link_id"])
		self.assertIsNotNone(result["linked_at"])

	def test_create_link_no_assignment(self):
		"""Test rejection when DA has no Kebele assignment."""
		# Delete the assignment
		frappe.delete_doc("DA Kebele Assignment", self.assignment.name, force=True)

		with self.assertRaises(frappe.ValidationError) as cm:
			svc.create_farmer_link(
				da_id=self.da_id,
				farmer_id=self.farmer_id,
				ctx=self.ctx,
			)

		self.assertIn("no active Kebele assignment", str(cm.exception))

	def test_create_link_automatic_mode(self):
		"""Test rejection when DA assignment mode is Automatic."""
		# Change mode to Automatic
		self.assignment.mode = "Automatic"
		self.assignment.save(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError) as cm:
			svc.create_farmer_link(
				da_id=self.da_id,
				farmer_id=self.farmer_id,
				ctx=self.ctx,
			)

		self.assertIn("mode is Automatic", str(cm.exception))

	def test_create_link_duplicate(self):
		"""Test rejection of duplicate active link."""
		# Create first link
		svc.create_farmer_link(
			da_id=self.da_id,
			farmer_id=self.farmer_id,
			ctx=self.ctx,
		)

		# Try to create duplicate
		with self.assertRaises(frappe.ValidationError) as cm:
			svc.create_farmer_link(
				da_id=self.da_id,
				farmer_id=self.farmer_id,
				ctx=self.ctx,
			)

		self.assertIn("already linked", str(cm.exception))

	def test_create_link_idempotency(self):
		"""Test idempotent retry using client_op_id."""
		client_op_id = "test-op-123"

		# Create first time
		result1 = svc.create_farmer_link(
			da_id=self.da_id,
			farmer_id=self.farmer_id,
			client_op_id=client_op_id,
			ctx=self.ctx,
		)

		# Retry with same client_op_id
		result2 = svc.create_farmer_link(
			da_id=self.da_id,
			farmer_id=self.farmer_id,
			client_op_id=client_op_id,
			ctx=self.ctx,
		)

		# Should return same link
		self.assertEqual(result1["link_id"], result2["link_id"])
		self.assertEqual(result1["da_id"], result2["da_id"])
		self.assertEqual(result1["farmer_id"], result2["farmer_id"])

	def test_create_link_supervisor_scope_violation(self):
		"""Test rejection when Supervisor doesn't have scope over Woreda."""
		# Create context with different Woreda scope
		wrong_ctx = ScopeContext(
			user=self.supervisor_user,
			roles={"Supervisor"},
			woreda_scopes={"ET03-W02"},  # Different Woreda
			unrestricted=False,
		)

		with self.assertRaises(frappe.PermissionError) as cm:
			svc.create_farmer_link(
				da_id=self.da_id,
				farmer_id=self.farmer_id,
				ctx=wrong_ctx,
			)

		self.assertIn("outside your Woreda", str(cm.exception))

	def test_create_link_missing_da_id(self):
		"""Test validation of required da_id."""
		with self.assertRaises(frappe.ValidationError) as cm:
			svc.create_farmer_link(
				da_id="",
				farmer_id=self.farmer_id,
				ctx=self.ctx,
			)

		self.assertIn("required", str(cm.exception))

	def test_create_link_missing_farmer_id(self):
		"""Test validation of required farmer_id."""
		with self.assertRaises(frappe.ValidationError) as cm:
			svc.create_farmer_link(
				da_id=self.da_id,
				farmer_id="",
				ctx=self.ctx,
			)

		self.assertIn("required", str(cm.exception))

	def test_list_farmer_links_by_da(self):
		"""Test listing farmer links filtered by DA."""
		# Create multiple links
		svc.create_farmer_link(
			da_id=self.da_id,
			farmer_id=self.farmer_id,
			ctx=self.ctx,
		)

		svc.create_farmer_link(
			da_id=self.da_id,
			farmer_id="FARMER-TEST-002",
			ctx=self.ctx,
		)

		# List links for this DA
		links = svc.list_farmer_links(da_id=self.da_id, ctx=self.ctx)

		self.assertEqual(len(links), 2)
		self.assertTrue(all(link["da_id"] == self.da_id for link in links))

	def test_list_farmer_links_by_type(self):
		"""Test listing farmer links filtered by link_type."""
		# Create manual link
		svc.create_farmer_link(
			da_id=self.da_id,
			farmer_id=self.farmer_id,
			ctx=self.ctx,
		)

		# List manual links
		links = svc.list_farmer_links(link_type="Manual", ctx=self.ctx)

		self.assertTrue(all(link["link_type"] == "Manual" for link in links))

	def test_list_farmer_links_inactive_filter(self):
		"""Test listing inactive farmer links."""
		# Create link
		result = svc.create_farmer_link(
			da_id=self.da_id,
			farmer_id=self.farmer_id,
			ctx=self.ctx,
		)

		# Deactivate it
		link = frappe.get_doc("DA Farmer Link", result["link_id"])
		link.is_active = 0
		link.save(ignore_permissions=True)

		# List inactive links
		active_links = svc.list_farmer_links(da_id=self.da_id, is_active=True, ctx=self.ctx)
		inactive_links = svc.list_farmer_links(da_id=self.da_id, is_active=False, ctx=self.ctx)

		self.assertEqual(len(active_links), 0)
		self.assertEqual(len(inactive_links), 1)
