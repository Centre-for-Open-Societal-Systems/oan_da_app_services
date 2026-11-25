"""Central dashboard visits chart feeder: row contract, filters, auth on/off."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import getdate, today

from da_services.api import router
from da_services.api.v1 import charts as charts_api
from da_services.da_services.doctype.da_visit.da_visit import (
	CANCELLED,
	COMPLETE,
	IN_PROGRESS,
	MISSED,
	PLANNED,
	RESCHEDULED,
)
from da_services.services import charts_auth, charts_visits
from da_services.services import constants as C
from da_services.tests.fixtures import (
	cleanup_test_rows,
	ensure_da_user,
	live_geo,
	set_da_registry_id,
	stamp_visit_geo,
)

DA_USER = "charts-da@da.local"
DA_ID = "DA-CHARTS-001"
DA_ID_2 = "DA-CHARTS-002"
FARMER_A = "FR-CHARTS-A"
FARMER_B = "FR-CHARTS-B"
KPI_KEYS = list(charts_visits.KPI_KEYS)
SETTINGS = "DA Services Settings"
CHARTS_AUTH_FIELDS = (
	"charts_auth_enabled",
	"charts_auth_issuer",
	"charts_auth_audience",
)


def _set_charts_auth(**values) -> None:
	settings = frappe.get_single(SETTINGS)
	for key, value in values.items():
		settings.set(key, value)
	settings.save()
	frappe.clear_document_cache(SETTINGS, SETTINGS)


def _month_bounds() -> tuple[str, str]:
	d = getdate(today())
	last = monthrange(d.year, d.month)[1]
	return f"{d.year:04d}-{d.month:02d}-01", f"{d.year:04d}-{d.month:02d}-{last:02d}"


def _user(email: str, roles: list[str]) -> str:
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = email.split("@")[0]
		u.send_welcome_email = 0
		u.insert(ignore_permissions=True, ignore_links=True)
	u = frappe.get_doc("User", email)
	for role in roles:
		if role not in frappe.get_roles(email):
			u.add_roles(role)
	return email


class TestChartsVisits(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.geo = live_geo()

	def setUp(self):
		frappe.set_user("Administrator")
		settings = frappe.get_single(SETTINGS)
		self._saved_charts_auth = {k: settings.get(k) for k in CHARTS_AUTH_FIELDS}
		_set_charts_auth(
			charts_auth_enabled=0,
			charts_auth_issuer="",
			charts_auth_audience="",
		)
		cleanup_test_rows(users=(DA_USER,), da_ids=(DA_ID, DA_ID_2), farmer_ids=(FARMER_A, FARMER_B))
		_user(DA_USER, [C.ROLE_DA])
		set_da_registry_id(DA_USER, DA_ID)
		ensure_da_user(DA_ID_2)
		self._seed()

	def tearDown(self):
		frappe.set_user("Administrator")
		cleanup_test_rows(users=(DA_USER,), da_ids=(DA_ID, DA_ID_2), farmer_ids=(FARMER_A, FARMER_B))
		restored = {
			k: (v or 0) if k == "charts_auth_enabled" else (v or "")
			for k, v in self._saved_charts_auth.items()
		}
		_set_charts_auth(**restored)

	def _insert(
		self,
		*,
		scheduled_at: str,
		status: str = PLANNED,
		farmer_id: str = FARMER_A,
		da_id: str = DA_ID,
		visit_type: str = "Advisory",
		geo: dict | None = None,
		user: str | None = None,
	) -> str:
		geo = geo or self.geo
		doc = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"user": user or DA_USER,
				"da_id": da_id,
				"farmer_id": farmer_id,
				"farmer_name": "Charts Farmer",
				"visit_type": visit_type,
				"physical_status": PLANNED,
				"scheduled_at": scheduled_at,
				"source": "Planned",
				"region": geo["region"],
				"zone": geo["zone"],
				"woreda": geo["woreda"],
				"kebele": geo["kebele"],
			}
		)
		doc.insert(ignore_permissions=True, ignore_links=True)
		if status != PLANNED:
			frappe.db.set_value("DA Visit", doc.name, "physical_status", status, update_modified=False)
		stamp_visit_geo(doc.name, geo)
		return doc.name

	def _seed(self):
		# Mid-month anchors so default "current month" and explicit ranges both hit.
		d = getdate(today())
		mid = f"{d.year:04d}-{d.month:02d}-15 10:00:00"
		self._insert(scheduled_at=mid, status=COMPLETE, farmer_id=FARMER_A, visit_type="Advisory")
		self._insert(scheduled_at=mid, status=PLANNED, farmer_id=FARMER_A, visit_type="Crop Survey")
		self._insert(scheduled_at=mid, status=MISSED, farmer_id=FARMER_B, visit_type="Advisory")
		self._insert(scheduled_at=mid, status=IN_PROGRESS, farmer_id=FARMER_B, visit_type="Livestock Survey")
		self._insert(scheduled_at=mid, status=RESCHEDULED, farmer_id=FARMER_A, visit_type="Follow-up")
		self._insert(scheduled_at=mid, status=CANCELLED, farmer_id=FARMER_B, visit_type="Services")
		foreign = {
			"region": self.geo["foreign_region"],
			"zone": frappe.db.get_value("DA Woreda", self.geo["foreign_woreda"], "zone"),
			"woreda": self.geo["foreign_woreda"],
			"kebele": self.geo["foreign_kebele"],
		}
		self._insert(
			scheduled_at=mid,
			status=COMPLETE,
			farmer_id=FARMER_A,
			da_id=DA_ID_2,
			visit_type="Advisory",
			geo=foreign,
			user=ensure_da_user(DA_ID_2),
		)

	def test_ungrouped_row_keys_and_counts(self):
		start, end = _month_bounds()
		# Scope to our seeded DA so leftover site visits cannot change the totals.
		rows = charts_visits.aggregate_visits(
			da_id=DA_ID,
			from_date=start,
			to_date=end,
		)
		self.assertEqual(len(rows), 1)
		row = rows[0]
		for key in KPI_KEYS:
			self.assertIn(key, row)
		self.assertEqual(row["total"], 6)
		self.assertEqual(row["completed"], 1)
		self.assertEqual(row["missed"], 1)
		self.assertEqual(row["rescheduled"], 1)
		self.assertEqual(row["in_progress"], 1)
		self.assertEqual(row["cancelled"], 1)
		# planned = PLANNED_STATES (Planned, Confirmed, In progress, Complete, Missed) — not Rescheduled/Cancelled
		self.assertEqual(row["planned"], 4)
		self.assertEqual(row["farmers_reached"], 2)

	def test_empty_filters_include_all_regions(self):
		start, end = _month_bounds()
		by_da = {
			r["da_id"]: r
			for r in charts_visits.aggregate_visits(from_date=start, to_date=end, group_by="da_id")
		}
		self.assertEqual(by_da[DA_ID]["total"], 6)
		self.assertEqual(by_da[DA_ID_2]["total"], 1)

	def test_all_geo_means_no_filter(self):
		start, end = _month_bounds()
		by_da = {
			r["da_id"]: r
			for r in charts_visits.aggregate_visits(
				region="all",
				woreda="",
				from_date=start,
				to_date=end,
				group_by="da_id",
			)
		}
		self.assertEqual(by_da[DA_ID]["total"], 6)
		self.assertEqual(by_da[DA_ID_2]["total"], 1)

	def test_ungrouped_empty_range_returns_zeroed_kpi(self):
		rows = charts_visits.aggregate_visits(from_date="2000-01-01", to_date="2000-01-31")
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0], charts_visits._zero_kpi())

	def test_grouped_empty_returns_empty_list(self):
		rows = charts_visits.aggregate_visits(
			from_date="2000-01-01",
			to_date="2000-01-31",
			group_by="woreda",
		)
		self.assertEqual(rows, [])

	def test_group_by_visit_type(self):
		start, end = _month_bounds()
		rows = charts_visits.aggregate_visits(
			da_id=DA_ID,
			from_date=start,
			to_date=end,
			group_by="visit_type",
		)
		by_type = {r["visit_type"]: r for r in rows}
		self.assertIn("Advisory", by_type)
		self.assertEqual(by_type["Advisory"]["total"], 2)  # Complete + Missed

	def test_group_by_month(self):
		start, end = _month_bounds()
		d = getdate(today())
		rows = charts_visits.aggregate_visits(
			da_id=DA_ID,
			from_date=start,
			to_date=end,
			group_by="month",
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["month"], f"{d.year:04d}-{d.month:02d}")
		self.assertEqual(rows[0]["total"], 6)

	def test_api_returns_raw_list_without_envelope(self):
		frappe.set_user("Guest")
		out = charts_api.visits_chart()
		self.assertIsInstance(out, list)
		self.assertEqual(len(out), 1)
		self.assertNotIn("status", out[0] if isinstance(out[0], dict) else {})
		for key in KPI_KEYS:
			self.assertIn(key, out[0])

	def test_auth_off_allows_missing_token(self):
		_set_charts_auth(charts_auth_enabled=0)
		self.assertIsNone(charts_auth.require_charts_read())

	def test_auth_on_rejects_missing_token(self):
		_set_charts_auth(
			charts_auth_enabled=1,
			charts_auth_issuer="https://kc.example/realms/staff",
			charts_auth_audience="da-services-dashboard-api",
		)
		with self.assertRaises(frappe.AuthenticationError):
			charts_auth.require_charts_read()

	def test_auth_on_accepts_valid_token(self):
		_set_charts_auth(
			charts_auth_enabled=1,
			charts_auth_issuer="https://kc.example/realms/staff",
			charts_auth_audience="da-services-dashboard-api",
		)
		claims = {
			"iss": "https://kc.example/realms/staff",
			"azp": "da-services-dashboard-api",
			"resource_access": {"da-services-dashboard-api": {"roles": [C.CHARTS_READ_ROLE]}},
		}
		with (
			patch.object(charts_auth, "_bearer_token", return_value="tok"),
			patch.object(charts_auth, "verify_charts_token", return_value=claims) as verify,
		):
			self.assertEqual(charts_auth.require_charts_read(), claims)
			verify.assert_called_once_with("tok")

	def test_role_check_rejects_missing_charts_read(self):
		claims = {
			"azp": "da-services-dashboard-api",
			"resource_access": {"da-services-dashboard-api": {"roles": ["other"]}},
		}
		self.assertFalse(
			charts_auth._has_charts_role(claims, "da-services-dashboard-api", C.CHARTS_READ_ROLE)
		)

	def test_audience_accepts_azp(self):
		self.assertTrue(
			charts_auth._audience_matches(
				{"aud": "account", "azp": "da-services-dashboard-api"},
				"da-services-dashboard-api",
			)
		)

	def test_route_registered_and_guest_exempt(self):
		router.ensure_routes_registered()
		import frappe.api

		paths = {r.rule for r in frappe.api.API_URL_MAP._rules}
		self.assertIn("/api/v1/da-services/charts/visits", paths)

	def test_default_month_bounds(self):
		start, end = charts_visits._default_month_bounds(date(2026, 11, 19))
		self.assertEqual(str(start), "2026-11-01")
		self.assertEqual(str(end), "2026-11-30")
