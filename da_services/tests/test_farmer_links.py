"""Tests for manual farmer-to-DA assignment (DA-211 Phase 1)."""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from da_services.services import constants as C
from da_services.services import farmer_links as svc
from da_services.tests.fixtures import as_user, create_administrative_areas, ensure_da_user


class TestFarmerLinks(IntegrationTestCase):
	"""Test manual farmer assignment API and service."""

	def setUp(self):
		"""Set up test data."""
		self.da_id = "DA-TEST-001"
		self.farmer_id = "FARMER-TEST-001"
		self.kebele = "ET04-W01-K03"
		self.woreda = "ET04-W01"
		self.supervisor_user = "supervisor@test.com"

		create_administrative_areas()
		if not frappe.db.exists("User", self.supervisor_user):
			user = frappe.new_doc("User")
			user.email = self.supervisor_user
			user.first_name = "Supervisor"
			user.send_welcome_email = 0
			user.insert(ignore_permissions=True)
		# DA Farmer Link create / read come from the Supervisor role (Role Permission Manager).
		if C.ROLE_SUPERVISOR not in frappe.get_roles(self.supervisor_user):
			frappe.get_doc("User", self.supervisor_user).add_roles(C.ROLE_SUPERVISOR)

		# Clean up any existing test data
		frappe.db.delete("DA Farmer Link", {"da_id": self.da_id})
		frappe.db.delete("DA Kebele Assignment", {"da_id": self.da_id})
		frappe.db.delete("DA Farmer Reference", {"farmer_id": ["in", [self.farmer_id, "FARMER-TEST-002"]]})

		# Create DA Kebele Assignment with Manual mode
		self.assignment = frappe.get_doc(
			{
				"doctype": "DA Kebele Assignment",
				"user": ensure_da_user(self.da_id),
				"da_id": self.da_id,
				"kebele": self.kebele,
				"state": "Effective",
				"mode": "Manual",
				"effective_date": "2026-10-01",
				"woreda": self.woreda,
			}
		).insert(ignore_permissions=True, ignore_links=True)

		for farmer_id in (self.farmer_id, "FARMER-TEST-002"):
			frappe.get_doc(
				{
					"doctype": "DA Farmer Reference",
					"farmer_id": farmer_id,
					"farmer_name": f"Test Farmer {farmer_id}",
					"kebele": self.kebele,
					"woreda": self.woreda,
					"registration_state": "APPROVED",
				}
			).insert(ignore_permissions=True, ignore_links=True)

		# Create Supervisor context
		self.ctx = self.supervisor_user

	def tearDown(self):
		"""Clean up test data."""
		frappe.db.delete("DA Farmer Link", {"da_id": self.da_id})
		frappe.db.delete("DA Kebele Assignment", {"da_id": self.da_id})
		frappe.db.delete("DA Farmer Reference", {"farmer_id": ["in", [self.farmer_id, "FARMER-TEST-002"]]})

	def test_create_manual_farmer_link_success(self):
		"""Test successful manual farmer link creation."""
		with as_user(self.ctx):
			result = svc.create_farmer_link(
				da_id=self.da_id,
				farmer_id=self.farmer_id,
			)

		self.assertEqual(result["da_id"], self.da_id)
		self.assertEqual(result["farmer_id"], self.farmer_id)
		self.assertEqual(result["link_type"], "Manual")
		self.assertEqual(result["kebele"], self.kebele)
		self.assertIsNotNone(result["link_id"])
		self.assertIsNotNone(result["linked_at"])

	def test_create_link_no_assignment(self):
		"""Test rejection when DA has no Kebele assignment."""
		# Supersede the assignment
		self.assignment.state = "Superseded"
		self.assignment.save(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError) as cm:
			with as_user(self.ctx):
				svc.create_farmer_link(
					da_id=self.da_id,
					farmer_id=self.farmer_id,
				)

		self.assertIn("Effective", str(cm.exception))

	def test_create_link_automatic_mode(self):
		"""Test rejection when DA assignment mode is Automatic."""
		# Change mode to Automatic
		self.assignment.mode = "Automatic"
		self.assignment.save(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError) as cm:
			with as_user(self.ctx):
				svc.create_farmer_link(
					da_id=self.da_id,
					farmer_id=self.farmer_id,
				)

		self.assertIn("Automatic", str(cm.exception))

	def test_create_link_duplicate(self):
		"""Test rejection of duplicate active link."""
		# Create first link
		with as_user(self.ctx):
			svc.create_farmer_link(
				da_id=self.da_id,
				farmer_id=self.farmer_id,
			)

		# Try to create duplicate
		with self.assertRaises(frappe.ValidationError) as cm:
			with as_user(self.ctx):
				svc.create_farmer_link(
					da_id=self.da_id,
					farmer_id=self.farmer_id,
				)

		self.assertIn("already actively linked", str(cm.exception))

	def test_create_link_idempotency(self):
		"""Test idempotent retry using client_op_id."""
		client_op_id = "test-op-123"

		# Create first time
		with as_user(self.ctx):
			result1 = svc.create_farmer_link(
				da_id=self.da_id,
				farmer_id=self.farmer_id,
				client_op_id=client_op_id,
			)

		# Retry with same client_op_id
		with as_user(self.ctx):
			result2 = svc.create_farmer_link(
				da_id=self.da_id,
				farmer_id=self.farmer_id,
				client_op_id=client_op_id,
			)

		# Should return same link
		self.assertEqual(result1["link_id"], result2["link_id"])
		self.assertEqual(result1["da_id"], result2["da_id"])
		self.assertEqual(result1["farmer_id"], result2["farmer_id"])

	def test_create_link_missing_da_id(self):
		"""Test validation of required da_id."""
		with self.assertRaises(frappe.ValidationError) as cm:
			with as_user(self.ctx):
				svc.create_farmer_link(
					da_id="",
					farmer_id=self.farmer_id,
				)

		self.assertIn("required", str(cm.exception))

	def test_create_link_missing_farmer_id(self):
		"""Test validation of required farmer_id."""
		with self.assertRaises(frappe.ValidationError) as cm:
			with as_user(self.ctx):
				svc.create_farmer_link(
					da_id=self.da_id,
					farmer_id="",
				)

		self.assertIn("required", str(cm.exception))

	def test_list_farmer_links_by_da(self):
		"""Test listing farmer links filtered by DA."""
		# Create multiple links
		with as_user(self.ctx):
			svc.create_farmer_link(
				da_id=self.da_id,
				farmer_id=self.farmer_id,
			)

		with as_user(self.ctx):
			svc.create_farmer_link(
				da_id=self.da_id,
				farmer_id="FARMER-TEST-002",
			)

		# List links for this DA
		with as_user(self.ctx):
			links = svc.list_farmer_links(da_id=self.da_id)

		self.assertEqual(len(links), 2)
		self.assertTrue(all(link["da_id"] == self.da_id for link in links))

	def test_list_farmer_links_by_type(self):
		"""Test listing farmer links filtered by link_type."""
		# Create manual link
		with as_user(self.ctx):
			svc.create_farmer_link(
				da_id=self.da_id,
				farmer_id=self.farmer_id,
			)

		# List manual links
		with as_user(self.ctx):
			links = svc.list_farmer_links(link_type="Manual")

		self.assertTrue(all(link["link_type"] == "Manual" for link in links))

	def test_list_farmer_links_inactive_filter(self):
		"""Test listing inactive farmer links."""
		# Create link
		with as_user(self.ctx):
			result = svc.create_farmer_link(
				da_id=self.da_id,
				farmer_id=self.farmer_id,
			)

		# Deactivate it
		link = frappe.get_doc("DA Farmer Link", result["link_id"])
		link.is_active = 0
		link.save(ignore_permissions=True)

		# List inactive links
		with as_user(self.ctx):
			active_links = svc.list_farmer_links(da_id=self.da_id, is_active=True)
		with as_user(self.ctx):
			inactive_links = svc.list_farmer_links(da_id=self.da_id, is_active=False)

		self.assertEqual(len(active_links), 0)
		self.assertEqual(len(inactive_links), 1)
