"""Tests for Kebele-based farmer scoping (DA-211 Phase 2)."""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.services import constants as C
from da_services.services import farmers as svc
from da_services.tests.fixtures import as_user, create_administrative_areas, ensure_da_user

SUPERVISOR = "kebele-scope-sup@da.local"
NO_KEBELE_DA = "kebele-scope-none@da.local"


def _with_role(user: str, role: str) -> str:
	if not frappe.db.exists("User", user):
		frappe.get_doc(
			{"doctype": "User", "email": user, "first_name": user.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	if role not in frappe.get_roles(user):
		frappe.get_doc("User", user).add_roles(role)
	return user


def _grant(user: str, role: str, **scope) -> None:
	frappe.get_doc(
		{"doctype": "DA RBAC Assignment", "user": user, "role": role, "effective_from": today(), **scope}
	).insert(ignore_permissions=True, ignore_links=True)


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

		self.da_user = _with_role(ensure_da_user(self.da_id), C.ROLE_DA)
		self.da_user_other = _with_role(ensure_da_user(self.da_id_other), C.ROLE_DA)
		self._clean()

		# Create DA Kebele Assignment for Kebele A
		frappe.get_doc(
			{
				"doctype": "DA Kebele Assignment",
				"user": self.da_user,
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
				"user": self.da_user_other,
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

		# The DA signs in: the Kebele assignment above gave them a User Permission on Kebele A.
		self.ctx_da = self._sign_in(self.da_user)

	def tearDown(self):
		"""Clean up test data."""
		frappe.set_user("Administrator")
		self._clean()

	def _clean(self):
		users = [self.da_user, self.da_user_other, SUPERVISOR, NO_KEBELE_DA]
		frappe.db.delete("DA Farmer Reference", {})
		frappe.db.delete("DA Kebele Assignment", {"da_id": ["in", [self.da_id, self.da_id_other]]})
		frappe.db.delete("DA RBAC Assignment", {"user": ["in", users]})
		frappe.db.delete("User Permission", {"user": ["in", users]})

	def _sign_in(self, user: str):
		frappe.set_user(user)
		return user

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
		with as_user(self.ctx_da):
			farmers, total = svc.list_farmers()

		farmer_ids = [f["farmer_id"] for f in farmers]

		# Should see both farmers in Kebele A
		self.assertIn(self.farmer_kebele_a_1, farmer_ids)
		self.assertIn(self.farmer_kebele_a_2, farmer_ids)

		# Should NOT see farmer in Kebele B
		self.assertNotIn(self.farmer_kebele_b_1, farmer_ids)

		self.assertEqual(total, 2)

	def test_another_kebele_filter_lists_nothing(self):
		"""A Kebele outside the DA's User Permissions only narrows the list to nothing."""
		with as_user(self.ctx_da):
			farmers, total = svc.list_farmers(kebele=self.kebele_b)
		self.assertEqual((farmers, total), ([], 0))

	def test_da_no_assignment_sees_empty_list(self):
		"""A live DA grant without a Kebele gives the Not Assigned Kebele User Permission."""
		frappe.set_user("Administrator")
		_with_role(NO_KEBELE_DA, C.ROLE_DA)
		_grant(NO_KEBELE_DA, C.ROLE_DA, da_id="DA-NO-ASSIGN", region_scope="ET04", woreda_scope=self.woreda)
		ctx_no_assignment = self._sign_in(NO_KEBELE_DA)

		with as_user(ctx_no_assignment):
			farmers, total = svc.list_farmers()

		self.assertEqual(len(farmers), 0)
		self.assertEqual(total, 0)

	def test_supervisor_sees_all_farmers_in_woreda(self):
		"""Test Supervisor sees all farmers in their Woreda."""
		frappe.set_user("Administrator")
		_with_role(SUPERVISOR, C.ROLE_SUPERVISOR)
		_grant(SUPERVISOR, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope=self.woreda)
		ctx_supervisor = self._sign_in(SUPERVISOR)

		with as_user(ctx_supervisor):
			farmers, total = svc.list_farmers()

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

	def test_kebele_name_from_the_registry_matches_the_assigned_code(self):
		"""The registry gives the Kebele's name; the assignment holds its code."""
		self._create_farmer("FARMER-A-NAME", "Dambi Gobu")  # name of ET04-W01-K03

		self.assertTrue(svc._is_farmer_in_da_kebele("FARMER-A-NAME", [self.da_id]))
		self.assertFalse(svc._is_farmer_in_da_kebele("FARMER-A-NAME", [self.da_id_other]))

	def test_da_reads_the_profile_of_an_unlinked_farmer_in_their_kebele(self):
		"""List and profile agree: no manual link is needed in the assigned Kebele."""
		with as_user(self.ctx_da):
			ref = svc._require_farmer_in_scope(self.farmer_kebele_a_1)

		self.assertEqual(ref["farmer_id"], self.farmer_kebele_a_1)

	def test_da_cannot_read_the_profile_of_a_farmer_in_another_kebele(self):
		with self.assertRaises(frappe.PermissionError):
			with as_user(self.ctx_da):
				svc._require_farmer_in_scope(self.farmer_kebele_b_1)

	def test_search_sends_the_woreda_asked_for(self):
		"""Registry search has no User Permissions to lean on: the caller names the Woreda."""
		from unittest.mock import Mock, patch

		client = Mock()
		client.search_farmers.return_value = {"items": [], "page": 1, "page_size": 10, "total": 0}
		with patch.object(svc, "get_farmer_registry_client", return_value=client):
			with as_user(self.ctx_da):
				svc.search_farmers(search_text="a", woreda=self.woreda, kebele=self.kebele_a)

		sent = client.search_farmers.call_args.kwargs["filter_by"]
		self.assertEqual((sent["woreda"], sent["kebele"]), (self.woreda, self.kebele_a))

	def test_kebele_filter_narrows_within_assignment(self):
		"""Test Kebele filter can only narrow within assigned Kebele."""
		# DA with assignment to Kebele A
		with as_user(self.ctx_da):
			farmers_all, _ = svc.list_farmers()
		with as_user(self.ctx_da):
			farmers_filtered, _ = svc.list_farmers(kebele=self.kebele_a)

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
		with as_user(self.ctx_da):
			farmers_page1, total = svc.list_farmers(limit=1, start=0)
		with as_user(self.ctx_da):
			farmers_page2, _ = svc.list_farmers(limit=1, start=1)

		self.assertEqual(total, 2)
		self.assertEqual(len(farmers_page1), 1)
		self.assertEqual(len(farmers_page2), 1)
		self.assertNotEqual(farmers_page1[0]["farmer_id"], farmers_page2[0]["farmer_id"])
