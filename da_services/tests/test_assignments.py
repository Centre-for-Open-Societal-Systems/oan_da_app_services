"""Agent ↔ Kebele assignment: master areas, geofence, list, coverage, assign (DA-37, DA-38)."""

import json
import unittest

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from da_services.api.v1 import assignments as api
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import assignments as svc
from da_services.services import constants as C
from da_services.utils import geofence
from da_services.utils.permissions import get_scope_context

DA1 = "asg-da1@da.local"  # DA-000001, ET04 / ET04-W01
SUP = "asg-sup@da.local"  # Supervisor ET04-W01
EXEC = "asg-exec@da.local"  # Executive ET04
COMMS = "asg-comms@da.local"

AREAS = [
	("ET04", "Region", None, "Oromia"),
	("ET04-Z01", "Zone", "ET04", "West Shewa"),
	("ET04-W01", "Woreda", "ET04-Z01", "Bako Tibe"),
	("ET04-W01-K01", "Kebele", "ET04-W01", "Bako 01"),
	("ET04-W01-K03", "Kebele", "ET04-W01", "Dambi Gobu"),
	("ET04-W01-K07", "Kebele", "ET04-W01", "Gombo"),
	("ET03", "Region", None, "Amhara"),
	("ET03-Z02", "Zone", "ET03", "North Shewa"),
	("ET03-W02", "Woreda", "ET03-Z02", "Ankober"),
	("ET03-W02-K01", "Kebele", "ET03-W02", "Ankober 01"),
]
BOX = {
	"type": "Polygon",
	"coordinates": [[[37.50, 9.08], [37.62, 9.08], [37.62, 9.17], [37.50, 9.17], [37.50, 9.08]]],
}
DOCTYPES = ("DA Notification", "DA Kebele Assignment", "DA Administrative Area", "DA RBAC Assignment")


def _user(email, roles):
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
	doc = frappe.new_doc("DA RBAC Assignment")
	doc.user = user
	doc.role = role
	doc.effective_from = today()
	for k, v in kw.items():
		setattr(doc, k, v)
	doc.insert(ignore_permissions=True, ignore_links=True)
	return doc


def _ctx(user):
	return get_scope_context(user)


class TestGeofence(unittest.TestCase):
	def test_point_in_polygon_and_holes(self):
		self.assertTrue(geofence.point_in_boundary(9.12, 37.56, BOX))
		self.assertFalse(geofence.point_in_boundary(9.30, 37.56, BOX))
		donut = {
			"type": "Polygon",
			"coordinates": [
				BOX["coordinates"][0],
				[[37.55, 9.11], [37.57, 9.11], [37.57, 9.13], [37.55, 9.13], [37.55, 9.11]],
			],
		}
		self.assertFalse(geofence.point_in_boundary(9.12, 37.56, donut))  # inside the hole
		self.assertTrue(geofence.point_in_boundary(9.09, 37.51, donut))
		multi = {"type": "MultiPolygon", "coordinates": [BOX["coordinates"]]}
		self.assertTrue(geofence.point_in_boundary(9.12, 37.56, multi))
		self.assertTrue(geofence.point_in_boundary(9.12, 37.56, {"type": "Feature", "geometry": BOX}))
		with self.assertRaises(ValueError):
			geofence.point_in_boundary(9.12, 37.56, {"type": "Point"})

	def test_check_reports_not_checked_when_data_is_missing(self):
		self.assertEqual(geofence.check(None, None, BOX, "K")[0], geofence.NOT_CHECKED)
		self.assertEqual(
			geofence.check(9.12, 37.56, None, "K"), (geofence.NOT_CHECKED, "No boundary data for K")
		)
		self.assertEqual(geofence.check(9.12, 37.56, BOX, "K")[0], geofence.IN_BOUNDARY)
		self.assertEqual(geofence.check(9.30, 37.56, BOX, "K")[0], geofence.OUT_OF_BOUNDS)
		self.assertEqual(geofence.check(9.12, 37.56, {"type": "Point"}, "K")[0], geofence.NOT_CHECKED)


