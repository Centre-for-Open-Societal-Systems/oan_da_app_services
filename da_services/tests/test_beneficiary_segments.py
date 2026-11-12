"""Tests for beneficiary segment management (DA-XX).

Test Admin-side beneficiary segment creation, listing, retrieval, updating,
and duplicate/empty name validation.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from da_services.services import beneficiary_segments as svc
from da_services.tests.fixtures import create_administrative_areas
from da_services.utils.permissions import get_scope_context


class TestBeneficiarySegments(IntegrationTestCase):
	"""Test beneficiary segment API and service."""

	def setUp(self):
		"""Set up test data."""
		create_administrative_areas()
		self.admin_user = "admin@test.com"

		if not frappe.db.exists("User", self.admin_user):
			user = frappe.new_doc("User")
			user.email = self.admin_user
			user.first_name = "Admin"
			user.send_welcome_email = 0
			user.insert(ignore_permissions=True)

		# Clean up any existing test data
		frappe.db.delete("DA Beneficiary Segment")

	def tearDown(self):
		"""Clean up test data."""
		frappe.db.delete("DA Beneficiary Segment")

	def test_admin_create_segment_success(self):
		"""Test successful segment creation by Admin."""
		result = svc.create_segment(
			segment_name="Input subsidy – teff & wheat",
			description="Input subsidy for teff and wheat farmers",
			client_op_id="test-op-001",
		)

		self.assertEqual(result["segment_name"], "Input subsidy – teff & wheat")
		self.assertEqual(result["description"], "Input subsidy for teff and wheat farmers")
		self.assertEqual(result["is_active"], True)
		self.assertTrue(result["name"].startswith("DA-BS-"))

	def test_admin_list_segments(self):
		"""Test Admin lists segments."""
		# Create a segment first
		svc.create_segment(
			segment_name="Credit-eligible smallholders",
			description="Credit-eligible smallholder farmers",
		)

		# List segments
		result = svc.list_segments(active_only=True)

		self.assertEqual(result["total"], 1)
		self.assertEqual(result["segments"][0]["segment_name"], "Credit-eligible smallholders")

	def test_admin_get_segment(self):
		"""Test Admin retrieves a segment."""
		# Create a segment
		create_result = svc.create_segment(
			segment_name="Drought-response kebeles",
			description="Farmers in drought-response areas",
		)

		# Retrieve the segment
		result = svc.get_segment(segment_name=create_result["name"])

		self.assertEqual(result["segment_name"], "Drought-response kebeles")
		self.assertEqual(result["description"], "Farmers in drought-response areas")
		self.assertEqual(result["is_active"], True)

	def test_admin_update_segment(self):
		"""Test Admin updates a segment."""
		# Create a segment
		create_result = svc.create_segment(
			segment_name="Women-headed households",
			description="Households headed by women",
		)

		# Update the segment
		result = svc.update_segment(
			segment_name=create_result["name"],
			new_segment_name="Women-headed households (updated)",
			new_description="Updated description for women-headed households",
		)

		self.assertEqual(result["segment_name"], "Women-headed households (updated)")
		self.assertEqual(result["description"], "Updated description for women-headed households")

	def test_admin_reject_duplicate_name(self):
		"""Test Admin cannot create segment with duplicate name."""
		svc.create_segment(
			segment_name="Input subsidy – teff & wheat",
			description="Original description",
		)

		with self.assertRaises(frappe.ValidationError) as cm:
			svc.create_segment(
				segment_name="Input subsidy – teff & wheat",
				description="Duplicate attempt",
			)

		self.assertIn("already exists", str(cm.exception))

	def test_admin_reject_empty_name(self):
		"""Test Admin cannot create segment with empty name."""
		with self.assertRaises(frappe.ValidationError) as cm:
			svc.create_segment(segment_name="")

		self.assertIn("required", str(cm.exception))

	def test_non_admin_cannot_create_segment(self):
		"""Test non-Admin cannot create a segment."""
		from da_services.services.beneficiary_segments import create_segment

		# Create a supervisor user
		supervisor_user = "supervisor@test.com"
		if not frappe.db.exists("User", supervisor_user):
			user = frappe.new_doc("User")
			user.email = supervisor_user
			user.first_name = "Supervisor"
			user.send_welcome_email = 0
			user.insert(ignore_permissions=True)

		supervisor_ctx = get_scope_context(supervisor_user)

		# Try to create segment as supervisor
		with self.assertRaises(frappe.PermissionError):
			create_segment(
				segment_name="Input subsidy – teff & wheat",
				ctx=supervisor_ctx,
			)

	def test_non_admin_cannot_update_segment(self):
		"""Test non-Admin cannot update a segment."""
		from da_services.services.beneficiary_segments import update_segment

		# Create a segment as admin first
		create_result = svc.create_segment(
			segment_name="Input subsidy – teff & wheat",
			description="Original",
		)

		supervisor_user = "supervisor@test.com"
		if not frappe.db.exists("User", supervisor_user):
			user = frappe.new_doc("User")
			user.email = supervisor_user
			user.first_name = "Supervisor"
			user.send_welcome_email = 0
			user.insert(ignore_permissions=True)

		supervisor_ctx = get_scope_context(supervisor_user)

		# Try to update as non-admin
		with self.assertRaises(frappe.PermissionError):
			update_segment(
				segment_name=create_result["name"],
				new_segment_name="Updated name",
				ctx=supervisor_ctx,
			)

	def test_invalid_segment_id_returns_error(self):
		"""Test invalid segment ID returns error."""
		with self.assertRaises(frappe.DoesNotExistError):
			svc.get_segment(segment_name="DA-BS-NONEXISTENT")

	def test_deactivate_and_reactivate_segment(self):
		"""Test deactivate and reactivate a segment."""
		# Create a segment
		create_result = svc.create_segment(
			segment_name="Test segment for deactivation",
			description="Test deactivation",
		)

		# Deactivate
		result = svc.set_segment_status(
			segment_name=create_result["name"],
			is_active=False,
		)
		self.assertEqual(result["is_active"], False)

		# Reactivate
		result = svc.set_segment_status(
			segment_name=create_result["name"],
			is_active=True,
		)
		self.assertEqual(result["is_active"], True)
