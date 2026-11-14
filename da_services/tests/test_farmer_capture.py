"""Farmer resolution for survey capture (FR-09d Stage 1).

These tests do not need the survey DocTypes, so they run on a site that has not
migrated the survey tables yet. Geography is the mock DA Registry posting for
DA-000001, which is rewritten from the synced masters (``aligned_mock_geo``).
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import farmers as svc
from da_services.tests.fixtures import (
	aligned_mock_geo,
	ensure_da_user,
	ensure_kebele_assignment,
	require_geo_masters,
)
from da_services.utils.permissions import get_scope_context

DA_ID = "DA-000001"
DA_OTHER = "DA-000004"
FARMER_A = "farmer-uuid-001"
FARMER_B = "farmer-uuid-002"
FARMER_C = "farmer-uuid-003"
FARMER_NO_KEBELE = "farmer-uuid-005"


class TestResolveFarmerForCapture(IntegrationTestCase):
	def setUp(self):
		require_geo_masters()
		MockDARegistryClient.reset()  # rebuilds the fixture postings from the live masters
		g = aligned_mock_geo()
		self.geo = g
		self.region, self.zone, self.woreda = g["region"], g["zone"], g["woreda"]
		self.kebele, self.kebele_alt = g["kebele"], g["kebele_alt"]
		self.other_woreda = g["other_woreda"]
		frappe.db.delete("DA Kebele Assignment", {"da_id": ("in", [DA_ID, DA_OTHER])})
		frappe.db.delete(
			"DA Farmer Reference", {"name": ("in", [FARMER_A, FARMER_B, FARMER_C, FARMER_NO_KEBELE])}
		)
		ensure_kebele_assignment(DA_ID, self.kebele)

		rows = {
			FARMER_A: ("FR-000101", self.kebele, self.woreda, self.zone, self.region),
			FARMER_B: ("FR-000102", self.kebele_alt, self.woreda, self.zone, self.region),
			FARMER_C: ("FR-000103", g["other_kebele"], g["other_woreda"], g["other_zone"], g["other_region"]),
			FARMER_NO_KEBELE: ("FR-000105", None, self.woreda, self.zone, self.region),
		}
		for farmer_id, (reg_id, kebele, woreda, zone, region) in rows.items():
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
		frappe.db.delete("DA Kebele Assignment", {"da_id": ("in", [DA_ID, DA_OTHER])})
		frappe.db.delete(
			"DA Farmer Reference", {"name": ("in", [FARMER_A, FARMER_B, FARMER_C, FARMER_NO_KEBELE])}
		)
		MockDARegistryClient.reset()

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
		self.assertEqual(ref["kebele"], self.kebele)
		self.assertEqual(ref["woreda"], self.woreda)
		self.assertEqual(ref["zone"], self.zone)
		self.assertEqual(ref["region"], self.region)

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
			self._resolve(FARMER_A, survey_woreda=self.other_woreda)

	def test_survey_woreda_match_passes(self):
		ref = self._resolve(FARMER_A, survey_woreda=self.woreda)
		self.assertEqual(ref["woreda"], self.woreda)

	def test_farmer_in_any_assigned_kebele_passes(self):
		frappe.get_doc(
			{
				"doctype": "DA Kebele Assignment",
				"user": ensure_da_user(DA_ID),
				"kebele": self.kebele_alt,
				"state": "Effective",
				"mode": "Automatic",
				"effective_date": "2026-10-01",
				"registry_sync_state": "Not sent",
			}
		).insert(ignore_permissions=True)
		ref = self._resolve(FARMER_B)
		self.assertEqual(ref["farmer_id"], FARMER_B)

	def test_collector_without_effective_assignment_is_403(self):
		with self.assertRaises(frappe.PermissionError):
			self._resolve(FARMER_C, collector_da_id=DA_OTHER)
