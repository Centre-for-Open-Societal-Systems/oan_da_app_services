"""DA-226: Development Agent listing API tests (integration tests).

Tests agent listing with RBAC scope enforcement, filters, search, and pagination.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.integrations.da_registry.mock import FIXTURE_DAS, MockDARegistryClient
from da_services.services import constants as C
from da_services.utils.permissions import get_scope_context

# Test users
DA1 = "agents-api-da1@da.local"  # DA-000001, ET04 / ET04-W01
DA2 = "agents-api-da2@da.local"  # DA-000002, ET04 / ET04-W01
SUP_W01 = "agents-api-sup-w01@da.local"  # Supervisor ET04 / ET04-W01
SUP_W02 = "agents-api-sup-w02@da.local"  # Supervisor ET03 / ET03-W02
EXEC_ET04 = "agents-api-exec-et04@da.local"  # Executive ET04
EXEC_ET03 = "agents-api-exec-et03@da.local"  # Executive ET03
ADMIN = "agents-api-admin@da.local"  # Administrator


def _user(email, roles):
	"""Create or get user with roles."""
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = email.split("@")[0]
		u.send_welcome_email = 0
		u.insert(ignore_permissions=True, ignore_links=True)
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
	doc.insert(ignore_permissions=True, ignore_links=True)
	return doc


class TestAgentsAPI(IntegrationTestCase):
	"""Test DA-226 agent listing API endpoint."""

	def setUp(self):
		"""Set up test users and RBAC grants."""
		frappe.set_user("Administrator")

		# Clean up
		frappe.db.delete("DA RBAC Assignment")

		# Create users
		_user(DA1, [C.ROLE_DA])
		_user(DA2, [C.ROLE_DA])
		_user(SUP_W01, [C.ROLE_SUPERVISOR])
		_user(SUP_W02, [C.ROLE_SUPERVISOR])
		_user(EXEC_ET04, [C.ROLE_EXECUTIVE])
		_user(EXEC_ET03, [C.ROLE_EXECUTIVE])
		_user(ADMIN, [C.ROLE_ADMIN])

		# Create RBAC grants
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA2, C.ROLE_DA, da_id="DA-000002", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP_W01, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP_W02, C.ROLE_SUPERVISOR, region_scope="ET03", woreda_scope="ET03-W02")
		_grant(EXEC_ET04, C.ROLE_EXECUTIVE, region_scope="ET04")
		_grant(EXEC_ET03, C.ROLE_EXECUTIVE, region_scope="ET03")
		_grant(ADMIN, C.ROLE_ADMIN)

		# Reset mock registry to default fixtures
		MockDARegistryClient.reset()

		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA RBAC Assignment")
		frappe.db.commit()
		MockDARegistryClient.reset()

	# ========== RBAC Scope Tests ==========

	def test_da_sees_only_self(self):
		"""DA role: User sees only their own DA record."""
		frappe.set_user(DA1)
		response = frappe.call("da_services.api.v1.agents.list_agents")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]
		total = response["data"]["total"]

		self.assertEqual(total, 1)
		self.assertEqual(len(agents), 1)
		self.assertEqual(agents[0]["da_id"], "DA-000001")
		self.assertEqual(agents[0]["full_name"], "Abebe Kebede")

	def test_da_cannot_see_other_das(self):
		"""DA role: User cannot see other DAs even in same Woreda."""
		frappe.set_user(DA2)
		response = frappe.call("da_services.api.v1.agents.list_agents")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]

		# DA2 should only see themselves
		self.assertEqual(len(agents), 1)
		self.assertEqual(agents[0]["da_id"], "DA-000002")

	def test_supervisor_sees_woreda(self):
		"""Supervisor role: User sees all DAs in their Woreda."""
		frappe.set_user(SUP_W01)
		response = frappe.call("da_services.api.v1.agents.list_agents")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]
		total = response["data"]["total"]

		# ET04-W01 has DA-000001 and DA-000002
		self.assertEqual(total, 2)
		self.assertEqual(len(agents), 2)
		agent_ids = {a["da_id"] for a in agents}
		self.assertEqual(agent_ids, {"DA-000001", "DA-000002"})

	def test_supervisor_cannot_see_other_woreda(self):
		"""Supervisor role: User cannot see DAs in other Woredas."""
		frappe.set_user(SUP_W02)
		response = frappe.call("da_services.api.v1.agents.list_agents")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]

		# ET03-W02 has DA-000003 and DA-000004
		self.assertEqual(len(agents), 2)
		agent_ids = {a["da_id"] for a in agents}
		self.assertEqual(agent_ids, {"DA-000003", "DA-000004"})

	def test_executive_sees_region(self):
		"""Executive role: User sees all DAs in their Region."""
		frappe.set_user(EXEC_ET04)
		response = frappe.call("da_services.api.v1.agents.list_agents")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]
		total = response["data"]["total"]

		# ET04 region has DA-000001 and DA-000002
		self.assertEqual(total, 2)
		agent_ids = {a["da_id"] for a in agents}
		self.assertEqual(agent_ids, {"DA-000001", "DA-000002"})

	def test_executive_cannot_see_other_region(self):
		"""Executive role: User cannot see DAs in other Regions."""
		frappe.set_user(EXEC_ET03)
		response = frappe.call("da_services.api.v1.agents.list_agents")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]

		# ET03 region has DA-000003 and DA-000004
		agent_ids = {a["da_id"] for a in agents}
		self.assertEqual(agent_ids, {"DA-000003", "DA-000004"})

	def test_admin_sees_all(self):
		"""Admin role: User sees all DAs (unrestricted)."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents")

		self.assertEqual(response["status"], "success")
		total = response["data"]["total"]

		# Admin sees all 4 fixture DAs
		self.assertEqual(total, 4)

	def test_unrestricted_wins_over_development_agent_role(self):
		"""Administrator also holds Development Agent, and must still see every DA."""
		_user(ADMIN, [C.ROLE_DA])
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents")

		self.assertEqual(response["status"], "success")
		self.assertEqual(response["data"]["total"], 4)

	# ========== Filter Tests ==========

	def test_filter_by_woreda(self):
		"""Filter: woreda parameter filters DAs correctly."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents", woreda="ET04-W01")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]

		# Only DA-000001 and DA-000002 are in ET04-W01
		self.assertEqual(len(agents), 2)
		agent_ids = {a["da_id"] for a in agents}
		self.assertEqual(agent_ids, {"DA-000001", "DA-000002"})

	def test_filter_by_region(self):
		"""Filter: region parameter filters DAs correctly."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents", region="ET03")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]

		# Only DA-000003 and DA-000004 are in ET03
		self.assertEqual(len(agents), 2)
		agent_ids = {a["da_id"] for a in agents}
		self.assertEqual(agent_ids, {"DA-000003", "DA-000004"})

	def test_filter_by_kebele(self):
		"""Filter: kebele parameter filters DAs correctly."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents", kebele="ET04-W01-K03")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]

		# Only DA-000001 is in ET04-W01-K03
		self.assertEqual(len(agents), 1)
		self.assertEqual(agents[0]["da_id"], "DA-000001")

	def test_filter_by_active_status(self):
		"""Filter: active_status parameter filters DAs correctly."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents", active_status="Active")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]

		# DA-000001 and DA-000004 are Active
		agent_ids = {a["da_id"] for a in agents}
		self.assertEqual(agent_ids, {"DA-000001", "DA-000004"})

	def test_filter_by_fayda_status(self):
		"""Filter: fayda_status parameter filters DAs correctly."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents", fayda_status="Verified")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]

		# DA-000001, DA-000002, DA-000003 are Verified
		agent_ids = {a["da_id"] for a in agents}
		self.assertEqual(agent_ids, {"DA-000001", "DA-000002", "DA-000003"})

	def test_filter_by_education_tier(self):
		"""Filter: education_tier parameter filters DAs correctly."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents", education_tier="Degree")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]

		# DA-000002 and DA-000004 have Degree
		agent_ids = {a["da_id"] for a in agents}
		self.assertEqual(agent_ids, {"DA-000002", "DA-000004"})

	def test_combined_filters(self):
		"""Filter: Multiple filters can be combined (AND logic)."""
		frappe.set_user(ADMIN)
		response = frappe.call(
			"da_services.api.v1.agents.list_agents",
			active_status="Active",
			fayda_status="Verified",
			region="ET04",
		)

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]

		# Only DA-000001 matches all three: Active + Verified + ET04
		self.assertEqual(len(agents), 1)
		self.assertEqual(agents[0]["da_id"], "DA-000001")

	def test_filters_are_independently_resettable(self):
		"""Filter: Filters are stateless (no session persistence)."""
		frappe.set_user(ADMIN)

		# First call with filter
		response1 = frappe.call("da_services.api.v1.agents.list_agents", active_status="Active")
		self.assertEqual(len(response1["data"]["agents"]), 2)

		# Second call without filter - should not retain previous filter
		response2 = frappe.call("da_services.api.v1.agents.list_agents")
		self.assertEqual(response2["data"]["total"], 4)

	def test_invalid_active_status_validation(self):
		"""Validation: Invalid active_status returns an error response."""
		frappe.set_user(ADMIN)

		response = frappe.call("da_services.api.v1.agents.list_agents", active_status="Invalid")
		self.assertEqual(response["status"], "error")
		self.assertIn("active_status", response["message"].lower())

	def test_invalid_fayda_status_validation(self):
		"""Validation: Invalid fayda_status returns an error response."""
		frappe.set_user(ADMIN)

		response = frappe.call("da_services.api.v1.agents.list_agents", fayda_status="BadValue")
		self.assertEqual(response["status"], "error")
		self.assertIn("fayda_status", response["message"].lower())

	def test_invalid_education_tier_validation(self):
		"""Validation: Invalid education_tier returns an error response."""
		frappe.set_user(ADMIN)

		response = frappe.call("da_services.api.v1.agents.list_agents", education_tier="Unknown")
		self.assertEqual(response["status"], "error")
		self.assertIn("education_tier", response["message"].lower())

	# ========== Search Tests ==========

	def test_search_by_da_id(self):
		"""Search: Finds DA by DA-ID (case-insensitive partial match)."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents", search="DA-000001")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]

		self.assertEqual(len(agents), 1)
		self.assertEqual(agents[0]["da_id"], "DA-000001")

	def test_search_by_da_id_partial(self):
		"""Search: Partial DA-ID match works."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents", search="0000")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]

		# All 4 fixture DAs match "0000"
		self.assertEqual(len(agents), 4)

	def test_search_by_name(self):
		"""Search: Finds DA by full name (case-insensitive partial match)."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents", search="Abebe")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]

		self.assertEqual(len(agents), 1)
		self.assertEqual(agents[0]["full_name"], "Abebe Kebede")

	def test_search_case_insensitive(self):
		"""Search: Search is case-insensitive."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents", search="MARTA")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]

		self.assertEqual(len(agents), 1)
		self.assertEqual(agents[0]["full_name"], "Marta Tesfaye")

	def test_search_with_filter(self):
		"""Search: Search can be combined with filters."""
		frappe.set_user(ADMIN)
		response = frappe.call(
			"da_services.api.v1.agents.list_agents", search="DA-0000", active_status="Active"
		)

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]

		# DA-000001 and DA-000004 are Active, both match "DA-0000"
		self.assertEqual(len(agents), 2)

	def test_search_respects_rbac_scope(self):
		"""Search: Search results are limited by RBAC scope."""
		frappe.set_user(SUP_W01)
		response = frappe.call("da_services.api.v1.agents.list_agents", search="DA-")

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]

		# Supervisor sees only DAs in their Woreda
		agent_ids = {a["da_id"] for a in agents}
		self.assertEqual(agent_ids, {"DA-000001", "DA-000002"})

	# ========== Pagination Tests ==========

	def test_pagination_default(self):
		"""Pagination: Default limit is 50, start is 0."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents")

		self.assertEqual(response["status"], "success")
		page = response["data"]["page"]

		self.assertEqual(page["limit"], 50)
		self.assertEqual(page["start"], 0)

	def test_pagination_custom_limit(self):
		"""Pagination: Custom limit is respected."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents", limit=2)

		self.assertEqual(response["status"], "success")
		agents = response["data"]["agents"]
		total = response["data"]["total"]

		self.assertEqual(len(agents), 2)
		self.assertEqual(total, 4)  # Total count is unchanged

	def test_pagination_offset(self):
		"""Pagination: Offset (start) skips records correctly."""
		frappe.set_user(ADMIN)

		# First page
		response1 = frappe.call("da_services.api.v1.agents.list_agents", limit=2, start=0)
		page1_ids = [a["da_id"] for a in response1["data"]["agents"]]

		# Second page
		response2 = frappe.call("da_services.api.v1.agents.list_agents", limit=2, start=2)
		page2_ids = [a["da_id"] for a in response2["data"]["agents"]]

		# No overlap between pages
		self.assertEqual(len(page1_ids), 2)
		self.assertEqual(len(page2_ids), 2)
		self.assertEqual(set(page1_ids) & set(page2_ids), set())

	def test_pagination_max_limit(self):
		"""Pagination: Limit is capped at 500."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents", limit=1000)

		self.assertEqual(response["status"], "success")
		page = response["data"]["page"]

		# Limit capped at 500
		self.assertEqual(page["limit"], 500)

	def test_pagination_min_limit(self):
		"""Pagination: Limit less than 1 defaults to 50."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents", limit=0)

		self.assertEqual(response["status"], "success")
		page = response["data"]["page"]

		self.assertEqual(page["limit"], 50)

	def test_pagination_negative_start(self):
		"""Pagination: Negative start is reset to 0."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents", start=-10)

		self.assertEqual(response["status"], "success")
		page = response["data"]["page"]

		self.assertEqual(page["start"], 0)

	# ========== Response Format Tests ==========

	def test_response_structure(self):
		"""Response: Correct structure with agents, total, and page."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents")

		self.assertEqual(response["status"], "success")
		self.assertIn("data", response)
		self.assertIn("agents", response["data"])
		self.assertIn("total", response["data"])
		self.assertIn("page", response["data"])
		self.assertIn("limit", response["data"]["page"])
		self.assertIn("start", response["data"]["page"])

	def test_agent_fields(self):
		"""Response: Agent objects contain all required fields."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents", limit=1)

		agents = response["data"]["agents"]
		self.assertEqual(len(agents), 1)

		agent = agents[0]
		required_fields = [
			"da_id",
			"full_name",
			"active_status",
			"approval_status",
			"region",
			"zone",
			"woreda",
			"kebele",
			"education_tier",
			"fayda_status",
			"phone",
			"specialisation",
			"joined_at",
			"farmer_count",
		]

		for field in required_fields:
			self.assertIn(field, agent)

	def test_sorted_by_da_id(self):
		"""Response: Agents are sorted by DA-ID for consistent ordering."""
		frappe.set_user(ADMIN)
		response = frappe.call("da_services.api.v1.agents.list_agents")

		agents = response["data"]["agents"]
		da_ids = [a["da_id"] for a in agents]

		# Should be sorted
		self.assertEqual(da_ids, sorted(da_ids))
