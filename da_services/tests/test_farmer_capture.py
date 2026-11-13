"""Farmer resolution for survey capture (FR-09d Stage 1).

These tests do not need the survey DocTypes, so they run on a site that has not
migrated the survey tables yet.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from da_services.services import farmers as svc
from da_services.tests.fixtures import create_administrative_areas, ensure_kebele_assignment
from da_services.utils.permissions import get_scope_context

DA_ID = "DA-000001"
DA_OTHER = "DA-000004"
FARMER_A = "farmer-uuid-001"  # FR-000101, Bako 01 / ET04-W01
FARMER_B = "farmer-uuid-002"  # FR-000102, Bako 02 / ET04-W01
FARMER_C = "farmer-uuid-003"  # FR-000103, Kebele 03 / ET03-W02
FARMER_NO_KEBELE = "farmer-uuid-005"

FARMERS = {
	FARMER_A: ("FR-000101", "Bako 01", "ET04-W01", "ET04-Z01", "ET04"),
	FARMER_B: ("FR-000102", "Bako 02", "ET04-W01", "ET04-Z01", "ET04"),
	FARMER_C: ("FR-000103", "Kebele 03", "ET03-W02", "ET03-Z02", "ET03"),
	FARMER_NO_KEBELE: ("FR-000105", None, "ET04-W01", "ET04-Z01", "ET04"),
}


class TestResolveFarmerForCapture(IntegrationTestCase):
	def setUp(self):
		self._farmer_backend = frappe.conf.get("farmer_registry_backend")
		frappe.conf.farmer_registry_backend = "mock"

		create_administrative_areas()
		frappe.db.delete("DA Kebele Assignment", {"da_id": ("in", [DA_ID, DA_OTHER])})
		frappe.db.delete("DA Farmer Reference", {"name": ("in", list(FARMERS))})
		ensure_kebele_assignment(DA_ID, "ET04-W01-K01")

		for farmer_id, (reg_id, kebele, woreda, zone, region) in FARMERS.items():
			frappe.get_doc(
				{
					"doctype": "DA Farmer Reference",
					"farmer_id": farmer_id,
					"reg_id": reg_id,
					"farmer_name": f"Test {farmer_id}",
					"kebele": kebele,
					"woreda": woreda,
					"zone": zone,
					"region": region,
				}
			).insert(ignore_permissions=True, ignore_links=True)

		self.ctx = get_scope_context("Administrator")

	def tearDown(self):
		if self._farmer_backend is None:
			frappe.conf.pop("farmer_registry_backend", None)
		else:
			frappe.conf.farmer_registry_backend = self._farmer_backend
		frappe.db.delete("DA Kebele Assignment", {"da_id": ("in", [DA_ID, DA_OTHER])})
		frappe.db.delete("DA Farmer Reference", {"name": ("in", list(FARMERS))})

	def _resolve(self, farmer_id, collector_da_id=DA_ID, survey_woreda=None):
		return svc.resolve_farmer_for_capture(
			farmer_id,
			self.ctx,
			collector_da_id=collector_da_id,
			survey_woreda=survey_woreda,
		)

	def test_internal_id_returns_canonical_location(self):
		ref = self._resolve(FARMER_A)
		self.assertEqual(ref["farmer_id"], FARMER_A)
		self.assertEqual(ref["kebele"], "Bako 01")
		self.assertEqual(ref["woreda"], "ET04-W01")
		self.assertEqual(ref["zone"], "ET04-Z01")
		self.assertEqual(ref["region"], "ET04")

	def test_display_id_resolves_to_internal_id(self):
		ref = self._resolve("FR-000101")
		self.assertEqual(ref["farmer_id"], FARMER_A)
		self.assertEqual(ref["reg_id"], "FR-000101")

	def test_unknown_farmer_is_404(self):
		with self.assertRaises(frappe.DoesNotExistError):
			self._resolve("FR-missing")

	def test_farmer_outside_collector_kebele_is_403(self):
		with self.assertRaises(frappe.PermissionError):
			self._resolve(FARMER_B)

	def test_farmer_without_kebele_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._resolve(FARMER_NO_KEBELE)

	def test_survey_woreda_mismatch_is_403(self):
		with self.assertRaises(frappe.PermissionError):
			self._resolve(FARMER_A, survey_woreda="ET03-W02")

	def test_survey_woreda_match_passes(self):
		ref = self._resolve(FARMER_A, survey_woreda="ET04-W01")
		self.assertEqual(ref["woreda"], "ET04-W01")

	def test_farmer_in_any_assigned_kebele_passes(self):
		# Second Effective assignment for the same DA: farmer B now qualifies.
		frappe.get_doc(
			{
				"doctype": "DA Kebele Assignment",
				"da_id": DA_ID,
				"kebele": "ET04-W01-K02",
				"state": "Effective",
				"mode": "Automatic",
				"effective_date": "2026-10-01",
			}
		).insert(ignore_permissions=True, ignore_links=True)
		ref = self._resolve(FARMER_B)
		self.assertEqual(ref["farmer_id"], FARMER_B)

	def test_collector_without_effective_assignment_is_403(self):
		with self.assertRaises(frappe.PermissionError):
			self._resolve(FARMER_C, collector_da_id=DA_OTHER)