class TestAssignments(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for dt in DOCTYPES:
			frappe.db.delete(dt)
		MockDARegistryClient.reset()  # pushes mutate the fixture postings
		for code, level, parent, name_en in AREAS:
			doc = frappe.new_doc("DA Administrative Area")
			doc.code, doc.level, doc.parent_area, doc.name_en = code, level, parent, name_en
			if code == "ET04-W01-K03":
				doc.boundary_geojson = json.dumps(BOX)
			doc.insert(ignore_permissions=True, ignore_links=True)
		_user(DA1, [C.ROLE_DA])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_user(EXEC, [C.ROLE_EXECUTIVE])
		_user(COMMS, [C.ROLE_COMMS])
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope="ET04")
		_grant(COMMS, C.ROLE_COMMS)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		frappe.local.request = None

	def tearDown(self):
		frappe.set_user("Administrator")
		for dt in DOCTYPES:
			frappe.db.delete(dt)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		MockDARegistryClient.reset()

	# -- master ------------------------------------------------------------------------

	def test_area_tree_rules_and_ancestors(self):
		bad = frappe.new_doc("DA Administrative Area")
		bad.code, bad.level, bad.parent_area, bad.name_en = "ET04-W01-K99", "Kebele", "ET04", "wrong parent"
		with self.assertRaises(frappe.ValidationError):
			bad.insert(ignore_permissions=True, ignore_links=True)
		tree = frappe.get_doc("DA Administrative Area", "ET04-W01-K03").ancestors()
		self.assertEqual(
			tree, {"region": "ET04", "zone": "ET04-Z01", "woreda": "ET04-W01", "kebele": "ET04-W01-K03"}
		)
		frappe.set_user(DA1)
		out = api.master_kebeles(woreda="et04-w01")
		self.assertEqual([k["code"] for k in out["data"]], ["ET04-W01-K01", "ET04-W01-K03", "ET04-W01-K07"])
		self.assertEqual(api.master_areas(level="Woreda")["data"][0]["name_en"], "Ankober")

	# -- list / me / coverage ------------------------------------------------------------

	def test_supervisor_list_defaults_to_own_woreda(self):
		frappe.set_user(SUP)
		out = api.list_assignments()
		self.assertEqual(out["status"], "success", out)
		rows = {r["da_id"]: r for r in out["data"]}
		self.assertEqual(set(rows), {"DA-000001", "DA-000002"})  # ET04-W01 fixtures only
		self.assertEqual(rows["DA-000001"]["agent"], "Abebe Kebede")
		self.assertEqual(
			(rows["DA-000001"]["kebele"], rows["DA-000001"]["kebele_name"]), ("ET04-W01-K03", "Dambi Gobu")
		)
		self.assertEqual(
			rows["DA-000001"]["assignment_source"], "registry"
		)  # posting from the registry, no row of ours
		self.assertEqual(rows["DA-000002"]["status"], "On-leave")
		self.assertEqual(out["meta"]["summary"]["on_leave"], 1)
		self.assertEqual(api.list_assignments(q="marta")["meta"]["total"], 1)
		self.assertEqual(api.list_assignments(status="Active")["meta"]["total"], 1)
		# another Woreda is refused; a DA may not use the officer list
		self.assertEqual(api.list_assignments(woreda="ET03-W02")["status"], "error")
		frappe.set_user(DA1)
		self.assertEqual(api.list_assignments()["status"], "error")

	def test_executive_lists_region_and_comms_is_refused(self):
		frappe.set_user(EXEC)
		out = api.list_assignments()
		self.assertEqual({r["da_id"] for r in out["data"]}, {"DA-000001", "DA-000002"})
		frappe.set_user(COMMS)
		self.assertEqual(api.list_assignments()["status"], "error")

	def test_my_assignment(self):
		frappe.set_user(DA1)
		out = api.my_assignment()
		self.assertEqual(out["status"], "success", out)
		self.assertEqual(
			(out["data"]["da_id"], out["data"]["kebele"], out["data"]["history"]),
			("DA-000001", "ET04-W01-K03", []),
		)

	def test_coverage_states(self):
		frappe.set_user(SUP)
		out = api.coverage()
		self.assertEqual(out["status"], "success", out)
		cov = {k["kebele"]: k for k in out["data"]["kebeles"]}
		self.assertEqual(cov["ET04-W01-K03"]["coverage"], "Full")  # DA-000001 active
		self.assertEqual(cov["ET04-W01-K07"]["coverage"], "Agent on leave")  # DA-000002
		self.assertEqual(cov["ET04-W01-K01"]["coverage"], "Uncovered")
		self.assertEqual(cov["ET04-W01-K03"]["farmers"], 128)
		self.assertIsNone(cov["ET04-W01-K03"]["households"])
		self.assertEqual(
			out["data"]["summary"],
			{"kebeles": 3, "full": 1, "uncovered": 1, "flagged": 0, "agent_on_leave": 1},
		)

	# -- assign ----------------------------------------------------------------------------

	def test_assign_in_boundary_pushes_to_registry_and_notifies(self):
		frappe.set_user(SUP)
		out = api.assign(
			da_id="DA-000001",
			kebele="et04-w01-k03",
			effective_date=add_days(today(), 7),
			reason="Coverage gap",
			mode="Manual",
			da_location={"lat": 9.1236, "lng": 37.5671},
			client_op_id="kas-1",
		)
		self.assertEqual(out["status"], "success", out)
		a = out["data"]
		self.assertEqual(
			(a["state"], a["geofence_result"], a["mode"]), ("Effective", "In-boundary", "Manual")
		)
		self.assertEqual((a["region"], a["zone"], a["woreda"]), ("ET04", "ET04-Z01", "ET04-W01"))
		self.assertEqual(a["assigned_by"], SUP)
		self.assertEqual(a["registry_sync_state"], "Accepted")
		self.assertTrue(a["registry_reference"].startswith("MOCK-"))
		self.assertEqual(MockDARegistryClient.shared().get_da("DA-000001").kebele, "ET04-W01-K03")
		self.assertEqual(frappe.db.count("DA Notification", {"user": DA1, "event": "assignment_created"}), 1)
		# the list now shows our row as the source
		row = {r["da_id"]: r for r in api.list_assignments()["data"]}["DA-000001"]
		self.assertEqual((row["assignment_source"], row["assignment_id"]), ("da_services", a["name"]))
		# replay of the same device operation returns the same row
		again = api.assign(da_id="DA-000001", kebele="ET04-W01-K01", client_op_id="kas-1")["data"]
		self.assertEqual(again["name"], a["name"])

	def test_reassign_supersedes_and_out_of_bounds_is_flagged(self):
		frappe.set_user(SUP)
		first = api.assign(da_id="DA-000001", kebele="ET04-W01-K03", da_location={"lat": 9.12, "lng": 37.56})[
			"data"
		]
		second = api.assign(
			da_id="DA-000001", kebele="ET04-W01-K01", reason="Moved", da_location={"lat": 9.12, "lng": 37.56}
		)["data"]
		self.assertEqual((second["state"], second["geofence_result"]), ("Effective", "Not checked"))
		self.assertEqual(second["geofence_reason"], "No boundary data for Bako 01")
		self.assertEqual(second["supersedes"], first["name"])
		self.assertEqual(
			frappe.db.get_value("DA Kebele Assignment", first["name"], ["state", "superseded_by"]),
			("Superseded", second["name"]),
		)
		# Out of bounds: a Flagged decision is recorded and not pushed, and the posting in
		# force (second) stays Effective until a Supervisor overrides the flag.
		third = api.assign(da_id="DA-000001", kebele="ET04-W01-K03", da_location={"lat": 9.30, "lng": 37.56})[
			"data"
		]
		self.assertEqual(
			(third["state"], third["geofence_result"], third["registry_sync_state"]),
			("Flagged", "Out-of-bounds", "Not sent"),
		)
		self.assertIn("outside", third["geofence_reason"])
		self.assertEqual(frappe.db.get_value("DA Kebele Assignment", second["name"], "state"), "Effective")
		self.assertIsNone(third["supersedes"])
		self.assertEqual(frappe.db.count("DA Notification", {"user": DA1, "event": "assignment_flagged"}), 1)
		row = {r["da_id"]: r for r in api.list_assignments()["data"]}["DA-000001"]
		self.assertEqual(
			(row["kebele"], row["assignment_status"], row["geofence"]),
			("ET04-W01-K01", "Flagged", "Out-of-bounds"),
		)
		self.assertEqual(row["flagged"]["kebele"], "ET04-W01-K03")
		self.assertEqual(api.list_assignments()["meta"]["summary"]["flagged"], 1)
		cov = {k["kebele"]: k for k in api.coverage()["data"]["kebeles"]}
		self.assertEqual(
			(cov["ET04-W01-K01"]["coverage"], cov["ET04-W01-K03"]["coverage"]), ("Full", "Flagged")
		)
		# A second flagged attempt replaces the earlier flag; a later in-boundary decision replaces both.
		fourth = api.assign(
			da_id="DA-000001", kebele="ET04-W01-K03", da_location={"lat": 9.40, "lng": 37.56}
		)["data"]
		self.assertEqual(frappe.db.get_value("DA Kebele Assignment", third["name"], "state"), "Superseded")
		fifth = api.assign(da_id="DA-000001", kebele="ET04-W01-K03", da_location={"lat": 9.12, "lng": 37.56})[
			"data"
		]
		self.assertEqual(fifth["state"], "Effective")
		states = frappe.get_all(
			"DA Kebele Assignment", filters={"name": ("in", [second["name"], fourth["name"]])}, pluck="state"
		)
		self.assertEqual(set(states), {"Superseded"})
		self.assertIsNone({r["da_id"]: r for r in api.list_assignments()["data"]}["DA-000001"]["flagged"])

	def test_coordinates_are_validated(self):
		frappe.set_user(SUP)
		frappe.db.commit()  # nosemgrep: error calls below roll back uncommitted rows
		for bad in (
			{"lat": 91, "lng": 37.5},
			{"lat": 9.1, "lng": -181},
			{"lat": "x", "lng": 37.5},
			{"lat": 9.1},
		):
			out = api.assign(da_id="DA-000001", kebele="ET04-W01-K03", da_location=bad)
			self.assertEqual(out["status"], "error", bad)
			self.assertEqual(frappe.response.get("http_status_code"), 400, bad)
		self.assertEqual(frappe.db.count("DA Kebele Assignment"), 0)
		with self.assertRaises(ValueError):
			geofence.validate_coordinates(float("nan"), 0)

	def test_client_op_id_is_unique_in_the_database(self):
		frappe.set_user("Administrator")
		a = svc.assign(
			da_id="DA-000001", kebele="ET04-W01-K03", client_op_id="kas-db-1", ctx=_ctx("Administrator")
		)
		doc = frappe.get_doc(
			{
				"doctype": "DA Kebele Assignment",
				"da_id": "DA-000001",
				"kebele": "ET04-W01-K01",
				"state": "Effective",
				"mode": "Automatic",
				"effective_date": today(),
				"client_op_id": "kas-db-1",
			}
		)
		with self.assertRaises(frappe.DuplicateEntryError):
			doc.insert(ignore_permissions=True, ignore_links=True)
		self.assertEqual(
			frappe.db.get_value("DA Kebele Assignment", {"client_op_id": "kas-db-1"}, "name"), a["name"]
		)
		self.assertEqual(frappe.db.count("DA Kebele Assignment", {"client_op_id": ("is", "set")}), 1)

	def test_registry_down_is_503_and_notification_failure_is_not_fatal(self):
		from unittest.mock import patch

		from da_services.integrations.da_registry.client import DARegistryUnavailable

		frappe.set_user(SUP)
		frappe.db.commit()  # nosemgrep
		with patch.object(MockDARegistryClient, "list_das", side_effect=DARegistryUnavailable("down")):
			out = api.list_assignments()
			self.assertEqual((out["status"], out["code"]), ("error", "SERVICE_UNAVAILABLE"))
			self.assertEqual(frappe.response.get("http_status_code"), 503)
			self.assertEqual(frappe.response.get("http_status_code"), 503)
		with patch("da_services.services.assignments.notify_da", side_effect=RuntimeError("smtp down")):
			out = api.assign(da_id="DA-000001", kebele="ET04-W01-K03", client_op_id="kas-ntf-1")
		self.assertEqual(out["status"], "success", out)
		self.assertEqual(out["data"]["state"], "Effective")
		self.assertEqual(frappe.db.count("DA Notification", {"user": DA1}), 0)

	def test_assign_scope_and_validation(self):
		frappe.set_user(SUP)
		frappe.db.commit()  # nosemgrep: error calls below roll back uncommitted rows
		self.assertEqual(
			api.assign(da_id="DA-000003", kebele="ET03-W02-K01")["status"], "error"
		)  # DA outside Woreda
		self.assertEqual(frappe.response.get("http_status_code"), 403)
		self.assertEqual(
			api.assign(da_id="DA-000001", kebele="ET03-W02-K01")["status"], "error"
		)  # Kebele outside Woreda
		self.assertEqual(frappe.response.get("http_status_code"), 403)
		self.assertEqual(api.assign(da_id="DA-000001", kebele="ET04-W01")["status"], "error")  # not a Kebele
		self.assertEqual(
			api.assign(da_id="DA-000001", kebele="ET04-W01-K03", mode="Weird")["status"], "error"
		)
		frappe.set_user("Administrator")
		self.assertEqual(
			api.assign(da_id="DA-000003", kebele="ET03-W02-K01")["status"], "error"
		)  # separated DA
		frappe.set_user(DA1)
		self.assertEqual(api.assign(da_id="DA-000001", kebele="ET04-W01-K03")["status"], "error")
		frappe.set_user(EXEC)
		self.assertEqual(api.assign(da_id="DA-000001", kebele="ET04-W01-K03")["status"], "error")

	def test_assignment_rows_cannot_be_deleted(self):
		frappe.set_user("Administrator")
		a = svc.assign(da_id="DA-000001", kebele="ET04-W01-K03", ctx=_ctx("Administrator"))
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("DA Kebele Assignment", a["name"], ignore_permissions=True)
