"""Zone level in the scope model: rows carry `zone`, lists and single-document checks
honour a Zone grant, Woreda beats Zone, Region covers every Zone, and the backfill
patch fills `zone` on rows created before the column existed."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime, today

from da_services.patches import backfill_zone
from da_services.services import constants as C
from da_services.services import farmers, profile, tasks, visits
from da_services.utils.permissions import get_scope_context

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
DOCTYPES = (
	"DA Visit",
	"DA Task",
	"DA Farmer Link",
	"DA Farmer Reference",
	"DA Profile Change Request",
	"DA RBAC Assignment",
	"DA Administrative Area",
)


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
	for k, v in kw.items():
		setattr(doc, k, v)
	doc.insert(ignore_permissions=True)
	return doc


def _ctx(user):
	return get_scope_context(user)


class TestZoneScope(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for dt in DOCTYPES:
			frappe.db.delete(dt)
		for code, level, parent in AREAS:
			doc = frappe.new_doc("DA Administrative Area")
			doc.code, doc.level, doc.parent_area, doc.name_en = code, level, parent, code
			doc.insert(ignore_permissions=True)
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

		self.v1 = visits.plan_visit(
			farmer_id="FR-88466", scheduled_at=f"{today()} 08:30:00", purpose="Crop inspection", ctx=_ctx(DA1)
		)
		self.v9 = frappe.get_doc(
			{
				"doctype": "DA Visit",
				"da_id": OTHER_DA,
				"farmer_id": "FR-77009",
				"scheduled_at": f"{add_days(today(), 1)} 09:00:00",
				"physical_status": "Planned",
				**OTHER,
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _visible(self, doctype, user) -> set[str]:
		frappe.set_user(user)
		try:
			return {r.da_id for r in frappe.get_list(doctype, fields=["da_id"])}
		finally:
			frappe.set_user("Administrator")

	# -- rows carry zone ------------------------------------------------------------

	def test_visit_task_and_change_request_carry_zone_from_registry(self):
		self.assertEqual(frappe.db.get_value("DA Visit", self.v1["name"], "zone"), "ET04-Z01")
		t = tasks.create_task(title="Soil sample", ctx=_ctx(DA1))
		self.assertEqual(frappe.db.get_value("DA Task", t["name"], "zone"), "ET04-Z01")
		req = profile.submit_change_request(
			changes={"language": "am"}, reason="Prefer Amharic", ctx=_ctx(DA1)
		)
		self.assertEqual(req["zone"], "ET04-Z01")
		self.assertEqual(frappe.db.get_value("DA Profile Change Request", req["name"], "zone"), "ET04-Z01")

	def test_follow_up_task_and_visit_inherit_zone(self):
		visits.start_visit(
			self.v1["name"], gps={"lat": 9.1236, "lng": 37.0521, "accuracy_m": 12}, ctx=_ctx(DA1)
		)
		out = visits.submit_outcome(
			self.v1["name"],
			observed="Rust",
			advice_given="Spray",
			next_action={"type": "Follow-up visit", "note": "Check", "date": add_days(today(), 7)},
			ctx=_ctx(DA1),
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

	def test_woreda_and_zone_grants_are_combined(self):
		# Woreda W01 (in Z01) + Zone Z02: both rows, nothing from the rest of the Region.
		self.assertEqual(self._visible("DA Visit", MIXED_EXEC), {"DA-000001", OTHER_DA})

	def test_woreda_supervisor_does_not_see_other_zone(self):
		self.assertEqual(self._visible("DA Visit", ZONE_SUP), {"DA-000001"})

	def test_zone_grant_applies_to_tasks_links_and_change_requests(self):
		tasks.create_task(title="Mine", ctx=_ctx(DA1))
		frappe.get_doc(
			{
				"doctype": "DA Task",
				"da_id": OTHER_DA,
				"title": "Theirs",
				"task_type": "Custom",
				**{k: OTHER[k] for k in ("region", "zone", "woreda")},
			}
		).insert(ignore_permissions=True)
		profile.submit_change_request(changes={"language": "om"}, reason="Prefer Oromo", ctx=_ctx(DA1))
		frappe.get_doc(
			{
				"doctype": "DA Profile Change Request",
				"da_id": OTHER_DA,
				"section": "Contact",
				"status": "Pending",
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

		frappe.set_user(ZONE_EXEC)  # the list hook scopes by the session user
		rows, total = profile.list_change_requests(_ctx(ZONE_EXEC))
		self.assertEqual(total, 1)
		self.assertEqual(rows[0]["da_id"], "DA-000001")

	# -- single document --------------------------------------------------------------

	def test_single_document_scope_honours_zone(self):
		visits.get_visit(self.v1["name"], ctx=_ctx(ZONE_EXEC))
		with self.assertRaises(frappe.PermissionError):
			visits.get_visit(self.v9.name, ctx=_ctx(ZONE_EXEC))
		visits.get_visit(self.v9.name, ctx=_ctx(OTHER_ZONE_EXEC))
		visits.get_visit(self.v9.name, ctx=_ctx(REGION_EXEC))
		with self.assertRaises(frappe.PermissionError):
			visits.get_visit(self.v9.name, ctx=_ctx(ZONE_SUP))

	def test_row_without_zone_is_denied_to_zone_grant_not_widened(self):
		frappe.db.set_value("DA Visit", self.v1["name"], "zone", None)
		self.assertEqual(self._visible("DA Visit", ZONE_EXEC), set())
		with self.assertRaises(frappe.PermissionError):
			visits.get_visit(self.v1["name"], ctx=_ctx(ZONE_EXEC))
		# The Region-only grant still covers it.
		self.assertEqual(self._visible("DA Visit", REGION_EXEC), {"DA-000001", OTHER_DA})

	# -- farmers list filter ------------------------------------------------------------

	def test_zone_officer_can_filter_farmers_by_a_woreda_in_their_zone(self):
		farmers.list_farmers(_ctx(ZONE_EXEC), woreda="ET04-W01")  # under ET04-Z01: allowed
		with self.assertRaises(frappe.PermissionError):
			farmers.list_farmers(_ctx(ZONE_EXEC), woreda="ET04-W09")  # under ET04-Z02

	# -- backfill patch -----------------------------------------------------------------

	def test_backfill_fills_zone_from_master_data_then_registry(self):
		# v1: Woreda known in master data. v9: Woreda not in master data, zone comes from the registry (DA-000001 posting).
		frappe.db.sql("update `tabDA Visit` set zone = NULL")
		frappe.db.set_value("DA Visit", self.v9.name, {"da_id": "DA-000001", "woreda": "ET04-W77"})
		t = tasks.create_task(title="Backfill me", ctx=_ctx(DA1))
		frappe.db.set_value("DA Task", t["name"], "zone", None)

		backfill_zone.execute()

		self.assertEqual(frappe.db.get_value("DA Visit", self.v1["name"], "zone"), "ET04-Z01")
		self.assertEqual(frappe.db.get_value("DA Visit", self.v9.name, "zone"), "ET04-Z01")
		self.assertEqual(frappe.db.get_value("DA Task", t["name"], "zone"), "ET04-Z01")
