"""Tests for cross-DA visit outcome visibility (DA-211 Phase 6).

Visibility comes from ``da_visit_query_conditions`` / ``has_da_visit_permission``
via ``frappe.get_list`` / ``has_permission`` — not from duplicated service filters.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.services import constants as C
from da_services.services import visit_specializations as spec_svc
from da_services.services import visits as svc
from da_services.tests.fixtures import ensure_da_user


def _grant(user: str, role: str, **kw):
	doc = frappe.new_doc("DA RBAC Assignment")
	doc.user = user
	doc.role = role
	doc.effective_from = today()
	for k, v in kw.items():
		setattr(doc, k, v)
	doc.insert(ignore_permissions=True, ignore_links=True)
	return doc


class TestCrossDAVisitVisibility(IntegrationTestCase):
	"""Test DAs can see other DAs' visits in same Kebele."""

	def setUp(self):
		"""Set up test data."""
		frappe.set_user("Administrator")

		k_rows = frappe.get_all(
			"DA Kebele",
			filters={"is_active": 1},
			fields=["name", "woreda"],
			order_by="name asc",
			limit=20,
		)
		self.woreda = k_rows[0].woreda
		same = [r.name for r in k_rows if r.woreda == self.woreda]
		other = [r.name for r in k_rows if r.woreda != self.woreda]
		if len(same) < 2 or not other:
			# Fall back: pull an extra kebele from the same / other woreda.
			same = frappe.get_all(
				"DA Kebele",
				filters={"woreda": self.woreda, "is_active": 1},
				pluck="name",
				order_by="name asc",
				limit=2,
			)
			other = frappe.get_all(
				"DA Kebele",
				filters={"woreda": ("!=", self.woreda), "is_active": 1},
				pluck="name",
				order_by="name asc",
				limit=1,
			)
		self.kebele_a = same[0]
		self.kebele_b = other[0]
		self.region = frappe.db.get_value(
			"DA Zone", frappe.db.get_value("DA Woreda", self.woreda, "zone"), "region"
		)

		self.da1 = "DA-VIS-001"
		self.da2 = "DA-VIS-002"
		self.da3 = "DA-VIS-003"  # Different Kebele

		self.farmer_kebele_a = "FARMER-VIS-A-001"
		self.farmer_kebele_b = "FARMER-VIS-B-001"

		frappe.db.delete("DA Visit", {"da_id": ["in", [self.da1, self.da2, self.da3]]})
		frappe.db.delete("DA Kebele Assignment", {"da_id": ["in", [self.da1, self.da2, self.da3]]})
		frappe.db.delete(
			"DA RBAC Assignment",
			{"da_id": ["in", [self.da1, self.da2, self.da3]]},
		)

		self.user_da1 = ensure_da_user(self.da1)
		self.user_da2 = ensure_da_user(self.da2)
		self.user_da3 = ensure_da_user(self.da3)
		for email in (self.user_da1, self.user_da2, self.user_da3):
			u = frappe.get_doc("User", email)
			if C.ROLE_DA not in frappe.get_roles(email):
				u.add_roles(C.ROLE_DA)

		_grant(
			self.user_da1,
			C.ROLE_DA,
			da_id=self.da1,
			region_scope=self.region,
			woreda_scope=self.woreda,
		)
		_grant(
			self.user_da2,
			C.ROLE_DA,
			da_id=self.da2,
			region_scope=self.region,
			woreda_scope=self.woreda,
		)
		_grant(
			self.user_da3,
			C.ROLE_DA,
			da_id=self.da3,
			region_scope=self.region,
			woreda_scope=self.woreda,
		)

		for da_id, kebele in [
			(self.da1, self.kebele_a),
			(self.da2, self.kebele_a),
			(self.da3, self.kebele_b),
		]:
			frappe.get_doc(
				{
					"doctype": "DA Kebele Assignment",
					"user": ensure_da_user(da_id),
					"da_id": da_id,
					"kebele": kebele,
					"state": "Effective",
					"mode": "Automatic",
					"effective_date": "2026-10-01",
					"woreda": frappe.db.get_value("DA Kebele", kebele, "woreda"),
				}
			).insert(ignore_permissions=True, ignore_links=True)

		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def tearDown(self):
		"""Clean up test data."""
		frappe.set_user("Administrator")
		frappe.db.delete("DA Visit", {"da_id": ["in", [self.da1, self.da2, self.da3, "DA-NO-ASSIGN"]]})
		frappe.db.delete(
			"DA Kebele Assignment", {"da_id": ["in", [self.da1, self.da2, self.da3, "DA-NO-ASSIGN"]]}
		)
		frappe.db.delete(
			"DA RBAC Assignment",
			{"da_id": ["in", [self.da1, self.da2, self.da3, "DA-NO-ASSIGN"]]},
		)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def _user_for(self, da_id: str) -> str:
		return {
			self.da1: self.user_da1,
			self.da2: self.user_da2,
			self.da3: self.user_da3,
		}.get(da_id) or ensure_da_user(da_id)

	def _insert_visit(
		self,
		da_id: str,
		farmer_id: str,
		*,
		kebele: str | None = None,
		woreda: str | None = None,
		status: str = "Planned",
		visit_type: str = "Crop Survey",
		days: int = 1,
		specs: list[str] | None = None,
	):
		doc = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"user": self._user_for(da_id),
				"da_id": da_id,
				"farmer_id": farmer_id,
				"visit_type": visit_type,
				"physical_status": status,
				"scheduled_at": datetime.now() + timedelta(days=days),
				"kebele": kebele,
				"woreda": woreda,
				"region": self.region,
			}
		)
		spec_svc.set_visit_specializations(doc, specs or ["Crop Production"])
		if status not in ("Planned", "Confirmed", "In progress"):
			doc.flags.ignore_validate = True
		doc.insert(ignore_permissions=True, ignore_links=True)
		return doc

	def test_da_sees_own_visit(self):
		"""Test DA sees their own visit."""
		visit1 = self._insert_visit(self.da1, self.farmer_kebele_a, kebele=self.kebele_a)

		frappe.set_user(self.user_da1)
		visits, total = svc.list_visits()

		visit_ids = [v["name"] for v in visits]
		self.assertIn(visit1.name, visit_ids)
		self.assertGreaterEqual(total, 1)

	def test_da_sees_other_da_visit_same_kebele(self):
		"""Test DA sees another DA's visit in same Kebele."""
		visit1 = self._insert_visit(
			self.da1,
			self.farmer_kebele_a,
			kebele=self.kebele_a,
			status="Physical Visit Complete",
			days=-1,
		)

		frappe.set_user(self.user_da2)
		visits, _total = svc.list_visits()

		visit_ids = [v["name"] for v in visits]
		self.assertIn(visit1.name, visit_ids)

	def test_da_does_not_see_other_kebele_visit(self):
		"""Test DA does not see visits from other Kebele."""
		visit1 = self._insert_visit(self.da1, self.farmer_kebele_a, kebele=self.kebele_a)

		frappe.set_user(self.user_da3)
		visits, _total = svc.list_visits()

		visit_ids = [v["name"] for v in visits]
		self.assertNotIn(visit1.name, visit_ids)

	def test_both_das_see_both_visits_same_kebele(self):
		"""Test both DAs in same Kebele see each other's visits."""
		visit1 = self._insert_visit(self.da1, self.farmer_kebele_a, kebele=self.kebele_a)
		visit2 = self._insert_visit(
			self.da2,
			self.farmer_kebele_a,
			kebele=self.kebele_a,
			visit_type="Livestock Survey",
			days=2,
			specs=["Livestock"],
		)

		frappe.set_user(self.user_da1)
		visits1, _ = svc.list_visits()
		visit1_ids = [v["name"] for v in visits1]
		self.assertIn(visit1.name, visit1_ids)
		self.assertIn(visit2.name, visit1_ids)

		frappe.set_user(self.user_da2)
		visits2, _ = svc.list_visits()
		visit2_ids = [v["name"] for v in visits2]
		self.assertIn(visit1.name, visit2_ids)
		self.assertIn(visit2.name, visit2_ids)

	def test_da_no_kebele_assignment_sees_own_visits(self):
		"""Without an Effective Kebele assignment, a DA still sees their own visits."""
		da_id = "DA-NO-ASSIGN"
		user = ensure_da_user(da_id)
		u = frappe.get_doc("User", user)
		if C.ROLE_DA not in frappe.get_roles(user):
			u.add_roles(C.ROLE_DA)
		_grant(user, C.ROLE_DA, da_id=da_id, region_scope=self.region, woreda_scope=self.woreda)

		own = self._insert_visit(da_id, self.farmer_kebele_a)

		frappe.set_user(user)
		visits, total = svc.list_visits(da_id=da_id)
		self.assertIn(own.name, [v["name"] for v in visits])
		self.assertGreaterEqual(total, 1)

		frappe.set_user("Administrator")
		other = self._insert_visit(self.da1, self.farmer_kebele_a, kebele=self.kebele_a, days=2)
		frappe.set_user(user)
		visits, _ = svc.list_visits(da_id=da_id)
		self.assertNotIn(other.name, [v["name"] for v in visits])

	def test_da_id_filter_returns_own_visit_without_kebele(self):
		"""``da_id`` lists that DA's scheduled visits even when Kebele was never filled."""
		visit = self._insert_visit(self.da1, self.farmer_kebele_a)

		frappe.set_user(self.user_da1)
		visits, total = svc.list_visits(da_id=self.da1)
		self.assertIn(visit.name, [v["name"] for v in visits])
		self.assertGreaterEqual(total, 1)

	def test_peer_visit_is_readable_but_not_writable(self):
		"""Same-Kebele peer visits are readable; start/cancel stay on own DA-ID."""
		visit = self._insert_visit(self.da1, self.farmer_kebele_a, kebele=self.kebele_a)

		frappe.set_user(self.user_da2)
		out = svc.get_visit(visit.name)
		self.assertEqual(out["name"], visit.name)

		with self.assertRaises(frappe.PermissionError):
			svc.start_visit(visit.name)

	def test_supervisor_sees_all_woreda_visits(self):
		"""Test Supervisor sees all visits in their Woreda."""
		visit1 = self._insert_visit(self.da1, self.farmer_kebele_a, kebele=self.kebele_a, woreda=self.woreda)
		# Peer kebele in another woreda is not in supervisor scope; keep both in same woreda.
		visit2 = self._insert_visit(
			self.da2,
			self.farmer_kebele_a,
			kebele=self.kebele_a,
			woreda=self.woreda,
			visit_type="Livestock Survey",
			specs=["Livestock"],
		)

		sup = "xda-vis-sup@da.local"
		if not frappe.db.exists("User", sup):
			frappe.get_doc(
				{"doctype": "User", "email": sup, "first_name": "sup", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		u = frappe.get_doc("User", sup)
		if C.ROLE_SUPERVISOR not in frappe.get_roles(sup):
			u.add_roles(C.ROLE_SUPERVISOR)
		_grant(sup, C.ROLE_SUPERVISOR, region_scope=self.region, woreda_scope=self.woreda)

		frappe.set_user(sup)
		visits, _ = svc.list_visits()
		ids = [v["name"] for v in visits]
		self.assertIn(visit1.name, ids)
		self.assertIn(visit2.name, ids)

	def test_filter_by_farmer_respects_kebele_scope(self):
		"""Test farmer filter respects Kebele scope."""
		visit1 = self._insert_visit(self.da1, self.farmer_kebele_a, kebele=self.kebele_a)

		frappe.set_user(self.user_da2)
		visits, _ = svc.list_visits(farmer_id=self.farmer_kebele_a)

		visit_ids = [v["name"] for v in visits]
		self.assertIn(visit1.name, visit_ids)

		frappe.set_user(self.user_da3)
		visits3, _ = svc.list_visits(farmer_id=self.farmer_kebele_a)

		visit3_ids = [v["name"] for v in visits3]
		self.assertNotIn(visit1.name, visit3_ids)

	def test_status_filter_with_kebele_scope(self):
		"""Test status filter works with Kebele scoping."""
		visit_completed = self._insert_visit(
			self.da1,
			self.farmer_kebele_a,
			kebele=self.kebele_a,
			status="Physical Visit Complete",
			days=-1,
		)
		visit_planned = self._insert_visit(
			self.da1,
			self.farmer_kebele_a,
			kebele=self.kebele_a,
			visit_type="Livestock Survey",
			specs=["Livestock"],
		)

		frappe.set_user(self.user_da2)
		completed_visits, _ = svc.list_visits(status="Completed")
		completed_ids = [v["name"] for v in completed_visits]
		self.assertIn(visit_completed.name, completed_ids)
		self.assertNotIn(visit_planned.name, completed_ids)

		planned_visits, _ = svc.list_visits(status="Planned")
		planned_ids = [v["name"] for v in planned_visits]
		self.assertIn(visit_planned.name, planned_ids)
		self.assertNotIn(visit_completed.name, planned_ids)
