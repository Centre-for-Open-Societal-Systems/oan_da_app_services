"""DA-226: Development Agent listing API tests (integration tests).

Tests agent listing with RBAC scope enforcement, filters, search, and pagination.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import constants as C

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
		# No role skips scope: the administrator reaches the test Regions through grants.
		_grant(ADMIN, C.ROLE_ADMIN, region_scope="ET03")
		_grant(ADMIN, C.ROLE_ADMIN, region_scope="ET04")

		# Reset mock registry to default fixtures
		MockDARegistryClient.reset()

		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA RBAC Assignment")
		frappe.db.commit()
		MockDARegistryClient.reset()

	# ========== RBAC Scope Tests ==========

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


def _post(user, da_id, kebele, state="Effective"):
	"""Create a DA Kebele Assignment posting `user` (DA `da_id`) to `kebele`."""
	doc = frappe.get_doc(
		{
			"doctype": "DA Kebele Assignment",
			"user": user,
			"da_id": da_id,
			"kebele": kebele,
			"state": state,
			"mode": "Manual",
			"effective_date": today(),
		}
	)
	doc.flags.ignore_validate = True
	doc.insert(ignore_permissions=True, ignore_links=True)
	# da_id is fetched from the User's DA Registry ID on save; pin it for the test.
	frappe.db.set_value("DA Kebele Assignment", doc.name, "da_id", da_id, update_modified=False)
	return doc


class TestMyTeamAPI(IntegrationTestCase):
	"""The signed-in user's team (GET /agents/my-team): scope by role, plus filters and facets."""

	METHOD = "da_services.api.v1.agents.list_my_team"
	USERS = (DA1, DA2, SUP_W01, SUP_W02, EXEC_ET04, ADMIN)
	# The fixture Kebeles; other suites' postings to them are cleared so the lists are exact.
	KEBELES = ("ET04-W01-K03", "ET04-W01-K07", "ET03-W02-K01")
	NOBODY = "agents-api-nobody@da.local"  # signed in, no DA role

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA RBAC Assignment")
		frappe.db.delete("DA Kebele Assignment", {"user": ("in", self.USERS)})
		frappe.db.delete("DA Kebele Assignment", {"kebele": ("in", self.KEBELES)})

		_user(DA1, [C.ROLE_DA])
		_user(DA2, [C.ROLE_DA])
		_user(SUP_W01, [C.ROLE_SUPERVISOR])
		_user(SUP_W02, [C.ROLE_SUPERVISOR])
		_user(EXEC_ET04, [C.ROLE_EXECUTIVE])
		_user(ADMIN, [C.ROLE_ADMIN])
		_user(self.NOBODY, [])

		# Registry fixtures: DA-000001 in ET04-W01-K03, DA-000002 in ET04-W01-K07,
		# DA-000003 in ET03-W02 (no Kebele), DA-000004 in ET03-W02-K01.
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA2, C.ROLE_DA, da_id="DA-000002", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP_W01, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP_W02, C.ROLE_SUPERVISOR, region_scope="ET03", woreda_scope="ET03-W02")
		_grant(EXEC_ET04, C.ROLE_EXECUTIVE, region_scope="ET04")

		MockDARegistryClient.reset()
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA RBAC Assignment")
		frappe.db.delete("DA Kebele Assignment", {"user": ("in", self.USERS)})
		frappe.db.delete("DA Kebele Assignment", {"kebele": ("in", self.KEBELES)})
		frappe.db.commit()
		MockDARegistryClient.reset()

	def _team(self, user, **kw):
		frappe.set_user(user)
		response = frappe.call(self.METHOD, **kw)
		self.assertEqual(response["status"], "success", response.get("message"))
		data = response["data"]
		data["ids"] = {a["da_id"] for a in data["agents"]}
		return data

	# ========== Scope by role ==========

	def test_admin_sees_every_da(self):
		data = self._team(ADMIN)
		self.assertEqual((data["scope"], data["areas"]), ("all", []))
		self.assertEqual(data["ids"], {"DA-000001", "DA-000002", "DA-000003", "DA-000004"})

	def test_administrator_user_sees_every_da(self):
		self.assertEqual(self._team("Administrator")["scope"], "all")

	def test_executive_sees_own_region(self):
		data = self._team(EXEC_ET04)
		self.assertEqual((data["scope"], data["areas"]), ("region", ["ET04"]))
		self.assertEqual(data["ids"], {"DA-000001", "DA-000002"})

	def test_supervisor_sees_own_woreda(self):
		data = self._team(SUP_W01)
		self.assertEqual((data["scope"], data["areas"]), ("woreda", ["ET04-W01"]))
		self.assertEqual(data["ids"], {"DA-000001", "DA-000002"})

	def test_other_woreda_supervisor(self):
		data = self._team(SUP_W02)
		self.assertEqual(data["areas"], ["ET03-W02"])
		self.assertEqual(data["ids"], {"DA-000003", "DA-000004"})

	def test_da_sees_own_kebele_from_registry(self):
		"""No Kebele Assignment: the Kebele comes from the DA's registry record."""
		data = self._team(DA1)
		self.assertEqual((data["scope"], data["areas"]), ("kebele", ["ET04-W01-K03"]))
		self.assertEqual(data["ids"], {"DA-000001"})

	def test_da_kebele_from_effective_assignment(self):
		"""An Effective Kebele Assignment wins over the registry posting, and its DA joins the team."""
		_post(DA2, "DA-000002", "ET04-W01-K03")
		frappe.db.commit()

		data = self._team(DA2)
		self.assertEqual(data["areas"], ["ET04-W01-K03"])
		self.assertEqual(data["ids"], {"DA-000001", "DA-000002"})
		self.assertEqual(self._team(DA1)["ids"], {"DA-000001", "DA-000002"})

	def test_superseded_assignment_is_ignored(self):
		_post(DA2, "DA-000002", "ET04-W01-K03", state="Superseded")
		frappe.db.commit()
		self.assertEqual(self._team(DA1)["ids"], {"DA-000001"})

	def test_assigned_da_joins_woreda(self):
		"""A DA with an Effective Kebele Assignment in the Woreda is listed even if the registry posts them elsewhere."""
		doc = _post(SUP_W02, "DA-000003", "ET04-W01-K03")
		frappe.db.set_value("DA Kebele Assignment", doc.name, "woreda", "ET04-W01", update_modified=False)
		frappe.db.commit()
		self.assertEqual(self._team(SUP_W01)["ids"], {"DA-000001", "DA-000002", "DA-000003"})

	def test_widest_role_wins(self):
		"""A Supervisor who is also a DA gets the Woreda, not the Kebele."""
		_user(SUP_W01, [C.ROLE_DA])
		data = self._team(SUP_W01)
		self.assertEqual(data["scope"], "woreda")
		self.assertEqual(data["ids"], {"DA-000001", "DA-000002"})

	def test_woreda_from_rbac_grant(self):
		"""No DA Woreda User Permission: the Woreda comes from the active RBAC grant."""
		frappe.db.delete("User Permission", {"user": SUP_W01, "allow": "DA Woreda"})
		frappe.db.commit()
		frappe.clear_cache(user=SUP_W01)
		self.assertEqual(self._team(SUP_W01)["areas"], ["ET04-W01"])

	def test_no_role_gets_empty_team(self):
		data = self._team(self.NOBODY)
		self.assertEqual((data["scope"], data["areas"], data["total"]), ("none", [], 0))
		self.assertEqual(data["agents"], [])

	# ========== Filters / facets / search / paging on the team ==========

	def test_filters_narrow_the_team(self):
		self.assertEqual(self._team(SUP_W01, kebele="ET04-W01-K07")["ids"], {"DA-000002"})
		self.assertEqual(self._team(SUP_W02, active_status="Active")["ids"], {"DA-000004"})
		self.assertEqual(
			self._team(SUP_W01, active_status="Active,On-leave")["ids"], {"DA-000001", "DA-000002"}
		)

	def test_filters_cannot_widen_the_team(self):
		"""A filter for an area outside the team returns nothing, never other DAs."""
		self.assertEqual(self._team(SUP_W01, woreda="ET03-W02")["ids"], set())
		self.assertEqual(self._team(DA1, kebele="ET03-W02-K01")["ids"], set())

	def test_facets_cover_only_the_team(self):
		facets = self._team(SUP_W01, limit=1)["facets"]
		self.assertEqual({f["value"] for f in facets["woreda"]}, {"ET04-W01"})
		self.assertEqual(
			{f["value"]: f["count"] for f in facets["kebele"]}, {"ET04-W01-K03": 1, "ET04-W01-K07": 1}
		)

	def test_search(self):
		self.assertEqual(self._team(SUP_W01, search="000002")["ids"], {"DA-000002"})
		self.assertEqual(self._team(DA1, search="marta")["ids"], set())

	def test_pagination(self):
		data = self._team(SUP_W01, limit=1, start=1)
		self.assertEqual(data["total"], 2)
		self.assertEqual([a["da_id"] for a in data["agents"]], ["DA-000002"])
		self.assertEqual(data["page"], {"limit": 1, "start": 1})

	def test_invalid_filter_value(self):
		frappe.set_user(SUP_W01)
		response = frappe.call(self.METHOD, education_tier="Bogus")
		self.assertEqual(response["status"], "error")
		self.assertIn("education_tier", response["message"].lower())


