"""Central dashboard assignments chart feeder: row contract, counts, filters, grouping."""

from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from da_services.api import router
from da_services.api.v1 import charts as charts_api
from da_services.services import charts_assignments, charts_auth
from da_services.tests.fixtures import cleanup_test_rows, ensure_da_user, live_geo

DA_ID = "DA-CHART-ASG-001"
DA_ID_2 = "DA-CHART-ASG-002"
# A past window no other test or demo posting uses, so counts without a da_id filter stay ours.
MARCH = ("2019-03-01", "2019-03-31")
WINDOW = ("2019-03-01", "2019-04-30")


class TestChartsAssignments(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.geo = live_geo()

	def setUp(self):
		frappe.set_user("Administrator")
		self.user = ensure_da_user(DA_ID)
		self.user_2 = ensure_da_user(DA_ID_2)
		cleanup_test_rows(users=(self.user, self.user_2), da_ids=(DA_ID, DA_ID_2))
		self._seed()

	def tearDown(self):
		frappe.set_user("Administrator")
		cleanup_test_rows(users=(self.user, self.user_2), da_ids=(DA_ID, DA_ID_2))

	def _assign(
		self,
		kebele: str,
		effective_date: str,
		*,
		state: str = "Effective",
		geofence_result: str = "In-boundary",
		user: str | None = None,
		da_id: str = DA_ID,
	) -> str:
		# Through the controller, so a new decision supersedes the earlier ones as in production.
		doc = frappe.get_doc(
			{
				"doctype": "DA Kebele Assignment",
				"user": user or self.user,
				"da_id": da_id,
				"kebele": kebele,
				"state": state,
				"mode": "Manual",
				"effective_date": effective_date,
				"geofence_result": geofence_result,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	def _seed(self):
		"""DA 1: posted, flagged out of bounds on a move, then moved. DA 2: one posting elsewhere."""
		geo = self.geo
		self.first = self._assign(geo["kebele"], "2019-03-05")
		self.flagged = self._assign(
			geo["other_kebele"], "2019-03-10", state="Flagged", geofence_result="Out-of-bounds"
		)
		self.moved = self._assign(geo["other_kebele"], "2019-04-02")
		self._assign(geo["foreign_kebele"], "2019-03-20", user=self.user_2, da_id=DA_ID_2)

	def _kpi(self, **filters) -> dict:
		rows = charts_assignments.aggregate_assignments(**filters)
		self.assertEqual(len(rows), 1)
		return rows[0]

	def test_seed_follows_the_assignment_lifecycle(self):
		rows = {
			r.name: r
			for r in frappe.get_all(
				"DA Kebele Assignment", filters={"da_id": DA_ID}, fields=["name", "state", "supersedes"]
			)
		}
		self.assertEqual(rows[self.first].state, "Superseded")
		self.assertEqual(rows[self.flagged].state, "Superseded")
		self.assertEqual(rows[self.moved].state, "Effective")
		self.assertEqual(rows[self.moved].supersedes, self.flagged)
		self.assertFalse(rows[self.flagged].supersedes)  # a flag leaves the posting in force

	def test_ungrouped_row_keys_and_counts(self):
		row = self._kpi(da_id=DA_ID, from_date=WINDOW[0], to_date=WINDOW[1])
		# The dashboard relies on these names: renaming or removing one is a breaking change.
		self.assertEqual(list(row), ["assignments", "reassignments", "out_of_bounds", "superseded"])
		self.assertEqual(row, {"assignments": 3, "reassignments": 1, "out_of_bounds": 1, "superseded": 2})

	def test_date_range_is_on_effective_date_and_inclusive(self):
		row = self._kpi(da_id=DA_ID, from_date="2019-03-05", to_date="2019-03-10")
		self.assertEqual(row["assignments"], 2)
		self.assertEqual(self._kpi(da_id=DA_ID, from_date="2019-04-02")["assignments"], 1)

	def test_one_bound_is_a_single_day_and_swapped_bounds_are_reordered(self):
		self.assertEqual(self._kpi(da_id=DA_ID, to_date="2019-03-10")["assignments"], 1)
		row = self._kpi(da_id=DA_ID, from_date=WINDOW[1], to_date=WINDOW[0])
		self.assertEqual(row["assignments"], 3)

	def test_empty_and_all_filters_mean_no_filter(self):
		for filters in ({}, {"region": "all", "zone": "", "woreda": "ALL", "kebele": " "}):
			row = self._kpi(from_date=MARCH[0], to_date=MARCH[1], **filters)
			self.assertEqual(row["assignments"], 3)  # both DAs, both regions

	def test_geo_and_da_filters_narrow(self):
		geo = self.geo
		march = {"from_date": MARCH[0], "to_date": MARCH[1]}
		self.assertEqual(self._kpi(woreda=geo["woreda"], **march)["assignments"], 2)
		self.assertEqual(self._kpi(region=geo["foreign_region"], **march)["assignments"], 1)
		self.assertEqual(self._kpi(kebele=geo["foreign_kebele"], **march)["assignments"], 1)
		self.assertEqual(self._kpi(da_id=DA_ID_2, **march)["assignments"], 1)
		# Filters combine with AND: this DA has nothing in the other region.
		self.assertEqual(self._kpi(da_id=DA_ID, region=geo["foreign_region"], **march)["assignments"], 0)

	def test_ungrouped_no_match_returns_zeroed_kpi(self):
		rows = charts_assignments.aggregate_assignments(from_date="2000-01-01", to_date="2000-01-31")
		self.assertEqual(rows, [{"assignments": 0, "reassignments": 0, "out_of_bounds": 0, "superseded": 0}])

	def test_grouped_no_match_returns_empty_list(self):
		rows = charts_assignments.aggregate_assignments(
			from_date="2000-01-01", to_date="2000-01-31", group_by="month"
		)
		self.assertEqual(rows, [])

	def test_group_by_month_oldest_first(self):
		rows = charts_assignments.aggregate_assignments(
			da_id=DA_ID, from_date=WINDOW[0], to_date=WINDOW[1], group_by="month"
		)
		self.assertEqual(
			rows,
			[
				{
					"assignments": 2,
					"reassignments": 0,
					"out_of_bounds": 1,
					"superseded": 2,
					"month": "2019-03",
				},
				{
					"assignments": 1,
					"reassignments": 1,
					"out_of_bounds": 0,
					"superseded": 0,
					"month": "2019-04",
				},
			],
		)

	def test_group_by_woreda(self):
		rows = charts_assignments.aggregate_assignments(
			from_date=WINDOW[0], to_date=WINDOW[1], group_by="woreda"
		)
		by_woreda = {r["woreda"]: r for r in rows}
		self.assertEqual(by_woreda[self.geo["woreda"]]["assignments"], 3)
		self.assertEqual(by_woreda[self.geo["foreign_woreda"]]["assignments"], 1)

	def test_unknown_group_by_is_ignored(self):
		row = self._kpi(da_id=DA_ID, from_date=WINDOW[0], to_date=WINDOW[1], group_by="kebele_name")
		self.assertEqual(row["assignments"], 3)

	def test_api_checks_charts_read_and_returns_bare_list(self):
		frappe.set_user("Guest")  # the service account has no portal session
		frappe.local.form_dict = frappe._dict(
			{"da_id": DA_ID, "from": WINDOW[0], "to": WINDOW[1], "groupBy": "month"}
		)
		with patch.object(charts_auth, "require_charts_read") as require:
			out = charts_api.assignments_chart()
		require.assert_called_once_with()
		self.assertIsInstance(out, list)
		self.assertEqual([r["month"] for r in out], ["2019-03", "2019-04"])

	def test_api_rejects_a_call_without_token_when_charts_auth_is_on(self):
		frappe.set_user("Guest")
		with (
			patch.object(charts_auth, "charts_auth_enabled", return_value=True),
			patch.object(charts_auth, "_bearer_token", return_value=None),
			patch.object(charts_assignments, "aggregate_assignments") as aggregate,
			self.assertRaises(frappe.AuthenticationError),
		):
			charts_api.assignments_chart()
		aggregate.assert_not_called()  # nothing is read before the token is checked

	def test_route_registered(self):
		router.ensure_routes_registered()
		import frappe.api

		paths = {r.rule for r in frappe.api.API_URL_MAP._rules}
		self.assertIn("/api/v1/da-services/charts/assignments", paths)
