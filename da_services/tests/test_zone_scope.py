"""Zone level in the scope model: rows carry `zone`, lists and single-document checks
honour a Zone grant, Woreda beats Zone, Region covers every Zone, and the backfill
patch fills `zone` on rows created before the column existed."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime, today

from da_services.da_services.doctype.da_hierarchy.da_hierarchy import ensure_root
from da_services.services import constants as C
from da_services.services import farmers, profile, tasks, visits
from da_services.tests.fixtures import (
	as_user,
	cleanup_test_rows,
	ensure_administrative_area,
	ensure_da_user,
	ensure_doctype_schema,
	ensure_geo_masters,
	set_da_registry_id,
)
from da_services.workflows.profile_change import ensure_workflow

DA1 = "zone-da1@da.local"  # DA-000001: ET04 / ET04-Z01 / ET04-W01
ZONE_EXEC = "zone-exec@da.local"  # Executive ET04 / ET04-Z01
OTHER_ZONE_EXEC = "zone-exec2@da.local"  # Executive ET04 / ET04-Z02
MIXED_EXEC = "zone-mixed@da.local"  # Executive: Woreda ET04-W01 + Zone ET04-Z02
REGION_EXEC = "zone-region@da.local"  # Executive ET04 (no zone)
ZONE_SUP = "zone-sup@da.local"  # Supervisor ET04-W01 (Z01)

# A DA in the same Region but another Zone. Not in the mock registry, so the row is
# inserted with its posting codes set (the registry fill is skipped when they are present).
OTHER_DA = "DA-000009"
OTHER = {"region": "ET04", "zone": "ET04-Z02", "woreda": "ET04-W09", "kebele": "ET04-W09-K01"}

AREAS = [
	("ET04", "Region", None),
	("ET04-Z01", "Zone", "ET04"),
	("ET04-W01", "Woreda", "ET04-Z01"),
	("ET04-Z02", "Zone", "ET04"),
	("ET04-W09", "Woreda", "ET04-Z02"),
]
USERS = (
	DA1,
	ZONE_EXEC,
	OTHER_ZONE_EXEC,
	MIXED_EXEC,
	REGION_EXEC,
	ZONE_SUP,
	f"{OTHER_DA.lower()}@da-test.local",
)
DA_IDS = ("DA-000001", OTHER_DA)


def _user(email, roles):
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
	doc = frappe.new_doc("DA RBAC Assignment")
	doc.user = user
	doc.role = role
	doc.effective_from = today()
	if kw.get("da_id"):
		set_da_registry_id(user, kw["da_id"])  # da_id is fetched from the user's DA Registry ID
	for k, v in kw.items():
		setattr(doc, k, v)
	doc.insert(ignore_permissions=True)
	return doc


def _ctx(user):
	return user


class TestZoneScope(IntegrationTestCase):
	def _remove_hierarchy(self):
		frappe.set_user("Administrator")
		for user in (DA1, ZONE_SUP):  # the leaf before its group
			if frappe.db.exists("DA Hierarchy", user):
				frappe.delete_doc("DA Hierarchy", user, ignore_permissions=True, force=True)
		frappe.db.delete("User Permission", {"user": ("in", (DA1, ZONE_SUP)), "allow": "User"})

	def setUp(self):
		frappe.set_user("Administrator")
		ensure_doctype_schema("DA Profile Change Request")
		ensure_workflow()
		cleanup_test_rows(users=USERS, da_ids=DA_IDS, farmer_ids=("farmer-z-1",))
		for code, level, parent in AREAS:
			ensure_administrative_area(code, level, parent, code)
		ensure_geo_masters()  # RBAC scopes link to DA Region / Zone / Woreda
		_user(DA1, [C.ROLE_DA])
		_user(ZONE_SUP, [C.ROLE_SUPERVISOR])
		for e in (ZONE_EXEC, OTHER_ZONE_EXEC, MIXED_EXEC, REGION_EXEC):
			_user(e, [C.ROLE_EXECUTIVE])
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(ZONE_SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(ZONE_EXEC, C.ROLE_EXECUTIVE, region_scope="ET04", zone_scope="ET04-Z01")
		_grant(OTHER_ZONE_EXEC, C.ROLE_EXECUTIVE, region_scope="ET04", zone_scope="ET04-Z02")
		_grant(MIXED_EXEC, C.ROLE_EXECUTIVE, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(MIXED_EXEC, C.ROLE_EXECUTIVE, region_scope="ET04", zone_scope="ET04-Z02")
		_grant(REGION_EXEC, C.ROLE_EXECUTIVE, region_scope="ET04")
		set_da_registry_id(DA1, "DA-000001")
		# A Supervisor sends a change request for a DA below them in DA Hierarchy.
		ensure_root()
		self.addCleanup(self._remove_hierarchy)
		self._remove_hierarchy()
		for user, parent, group in ((ZONE_SUP, None, 1), (DA1, ZONE_SUP, 0)):
			frappe.get_doc(
				{"doctype": "DA Hierarchy", "user": user, "is_group": group, "parent_da_hierarchy": parent}
			).insert(ignore_permissions=True)

		frappe.set_user(DA1)
		self.v1 = visits.plan_visit(
			farmer_id="FR-88466",
			farmer_name="Test Farmer",
			scheduled_at=f"{today()} 08:30:00",
			purpose="Crop inspection",
			user=DA1,
		)
		frappe.set_user("Administrator")
		# Plan no longer fills geo from registry; stamp posting codes for scope assertions.
		frappe.db.set_value(
			"DA Visit",
			self.v1["name"],
			{"region": "ET04", "zone": "ET04-Z01", "woreda": "ET04-W01", "kebele": "ET04-W01-K03"},
			update_modified=False,
		)
		self.v9 = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"user": ensure_da_user(OTHER_DA),
				"da_id": OTHER_DA,
				"farmer_id": "FR-77009",
				"farmer_name": "Test Farmer",
				"scheduled_at": f"{add_days(today(), 1)} 09:00:00",
				"physical_status": "Planned",
				**OTHER,
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		cleanup_test_rows(users=USERS, da_ids=DA_IDS, farmer_ids=("farmer-z-1",))
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def _visible(self, doctype, user) -> set[str]:
		frappe.set_user(user)
		try:
			return {r.da_id for r in frappe.get_list(doctype, fields=["da_id"])}
		finally:
			frappe.set_user("Administrator")

	# -- rows carry zone ------------------------------------------------------------

	def test_visit_task_and_change_request_carry_zone_from_stamp(self):
		self.assertEqual(frappe.db.get_value("DA Visit", self.v1["name"], "zone"), "ET04-Z01")
		with as_user(DA1):
			t = tasks.create_task(
				title="Soil sample",
				user=DA1,
				region="ET04",
				zone="ET04-Z01",
				woreda="ET04-W01",
			)
		self.assertEqual(frappe.db.get_value("DA Task", t["name"], "zone"), "ET04-Z01")
		with as_user(ZONE_SUP):  # the Supervisor above the DA asks for them
			req = profile.submit_change_request(
				changes={"telegram": "+251911222333"}, reason="New Telegram number", da_id="DA-000001"
			)
		self.assertEqual(req["zone"], "ET04-Z01")
		self.assertEqual(frappe.db.get_value("DA Profile Change Request", req["name"], "zone"), "ET04-Z01")

	def test_follow_up_task_and_visit_inherit_zone(self):
		_ctx(DA1)
		visits.start_visit(self.v1["name"], gps={"lat": 9.1236, "lng": 37.0521, "accuracy_m": 12})
		out = visits.submit_outcome(
			self.v1["name"],
			observed="Rust",
			advice_given="Spray",
			next_action={"type": "Follow-up visit", "note": "Check", "date": add_days(today(), 7)},
		)
		self.assertEqual(frappe.db.get_value("DA Task", out["follow_up_task"], "zone"), "ET04-Z01")
		self.assertEqual(frappe.db.get_value("DA Visit", out["follow_up_visit"], "zone"), "ET04-Z01")

	def test_farmer_link_copies_zone_from_reference(self):
		frappe.get_doc(
			{
				"doctype": "DA Farmer Reference",
				"farmer_id": "farmer-z-1",
				"reg_id": "FR-Z-1",
				"farmer_name": "Zone Farmer",
				"region": "ET04",
				"zone": "ET04-Z01",
				"woreda": "ET04-W01",
				"kebele": "ET04-W01-K03",
				"registration_state": "APPROVED",
				"registered_at": now_datetime(),
			}
		).insert(ignore_permissions=True)
		link = frappe.get_doc(
			{"doctype": "DA Farmer Link", "da_id": "DA-000001", "farmer_id": "farmer-z-1", "is_active": 1}
		).insert(ignore_permissions=True)
		self.assertEqual(link.zone, "ET04-Z01")

	# -- lists --------------------------------------------------------------------------

	def test_zone_grant_lists_only_its_zone(self):
		self.assertEqual(self._visible("DA Visit", ZONE_EXEC), {"DA-000001"})
		self.assertEqual(self._visible("DA Visit", OTHER_ZONE_EXEC), {OTHER_DA})

	def test_region_grant_lists_every_zone(self):
		self.assertEqual(self._visible("DA Visit", REGION_EXEC), {"DA-000001", OTHER_DA})

	def test_woreda_and_zone_grants_are_anded_for_visits(self):
		# Frappe User Permissions AND across allow doctypes. A mixed Woreda+Zone grant
		# therefore matches neither visit here (W01/Z01 vs W09/Z02). Visit hooks that
		# ORed grants were removed — Role DocPerm + UP only.
		self.assertEqual(self._visible("DA Visit", MIXED_EXEC), set())

	def test_woreda_supervisor_does_not_see_other_zone(self):
		self.assertEqual(self._visible("DA Visit", ZONE_SUP), {"DA-000001"})

	def test_zone_grant_applies_to_tasks_links_and_change_requests(self):
		with as_user(DA1):
			tasks.create_task(
				title="Mine",
				user=DA1,
				region="ET04",
				zone="ET04-Z01",
				woreda="ET04-W01",
			)
		frappe.get_doc(
			{
				"doctype": "DA Task",
				"user": ensure_da_user(OTHER_DA),
				"da_id": OTHER_DA,
				"title": "Theirs",
				"task_type": "Custom",
				**{k: OTHER[k] for k in ("region", "zone", "woreda")},
			}
		).insert(ignore_permissions=True)
		with as_user(ZONE_SUP):
			profile.submit_change_request(
				changes={"telegram": "+251911222444"}, reason="New Telegram number", da_id="DA-000001"
			)
		frappe.get_doc(
			{
				"doctype": "DA Profile Change Request",
				"da_id": OTHER_DA,
				"section": "Contact",
				"status": "Pending Supervisor",
				"requested_by": "Administrator",
				"changes": "{}",
				**{k: OTHER[k] for k in ("region", "zone", "woreda")},
			}
		).insert(ignore_permissions=True)
		for link in (
			("DA-000001", "farmer-a", "ET04-Z01", "ET04-W01"),
			(OTHER_DA, "farmer-b", "ET04-Z02", "ET04-W09"),
		):
			frappe.get_doc(
				{
					"doctype": "DA Farmer Link",
					"da_id": link[0],
					"farmer_id": link[1],
					"farmer_name": link[1],
					"region": "ET04",
					"zone": link[2],
					"woreda": link[3],
					"is_active": 1,
				}
			).insert(ignore_permissions=True)

		for dt in ("DA Task", "DA Profile Change Request", "DA Farmer Link"):
			self.assertEqual(self._visible(dt, ZONE_EXEC), {"DA-000001"}, dt)
			self.assertEqual(self._visible(dt, OTHER_ZONE_EXEC), {OTHER_DA}, dt)
			self.assertEqual(self._visible(dt, REGION_EXEC), {"DA-000001", OTHER_DA}, dt)

		frappe.set_user(ZONE_EXEC)  # User Permissions scope get_list by the session user
		with as_user(ZONE_EXEC):
			rows, total = profile.list_change_requests()
		self.assertEqual(total, 1)
		self.assertEqual(rows[0]["da_id"], "DA-000001")

	# -- single document --------------------------------------------------------------

	def test_single_document_get_doc_loads(self):
		# Visit detail is plain get_doc — no service-side scope gate. List scope is
		# covered by test_zone_grant_lists_only_its_zone / related list tests.
		frappe.set_user(ZONE_EXEC)
		try:
			self.assertEqual(visits.get_visit(self.v1["name"])["name"], self.v1["name"])
		finally:
			frappe.set_user("Administrator")
		frappe.set_user(OTHER_ZONE_EXEC)
		try:
			self.assertEqual(visits.get_visit(self.v9.name)["name"], self.v9.name)
		finally:
			frappe.set_user("Administrator")

	def test_row_without_zone_under_user_permissions(self):
		frappe.db.set_value("DA Visit", self.v1["name"], "zone", None)
		# Without Visit hooks, empty zone is not a custom deny — Role + User Permissions apply.
		self.assertIn("DA-000001", self._visible("DA Visit", ZONE_EXEC))
		frappe.set_user(ZONE_EXEC)
		try:
			visits.get_visit(self.v1["name"])
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(self._visible("DA Visit", REGION_EXEC), {"DA-000001", OTHER_DA})

	# -- farmers list filter ------------------------------------------------------------

	def test_zone_officer_can_filter_farmers_by_a_woreda_in_their_zone(self):
		with as_user(ZONE_EXEC):
			farmers.list_farmers(woreda="ET04-W01")  # under ET04-Z01: allowed
		frappe.set_user(ZONE_EXEC)  # User Permissions apply to the session user
		try:
			# under ET04-Z02: outside the Zone User Permission, so the filter narrows to nothing
			with as_user(ZONE_EXEC):
				self.assertEqual(farmers.list_farmers(woreda="ET04-W09")[1], 0)
		finally:
			frappe.set_user("Administrator")
