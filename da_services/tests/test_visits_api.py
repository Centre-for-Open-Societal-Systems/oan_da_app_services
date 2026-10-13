"""DA-42: Visit list API with role-based scope selector (API v1 endpoints)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, now_datetime, today

from da_services.services import constants as C
from da_services.services import visits
from da_services.utils.permissions import get_scope_context

# Test users
DA1 = "visit-api-da1@da.local"  # DA-000001, ET04 / ET04-W01
DA2 = "visit-api-da2@da.local"  # DA-000002, ET04 / ET04-W01
DA3 = "visit-api-da3@da.local"  # DA-000003, ET03 / ET03-W02
SUP = "visit-api-sup@da.local"  # Supervisor ET04 / ET04-W01
EXEC = "visit-api-exec@da.local"  # Executive ET03
COMMS = "visit-api-comms@da.local"  # Communications Officer


def _user(email, roles):
	"""Create or get user with roles."""
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = email.split("@")[0]
		u.send_welcome_email = 0
		u.insert(ignore_permissions=True)
	u = frappe.get_doc("User", email)
	for r in roles:
		if r not in frappe.get_roles(email):
			u.add_roles(r)
	return email


def _grant(user, role, **kw):
	"""Create RBAC assignment."""
	doc = frappe.new_doc("DA RBAC Assignment")
	doc.user = user
	doc.role = role
	doc.effective_from = today()
	for k, v in kw.items():
		setattr(doc, k, v)
	doc.insert(ignore_permissions=True)
	return doc


class TestVisitsAPI(IntegrationTestCase):
	"""Test DA-42 visit list API endpoints with scope selector and RBAC enforcement."""

	def setUp(self):
		"""Set up test users, grants, and seed visits."""
		frappe.set_user("Administrator")
		frappe.db.delete("DA Visit")
		frappe.db.delete("DA Task")
		frappe.db.delete("DA RBAC Assignment")

		# Create users
		_user(DA1, [C.ROLE_DA])
		_user(DA2, [C.ROLE_DA])
		_user(DA3, [C.ROLE_DA])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_user(EXEC, [C.ROLE_EXECUTIVE])
		_user(COMMS, [C.ROLE_COMMS])

		# Create RBAC grants
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA2, C.ROLE_DA, da_id="DA-000002", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA3, C.ROLE_DA, da_id="DA-000003", region_scope="ET03", woreda_scope="ET03-W02")
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope="ET03")
		_grant(COMMS, C.ROLE_COMMS)

		# Seed visits
		ctx = get_scope_context("Administrator")

		# DA1: 2 visits (1 today, 1 future)
		self.v1 = visits.plan_visit(
			da_id="DA-000001",
			farmer_id="F-001",
			visit_type="Crop Survey",
			purpose="Monthly crop inspection",
			scheduled_at=f"{today()} 09:00:00",
			priority="High",
			ctx=ctx,
		)
		self.v2 = visits.plan_visit(
			da_id="DA-000001",
			farmer_id="F-002",
			visit_type="Advisory",
			purpose="Pest management advice",
			scheduled_at=f"{add_days(today(), 3)} 10:00:00",
			priority="Medium",
			ctx=ctx,
		)

		# DA2: 1 visit (same woreda, today)
		self.v3 = visits.plan_visit(
			da_id="DA-000002",
			farmer_id="F-003",
			visit_type="Livestock Survey",
			purpose="Animal health check",
			scheduled_at=f"{today()} 14:00:00",
			priority="Medium",
			ctx=ctx,
		)

		# DA3: 1 visit (different region, tomorrow)
		self.v4 = visits.plan_visit(
			da_id="DA-000003",
			farmer_id="F-004",
			visit_type="Follow-up",
			purpose="Follow-up previous visit",
			scheduled_at=f"{add_days(today(), 1)} 11:00:00",
			priority="High",
			ctx=ctx,
		)

		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def tearDown(self):
		"""Clean up test data."""
		frappe.set_user("Administrator")
		frappe.db.delete("DA Visit")
		frappe.db.delete("DA Task")
		frappe.db.delete("DA RBAC Assignment")
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	# -- RBAC: Scope Selector -----------------------------------------------

	def test_da_sees_only_own_visits_my_visits_scope(self):
		"""DA role sees only their own visits (My visits scope)."""
		frappe.set_user(DA1)
		result = frappe.call("da_services.api.v1.visits.list_visits")
		data = result.get("data", {})
		visit_ids = {v["da_id"] for v in data.get("visits", [])}
		self.assertEqual(visit_ids, {"DA-000001"})
		self.assertEqual(data["total"], 2)

	def test_supervisor_sees_own_woreda_my_team_scope(self):
		"""Supervisor sees visits for DAs in their Woreda (My team scope)."""
		frappe.set_user(SUP)
		result = frappe.call("da_services.api.v1.visits.list_visits")
		data = result.get("data", {})
		visit_ids = {v["da_id"] for v in data.get("visits", [])}
		self.assertEqual(visit_ids, {"DA-000001", "DA-000002"})
		self.assertEqual(data["total"], 3)

	def test_executive_sees_own_region_region_scope(self):
		"""Executive sees visits for DAs in their Region (Region scope)."""
		frappe.set_user(EXEC)
		result = frappe.call("da_services.api.v1.visits.list_visits")
		data = result.get("data", {})
		visit_ids = {v["da_id"] for v in data.get("visits", [])}
		self.assertEqual(visit_ids, {"DA-000003"})
		self.assertEqual(data["total"], 1)

	def test_comms_officer_denied_access(self):
		"""Communications Officer is denied access (no DA identity access per FSD Appendix D)."""
		frappe.set_user(COMMS)
		result = frappe.call("da_services.api.v1.visits.list_visits")
		self.assertEqual(result.get("status"), "error")
		self.assertEqual(result.get("code"), "PERMISSION_DENIED")

	def test_administrator_sees_all_visits(self):
		"""Administrator sees all visits regardless of scope."""
		frappe.set_user("Administrator")
		result = frappe.call("da_services.api.v1.visits.list_visits")
		data = result.get("data", {})
		self.assertEqual(data["total"], 4)

	# -- Filtering ---------------------------------------------------------------

	def test_filter_by_status(self):
		"""Filter visits by physical_status."""
		frappe.set_user("Administrator")
		# All are Planned initially
		result = frappe.call("da_services.api.v1.visits.list_visits", status="Planned")
		data = result.get("data", {})
		self.assertEqual(data["total"], 4)

		# Complete one visit
		doc = frappe.get_doc("DA Visit", self.v1["name"])
		doc.flags.ignore_permissions = True
		doc.submit_outcome(outcome="Inspection completed successfully")

		result = frappe.call(
			"da_services.api.v1.visits.list_visits", status="Physical Visit Complete"
		)
		data = result.get("data", {})
		self.assertEqual(data["total"], 1)

	def test_filter_by_date_range(self):
		"""Filter visits by scheduled_at date range."""
		frappe.set_user("Administrator")
		# Today only (2 visits scheduled today)
		result = frappe.call(
			"da_services.api.v1.visits.list_visits",
			date_from=str(today()),
			date_to=str(today()),
		)
		data = result.get("data", {})
		self.assertEqual(data["total"], 2)

		# Future only (2 visits scheduled in future)
		result = frappe.call(
			"da_services.api.v1.visits.list_visits",
			date_from=str(add_days(today(), 1)),
			date_to=str(add_days(today(), 7)),
		)
		data = result.get("data", {})
		self.assertEqual(data["total"], 2)

	def test_filter_by_kebele(self):
		"""Filter visits by Kebele."""
		frappe.set_user("Administrator")
		# Set kebele on one visit
		doc = frappe.get_doc("DA Visit", self.v1["name"])
		doc.kebele = "ET04-W01-K03"
		doc.save(ignore_permissions=True)

		result = frappe.call(
			"da_services.api.v1.visits.list_visits", kebele="ET04-W01-K03"
		)
		data = result.get("data", {})
		self.assertGreaterEqual(data["total"], 1)

	def test_filter_by_da_id_agent(self):
		"""Filter visits by specific DA (agent filter)."""
		frappe.set_user("Administrator")
		result = frappe.call("da_services.api.v1.visits.list_visits", da_id="DA-000001")
		data = result.get("data", {})
		self.assertEqual(data["total"], 2)
		self.assertTrue(all(v["da_id"] == "DA-000001" for v in data["visits"]))

	def test_filter_by_farmer_id(self):
		"""Filter visits by farmer."""
		frappe.set_user("Administrator")
		result = frappe.call("da_services.api.v1.visits.list_visits", farmer_id="F-001")
		data = result.get("data", {})
		self.assertEqual(data["total"], 1)
		self.assertEqual(data["visits"][0]["farmer_id"], "F-001")

	# -- Pagination --------------------------------------------------------------

	def test_pagination_limits(self):
		"""Test pagination parameters."""
		frappe.set_user("Administrator")

		# Default pagination
		result = frappe.call("da_services.api.v1.visits.list_visits")
		data = result.get("data", {})
		self.assertEqual(data["page"]["limit"], 20)
		self.assertEqual(data["page"]["start"], 0)

		# Custom pagination
		result = frappe.call(
			"da_services.api.v1.visits.list_visits", limit=2, start=1
		)
		data = result.get("data", {})
		self.assertEqual(data["page"]["limit"], 2)
		self.assertEqual(data["page"]["start"], 1)
		self.assertLessEqual(len(data["visits"]), 2)

	def test_pagination_max_limit_enforced(self):
		"""Limit cannot exceed 500."""
		frappe.set_user("Administrator")
		result = frappe.call("da_services.api.v1.visits.list_visits", limit=501)
		self.assertEqual(result.get("status"), "error")
		self.assertEqual(result.get("code"), "VALIDATION_ERROR")

	def test_pagination_negative_start_rejected(self):
		"""Start cannot be negative."""
		frappe.set_user("Administrator")
		result = frappe.call("da_services.api.v1.visits.list_visits", start=-1)
		self.assertEqual(result.get("status"), "error")
		self.assertEqual(result.get("code"), "VALIDATION_ERROR")

	# -- Validation --------------------------------------------------------------

	def test_invalid_date_format_rejected(self):
		"""Invalid date format returns error response."""
		frappe.set_user("Administrator")
		result = frappe.call("da_services.api.v1.visits.list_visits", date_from="invalid-date")
		self.assertEqual(result.get("status"), "error")
		self.assertEqual(result.get("code"), "VALIDATION_ERROR")

	def test_invalid_status_rejected(self):
		"""Unknown status value returns error response."""
		frappe.set_user("Administrator")
		result = frappe.call("da_services.api.v1.visits.list_visits", status="InvalidStatus")
		self.assertEqual(result.get("status"), "error")
		self.assertEqual(result.get("code"), "VALIDATION_ERROR")

	def test_date_range_validation(self):
		"""date_from cannot be after date_to."""
		frappe.set_user("Administrator")
		result = frappe.call(
			"da_services.api.v1.visits.list_visits",
			date_from=str(add_days(today(), 7)),
			date_to=str(today()),
		)
		self.assertEqual(result.get("status"), "error")
		self.assertEqual(result.get("code"), "VALIDATION_ERROR")

	# -- Today's Visits ----------------------------------------------------------

	def test_today_visits_returns_only_today(self):
		"""today endpoint returns only visits scheduled today."""
		frappe.set_user("Administrator")
		result = frappe.call("da_services.api.v1.visits.today_visits")
		data = result.get("data", {})
		self.assertEqual(data["total"], 2)  # v1 and v3 are today
		self.assertEqual(data["date"], str(today()))

	def test_today_visits_respects_scope(self):
		"""today endpoint respects DA scope."""
		frappe.set_user(DA1)
		result = frappe.call("da_services.api.v1.visits.today_visits")
		data = result.get("data", {})
		self.assertEqual(data["total"], 1)  # Only v1 for DA1

	# -- Summary Tiles -----------------------------------------------------------

	def test_summary_stats_for_da(self):
		"""Summary endpoint returns dashboard stats with tiles."""
		frappe.set_user(DA1)
		result = frappe.call("da_services.api.v1.visits.visit_summary")
		data = result.get("data", {})
		self.assertIn("planned_this_week", data)
		self.assertIn("completed", data)
		self.assertIn("missed", data)
		self.assertIn("rescheduled", data)
		self.assertIn("on_time_rate", data)
		self.assertGreater(data["planned_this_week"], 0)

	def test_summary_requires_da_id_for_officers(self):
		"""Officers must provide da_id for summary."""
		frappe.set_user(SUP)
		result = frappe.call("da_services.api.v1.visits.visit_summary")
		self.assertEqual(result.get("status"), "error")
		self.assertEqual(result.get("code"), "VALIDATION_ERROR")

	def test_summary_with_date_range(self):
		"""Summary supports date range filtering."""
		frappe.set_user("Administrator")
		result = frappe.call(
			"da_services.api.v1.visits.visit_summary",
			da_id="DA-000001",
			date_from=str(today()),
			date_to=str(add_days(today(), 7)),
		)
		data = result.get("data", {})
		self.assertIsNotNone(data)
		self.assertIn("planned_this_week", data)

	def test_summary_calculates_on_time_rate(self):
		"""Summary calculates on-time rate correctly."""
		frappe.set_user("Administrator")
		# Complete a visit on time
		doc = frappe.get_doc("DA Visit", self.v1["name"])
		doc.duration_min = 60
		doc.save(ignore_permissions=True)
		doc.start_visit()
		doc.submit_outcome(outcome="Completed on time")

		result = frappe.call(
			"da_services.api.v1.visits.visit_summary",
			da_id="DA-000001",
		)
		data = result.get("data", {})
		self.assertIn("on_time_rate", data)
		self.assertGreaterEqual(data["on_time_rate"], 0.0)
		self.assertLessEqual(data["on_time_rate"], 1.0)

	# -- Single Visit (View Action) ----------------------------------------------

	def test_get_single_visit(self):
		"""Get single visit by name (View action)."""
		frappe.set_user(DA1)
		result = frappe.call("da_services.api.v1.visits.get_visit", name=self.v1["name"])
		data = result.get("data", {})
		self.assertEqual(data["name"], self.v1["name"])
		self.assertEqual(data["da_id"], "DA-000001")

	def test_get_visit_out_of_scope_denied(self):
		"""Cannot get visit outside scope."""
		frappe.set_user(DA1)
		result = frappe.call("da_services.api.v1.visits.get_visit", name=self.v4["name"])
		self.assertEqual(result.get("status"), "error")
		self.assertEqual(result.get("code"), "PERMISSION_DENIED")

	# -- Response Format ---------------------------------------------------------

	def test_response_format_consistent(self):
		"""API responses follow consistent format."""
		frappe.set_user("Administrator")
		result = frappe.call("da_services.api.v1.visits.list_visits")
		self.assertEqual(result.get("status"), "success")
		self.assertIn("data", result)
		self.assertIn("visits", result["data"])
		self.assertIn("total", result["data"])
		self.assertIn("page", result["data"])

	def test_visit_object_has_required_fields(self):
		"""Visit objects contain all table columns (Date & time, Agent, Farmer, Kebele, Purpose, Status)."""
		frappe.set_user("Administrator")
		result = frappe.call("da_services.api.v1.visits.list_visits")
		data = result.get("data", {})
		if data["visits"]:
			visit = data["visits"][0]
			# Table columns from requirement
			self.assertIn("scheduled_at", visit)  # Date & time
			self.assertIn("da_id", visit)  # Agent
			self.assertIn("farmer_id", visit)  # Farmer
			self.assertIn("kebele", visit)  # Kebele
			self.assertIn("purpose", visit)  # Purpose
			self.assertIn("physical_status", visit)  # Status
			self.assertIn("ui_status", visit)  # UI Status