class TestAgentFiltersAPI(IntegrationTestCase):
	"""Admin's agent list: multi-value filters and the filter facets (GET /agents)."""

	METHOD = "da_services.api.v1.agents.list_agents"

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA RBAC Assignment")
		_user(ADMIN, [C.ROLE_ADMIN])
		_user(SUP_W01, [C.ROLE_SUPERVISOR])
		_grant(SUP_W01, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		MockDARegistryClient.reset()
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA RBAC Assignment")
		frappe.db.commit()
		MockDARegistryClient.reset()

	def _ids(self, **kw):
		response = frappe.call(self.METHOD, **kw)
		self.assertEqual(response["status"], "success", response.get("message"))
		return {a["da_id"] for a in response["data"]["agents"]}

	# ========== Admin sees all ==========

	def test_admin_sees_every_da(self):
		"""Admin: every DA the registry returns, across Regions."""
		frappe.set_user(ADMIN)
		self.assertEqual(self._ids(), {"DA-000001", "DA-000002", "DA-000003", "DA-000004"})

	# ========== Multi-value filters ==========

	def test_several_woredas(self):
		"""Comma-separated values: a DA matches any of them."""
		frappe.set_user(ADMIN)
		self.assertEqual(
			self._ids(woreda="ET04-W01,ET03-W02"), {"DA-000001", "DA-000002", "DA-000003", "DA-000004"}
		)
		self.assertEqual(self._ids(kebele="ET04-W01-K03,ET03-W02-K01"), {"DA-000001", "DA-000004"})

	def test_several_statuses(self):
		frappe.set_user(ADMIN)
		self.assertEqual(self._ids(active_status="On-leave,Separated"), {"DA-000002", "DA-000003"})

	def test_unassigned_kebele(self):
		"""'unassigned' matches a DA with no Kebele posting."""
		frappe.set_user(ADMIN)
		self.assertEqual(self._ids(kebele="unassigned"), {"DA-000003"})

	def test_filters_combine(self):
		"""Different filters narrow together (AND)."""
		frappe.set_user(ADMIN)
		self.assertEqual(self._ids(region="ET03", active_status="Active"), {"DA-000004"})

	def test_approval_status_filter(self):
		frappe.set_user(ADMIN)
		# DA-000004 is "Awaiting review" in the fixtures, the others "Published"
		self.assertEqual(self._ids(approval_status="Awaiting review"), {"DA-000004"})
		self.assertEqual(self._ids(approval_status="Published"), {"DA-000001", "DA-000002", "DA-000003"})
		self.assertEqual(self._ids(approval_status="Draft"), set())

	def test_invalid_value_in_list(self):
		"""One bad value in a list is refused."""
		frappe.set_user(ADMIN)
		response = frappe.call(self.METHOD, active_status="Active,Retired")
		self.assertEqual(response["status"], "error")
		self.assertIn("retired", response["message"].lower())

	# ========== Facets ==========

	def test_facets_count_every_da(self):
		"""Facets count the whole list, not just the page."""
		frappe.set_user(ADMIN)
		response = frappe.call(self.METHOD, limit=1)
		facets = response["data"]["facets"]

		self.assertEqual(len(response["data"]["agents"]), 1)
		self.assertEqual({f["value"]: f["count"] for f in facets["region"]}, {"ET03": 2, "ET04": 2})
		self.assertEqual(sum(f["count"] for f in facets["active_status"]), 4)
		self.assertIn({"value": "unassigned", "label": "Unassigned", "count": 1}, facets["kebele"])

	def test_geo_facets_cascade(self):
		"""Woreda facet follows the chosen Region; Kebele facet the chosen Woreda."""
		frappe.set_user(ADMIN)
		facets = frappe.call(self.METHOD, region="ET04")["data"]["facets"]

		self.assertEqual({f["value"] for f in facets["region"]}, {"ET03", "ET04"})
		self.assertEqual({f["value"] for f in facets["woreda"]}, {"ET04-W01"})
		self.assertEqual({f["value"] for f in facets["kebele"]}, {"ET04-W01-K03", "ET04-W01-K07"})

	def test_facets_follow_search(self):
		frappe.set_user(ADMIN)
		facets = frappe.call(self.METHOD, search="hana")["data"]["facets"]
		self.assertEqual([f["value"] for f in facets["region"]], ["ET03"])
