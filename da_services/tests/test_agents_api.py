"""DA-226: Development Agent listing API tests (integration tests).

Tests agent listing with RBAC scope enforcement, filters, search, and pagination.
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.da_services.doctype.da_hierarchy.da_hierarchy import ROOT, ensure_root
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import constants as C
from da_services.tests.fixtures import set_da_registry_id

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

	def test_each_agent_has_its_portal_user(self):
		"""user: the portal User bound to the DA record, null for a DA with none (survey assignees)."""
		set_da_registry_id(DA1, "DA-000001")
		frappe.db.set_value("User", {"da_registry_id": "DA-000003"}, "da_registry_id", None)
		frappe.db.commit()
		try:
			frappe.set_user(ADMIN)
			agents = frappe.call("da_services.api.v1.agents.list_agents")["data"]["agents"]
			users = {a["da_id"]: a["user"] for a in agents}
			self.assertEqual(users["DA-000001"], DA1)
			self.assertIsNone(users["DA-000003"])
		finally:
			frappe.set_user("Administrator")
			frappe.db.set_value("User", DA1, "da_registry_id", None)
			frappe.db.commit()

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
	"""The signed-in user's team from the DA Hierarchy (GET /agents/my-team and /agents/my-team/tree).

	Tree used by the tests:

	    All DA Hierarchy
	    |-- EXEC_ET04 (group)
	    |   `-- SUP_W01 (group)
	    |       |-- DA1  (DA-000001)
	    |       `-- DA2  (DA-000002)
	    |-- SUP_W02 (group)
	    |   |-- DA3  (DA-000003)
	    |   `-- DA4  (DA-000004)
	    `-- LONE (no boss, no DA record)
	"""

	METHOD = "da_services.api.v1.agents.list_my_team"
	TREE = "da_services.api.v1.agents.my_team_tree"
	DA3 = "agents-api-da3@da.local"
	DA4 = "agents-api-da4@da.local"
	LONE = "agents-api-lone@da.local"
	NOBODY = "agents-api-nobody@da.local"  # signed in, no DA Hierarchy node
	# children before parents, for teardown
	NODES = ()

	def setUp(self):
		frappe.set_user("Administrator")
		self.NODES = (DA1, DA2, self.DA3, self.DA4, self.LONE, SUP_W01, SUP_W02, EXEC_ET04)
		self._delete_nodes()

		for u, roles in (
			(DA1, [C.ROLE_DA]),
			(DA2, [C.ROLE_DA]),
			(self.DA3, [C.ROLE_DA]),
			(self.DA4, [C.ROLE_DA]),
			(self.LONE, [C.ROLE_DA]),
			(SUP_W01, [C.ROLE_SUPERVISOR]),
			(SUP_W02, [C.ROLE_SUPERVISOR]),
			(EXEC_ET04, [C.ROLE_EXECUTIVE]),
			(ADMIN, [C.ROLE_ADMIN]),
			(self.NOBODY, []),
		):
			_user(u, roles)
		for u, da_id in (
			(DA1, "DA-000001"),
			(DA2, "DA-000002"),
			(self.DA3, "DA-000003"),
			(self.DA4, "DA-000004"),
		):
			set_da_registry_id(u, da_id)

		ensure_root()
		self._node(EXEC_ET04, ROOT, group=True)
		self._node(SUP_W01, EXEC_ET04, group=True)
		self._node(DA1, SUP_W01)
		self._node(DA2, SUP_W01)
		self._node(SUP_W02, ROOT, group=True)
		self._node(self.DA3, SUP_W02)
		self._node(self.DA4, SUP_W02)
		self._node(self.LONE, ROOT)
		# The Supervisors' Woredas, on their nodes
		frappe.db.set_value("DA Hierarchy", {"user": SUP_W01}, "woreda", "ET04-W01")
		frappe.db.set_value("DA Hierarchy", {"user": SUP_W02}, "woreda", "ET03-W02")

		MockDARegistryClient.reset()
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		self._delete_nodes()
		# Free the DA-IDs again: they are unique, and the local demo users hold them
		for u in (DA1, DA2, self.DA3, self.DA4, self.LONE, SUP_W01):
			frappe.db.set_value("User", u, "da_registry_id", None)
		frappe.db.commit()
		MockDARegistryClient.reset()

	def _node(self, user, parent, group=False):
		frappe.get_doc(
			{
				"doctype": "DA Hierarchy",
				"user": user,
				"full_name": user.split("@")[0],
				"parent_da_hierarchy": parent,
				"is_group": 1 if group else 0,
			}
		).insert(ignore_permissions=True)

	def _drop_woreda_permissions(self, user):
		"""Other suites may leave a DA Woreda User Permission on the user."""
		frappe.db.delete("User Permission", {"user": user, "allow": "DA Woreda"})
		frappe.clear_cache(user=user)

	def _delete_nodes(self):
		# deepest first: a node with children cannot be deleted
		for name in frappe.get_all(
			"DA Hierarchy", filters={"user": ("in", self.NODES)}, pluck="name", order_by="lft desc"
		):
			frappe.delete_doc("DA Hierarchy", name, ignore_permissions=True, force=True)

	def _team(self, user, **kw):
		frappe.set_user(user)
		response = frappe.call(self.METHOD, **kw)
		self.assertEqual(response["status"], "success", response.get("message"))
		data = response["data"]
		data["ids"] = {a["da_id"] for a in data["agents"]}
		return data

	def _tree(self, user):
		frappe.set_user(user)
		response = frappe.call(self.TREE)
		self.assertEqual(response["status"], "success", response.get("message"))
		return response["data"]

	# ========== Team by the DA Hierarchy ==========

	def test_admin_sees_every_da(self):
		data = self._team(ADMIN)
		self.assertEqual(data["team"], "all")
		self.assertEqual(data["root"]["name"], ROOT)
		self.assertEqual(data["ids"], {"DA-000001", "DA-000002", "DA-000003", "DA-000004"})

	def test_administrator_user_sees_every_da(self):
		self.assertEqual(self._team("Administrator")["team"], "all")

	def test_supervisor_sees_their_woreda(self):
		data = self._team(SUP_W01)
		self.assertEqual(
			(data["team"], data["areas"], data["root"]["user"]), ("woreda", ["ET04-W01"], SUP_W01)
		)
		self.assertEqual(data["ids"], {"DA-000001", "DA-000002"})
		self.assertEqual(self._team(SUP_W02)["ids"], {"DA-000003", "DA-000004"})

	def test_supervisor_sees_woreda_das_who_report_elsewhere(self):
		"""The Woreda decides, not the reporting line: DA2 moved under SUP_W02 stays in W01."""
		doc = frappe.get_doc("DA Hierarchy", DA2)
		doc.parent_da_hierarchy = SUP_W02
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		self.assertEqual(self._team(SUP_W01)["ids"], {"DA-000001", "DA-000002"})
		self.assertEqual(self._team(SUP_W02)["ids"], {"DA-000003", "DA-000004"})

	def test_supervisor_woreda_falls_back_to_own_record(self):
		frappe.db.set_value("DA Hierarchy", {"user": SUP_W01}, "woreda", None)
		self._drop_woreda_permissions(SUP_W01)
		set_da_registry_id(SUP_W01, "DA-000003")
		frappe.db.commit()
		data = self._team(SUP_W01)
		self.assertEqual((data["areas"], data["ids"]), (["ET03-W02"], {"DA-000003", "DA-000004"}))

	def test_supervisor_without_woreda_gets_empty_list(self):
		frappe.db.set_value("DA Hierarchy", {"user": SUP_W01}, "woreda", None)
		self._drop_woreda_permissions(SUP_W01)
		frappe.db.commit()
		data = self._team(SUP_W01)
		self.assertEqual((data["team"], data["areas"], data["total"]), ("woreda", [], 0))

	def test_manager_sees_every_level_below(self):
		"""The Executive is above SUP_W01, so SUP_W01's DAs are in their team too."""
		data = self._team(EXEC_ET04)
		self.assertEqual(data["team"], "reports")
		self.assertEqual(data["ids"], {"DA-000001", "DA-000002"})

	def _place(self, da_id, kebele):
		MockDARegistryClient.shared()._records[da_id]["assigned_kebele"] = kebele

	def test_da_sees_their_kebele(self):
		"""A DA's list is every DA posted to their Kebele, them included, whoever their boss is."""
		self._place("DA-000002", "ET04-W01-K03")  # DA2: same Kebele as DA1
		self._place("DA-000004", "ET04-W01-K03")  # DA4: same Kebele, another boss
		data = self._team(DA1)
		self.assertEqual(
			(data["team"], data["areas"], data["root"]["user"]), ("kebele", ["ET04-W01-K03"], DA1)
		)
		self.assertEqual(data["ids"], {"DA-000001", "DA-000002", "DA-000004"})

	def test_da_alone_in_kebele_sees_only_self(self):
		data = self._team(DA1)
		self.assertEqual((data["team"], data["ids"]), ("kebele", {"DA-000001"}))

	def test_da_without_boss_sees_their_kebele(self):
		"""Straight under the root: still their Kebele, never the whole tree."""
		set_da_registry_id(self.LONE, "DA-000004")
		frappe.db.commit()
		data = self._team(self.LONE)
		self.assertEqual((data["team"], data["root"]["user"]), ("kebele", self.LONE))
		self.assertEqual(data["ids"], {"DA-000004"})

	def test_da_without_kebele_gets_empty_list(self):
		"""DA-000003 has no Kebele on the registry and none on the node."""
		data = self._team(self.DA3)
		self.assertEqual((data["team"], data["areas"], data["total"]), ("kebele", [], 0))

	def test_da_kebele_falls_back_to_node(self):
		frappe.db.set_value("DA Hierarchy", {"user": self.DA3}, "kebele", "ET04-W01-K07")
		frappe.db.commit()
		data = self._team(self.DA3)
		self.assertEqual((data["areas"], data["ids"]), (["ET04-W01-K07"], {"DA-000002"}))

	def test_filter_by_specialisation(self):
		data = self._team(ADMIN, specialisation="Crop production,Livestock")
		self.assertEqual(data["ids"], {"DA-000001", "DA-000002", "DA-000004"})
		facets = {f["value"]: f["count"] for f in self._team(SUP_W02)["facets"]["specialisation"]}
		self.assertEqual(facets, {"Natural resources": 1, "Crop production": 1})

	def test_reports_to_is_the_callers_boss(self):
		self.assertEqual(self._team(DA1)["reports_to"]["user"], SUP_W01)
		self.assertEqual(self._team(SUP_W01)["reports_to"]["user"], EXEC_ET04)
		self.assertIsNone(self._team(SUP_W02)["reports_to"])  # straight under the root
		self.assertIsNone(self._team(ADMIN)["reports_to"])
		self.assertIsNone(self._team(self.NOBODY)["reports_to"])

	def test_no_node_gets_empty_team(self):
		data = self._team(self.NOBODY)
		self.assertEqual((data["team"], data["root"], data["total"]), ("none", None, 0))

	def test_moving_a_da_changes_the_managers_team(self):
		"""Above the Supervisors the team follows the tree: DA2 moved under SUP_W02 leaves the
		Executive's team."""
		doc = frappe.get_doc("DA Hierarchy", DA2)
		doc.parent_da_hierarchy = SUP_W02
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		self.assertEqual(self._team(EXEC_ET04)["ids"], {"DA-000001"})

	# ========== Filters / facets / search / paging on the team ==========

	def test_filters_narrow_the_team(self):
		self.assertEqual(self._team(SUP_W01, kebele="ET04-W01-K07")["ids"], {"DA-000002"})
		self.assertEqual(self._team(SUP_W02, kebele="unassigned")["ids"], {"DA-000003"})

	def test_filters_cannot_widen_the_team(self):
		self.assertEqual(self._team(DA1, kebele="ET03-W02-K01")["ids"], set())
		self.assertEqual(self._team(DA1, kebele="ET04-W01-K07")["ids"], set())
		self.assertEqual(self._team(SUP_W01, region="ET03")["ids"], set())

	def test_facets_cover_only_the_team(self):
		facets = self._team(SUP_W01, limit=1)["facets"]
		self.assertEqual({f["value"] for f in facets["woreda"]}, {"ET04-W01"})
		self.assertEqual(
			{f["value"]: f["count"] for f in facets["kebele"]}, {"ET04-W01-K03": 1, "ET04-W01-K07": 1}
		)

	def test_search_and_pagination(self):
		self.assertEqual(self._team(SUP_W01, search="000002")["ids"], {"DA-000002"})
		data = self._team(SUP_W01, limit=1, start=1)
		self.assertEqual((data["total"], [a["da_id"] for a in data["agents"]]), (2, ["DA-000002"]))

	def test_invalid_filter_value(self):
		frappe.set_user(SUP_W01)
		response = frappe.call(self.METHOD, fayda_status="Bogus")
		self.assertEqual(response["status"], "error")

	def test_hidden_fields_left_out(self):
		"""active_status, education_tier and approval_status: not on agents, not in facets, not filters."""
		hidden = {"active_status", "education_tier", "approval_status"}
		data = self._team(ADMIN, active_status="Separated", education_tier="Bogus")
		self.assertEqual(data["ids"], {"DA-000001", "DA-000002", "DA-000003", "DA-000004"})
		self.assertFalse(hidden & set(data["facets"]))
		for agent in data["agents"]:
			self.assertFalse(hidden & set(agent), agent)

	# ========== Tree ==========

	def test_supervisor_tree(self):
		data = self._tree(SUP_W01)
		tree = data["tree"]
		self.assertEqual((data["team"], tree["user"], tree["is_group"]), ("reports", SUP_W01, True))
		self.assertEqual(
			{(r["user"], r["da_id"]) for r in tree["reports"]}, {(DA1, "DA-000001"), (DA2, "DA-000002")}
		)
		self.assertTrue(all(r["reports"] == [] for r in tree["reports"]))

	def test_executive_tree_is_nested(self):
		tree = self._tree(EXEC_ET04)["tree"]
		self.assertEqual([r["user"] for r in tree["reports"]], [SUP_W01])
		self.assertEqual({r["user"] for r in tree["reports"][0]["reports"]}, {DA1, DA2})

	def test_da_tree_hangs_from_boss(self):
		data = self._tree(DA2)
		self.assertEqual((data["team"], data["tree"]["user"]), ("peers", SUP_W01))
		self.assertIn(DA2, {r["user"] for r in data["tree"]["reports"]})

	def test_admin_tree_is_the_whole_tree(self):
		tree = self._tree(ADMIN)["tree"]
		self.assertEqual(tree["name"], ROOT)
		top = {r["user"] for r in tree["reports"]}
		self.assertTrue({EXEC_ET04, SUP_W02, self.LONE} <= top)

	def test_no_node_tree_is_null(self):
		self.assertEqual(self._tree(self.NOBODY), {"team": "none", "tree": None})


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
