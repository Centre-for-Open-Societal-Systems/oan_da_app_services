"""Agent Γåö Kebele assignment: geography masters, geofence, list, coverage, assign (DA-37, DA-38).

Geography comes from the synced DA Region / Zone / Woreda / Kebele masters. The mock DA
Registry rewrites its fixture postings from those masters (``aligned_mock_geo``):
DA-000001 ΓåÆ ``kebele``, DA-000002 (On-leave) ΓåÆ ``kebele_alt``, DA-000003/4 ΓåÆ the other
chain. setUp fails closed when the masters are empty.
"""

import unittest
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from da_services.api.v1 import assignments as api
from da_services.integrations.da_registry.client import DARegistryUnavailable
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import assignments as svc
from da_services.services import constants as C
from da_services.services import geo
from da_services.tests.fixtures import aligned_mock_geo, bind_da_user, cleanup_test_rows, require_geo_masters
from da_services.utils import geofence
from da_services.utils.permissions import get_scope_context

DA1 = "asg-da1@da.local"  # DA-000001, primary Region / Woreda
SUP = "asg-sup@da.local"  # Supervisor, primary Woreda
EXEC = "asg-exec@da.local"  # Executive, primary Region
COMMS = "asg-comms@da.local"

# Rows this module creates; setUp / tearDown delete only these.
USERS = (DA1, SUP, EXEC, COMMS)
DA_IDS = ("DA-000001",)

BOX = {
	"type": "Polygon",
	"coordinates": [[[37.50, 9.08], [37.62, 9.08], [37.62, 9.17], [37.50, 9.17], [37.50, 9.08]]],
}


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


def _uncovered_kebele(g: dict) -> str:
	"""An active Kebele of the primary Woreda where no fixture DA is posted."""
	for row in geo.kebeles_for_woreda(g["woreda"]):
		if row["code"] not in (g["kebele"], g["kebele_alt"]):
			return row["code"]
	frappe.throw(f"Woreda {g['woreda']} needs a third active Kebele in the geography masters.")


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
		cleanup_test_rows(users=USERS, da_ids=DA_IDS)
		require_geo_masters()
		MockDARegistryClient.reset()  # pushes mutate the fixture postings; rebuilds with aligned geo
		g = aligned_mock_geo()
		self.geo = g
		self.region, self.zone, self.woreda = g["region"], g["zone"], g["woreda"]
		self.kebele = g["kebele"]  # DA-000001's registry posting
		self.kebele_on_leave = g["kebele_alt"]  # DA-000002 (On-leave)
		self.kebele_alt = _uncovered_kebele(g)  # nobody posted here
		self.other_woreda, self.other_kebele = g["other_woreda"], g["other_kebele"]
		_user(DA1, [C.ROLE_DA])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_user(EXEC, [C.ROLE_EXECUTIVE])
		_user(COMMS, [C.ROLE_COMMS])
		bind_da_user(DA1, "DA-000001")  # assign / notify / me resolve the DA through User.da_registry_id
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope=self.region, woreda_scope=self.woreda)
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope=self.region, woreda_scope=self.woreda)
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope=self.region)
		_grant(COMMS, C.ROLE_COMMS)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		frappe.local.request = None

	def tearDown(self):
		frappe.set_user("Administrator")
		cleanup_test_rows(users=USERS, da_ids=DA_IDS)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		MockDARegistryClient.reset()

	# -- geography masters -----------------------------------------------------------------

	def test_master_tree_ancestors_and_cascading_lists(self):
		region, zone, woreda, kebele = self.region, self.zone, self.woreda, self.kebele
		self.assertEqual(
			geo.ancestors_of_kebele(kebele),
			{"region": region, "zone": zone, "woreda": woreda, "kebele": kebele},
		)
		self.assertEqual(geo.ancestors_of_woreda(woreda)["zone"], zone)
		self.assertEqual(geo.ancestors_of_zone(zone)["region"], region)
		# Unknown codes resolve to nothing instead of inventing a parent.
		self.assertEqual(geo.ancestors_of_kebele(f"{woreda}-K99")["woreda"], None)

		frappe.set_user(DA1)
		out = api.master_kebeles(woreda=woreda.lower())  # case-insensitive Woreda code
		self.assertEqual(out["status"], "success", out)
		codes = [k["code"] for k in out["data"]]
		self.assertEqual(codes, [k["code"] for k in geo.kebeles_for_woreda(woreda)])
		self.assertTrue({kebele, self.kebele_alt, self.kebele_on_leave} <= set(codes))
		self.assertTrue(all(k.get("parent_area") == woreda or k.get("woreda") == woreda for k in out["data"]))
		self.assertEqual(api.master_kebeles()["status"], "error")  # woreda is required

		woredas = api.master_areas(level="Woreda", parent=zone)["data"]
		self.assertIn(woreda, [w["code"] for w in woredas])
		self.assertTrue(all((w["level"], w["parent_area"]) == ("Woreda", zone) for w in woredas))
		self.assertEqual(
			geo.display_name("DA Woreda", woreda),
			next(w["name_en"] for w in woredas if w["code"] == woreda),
		)
		self.assertIn(region, [r["code"] for r in api.master_areas(level="Region")["data"]])
		self.assertEqual(api.master_areas(level="Region", parent=region)["status"], "error")
		self.assertEqual(api.master_areas(level="Village")["status"], "error")

	# -- list / me / coverage ------------------------------------------------------------

	def test_supervisor_list_defaults_to_own_woreda(self):
		frappe.set_user(SUP)
		out = api.list_assignments()
		self.assertEqual(out["status"], "success", out)
		rows = {r["da_id"]: r for r in out["data"]}
		self.assertEqual(set(rows), {"DA-000001", "DA-000002"})  # primary-Woreda fixtures only
		self.assertEqual(rows["DA-000001"]["agent"], "Abebe Kebede")
		self.assertEqual(
			(rows["DA-000001"]["kebele"], rows["DA-000001"]["kebele_name"]),
			(self.kebele, geo.display_name("DA Kebele", self.kebele)),
		)
		self.assertEqual(
			rows["DA-000001"]["assignment_source"], "registry"
		)  # posting from the registry, no row of ours
		self.assertEqual(rows["DA-000002"]["status"], "On-leave")
		self.assertEqual(out["meta"]["summary"]["on_leave"], 1)
		self.assertEqual(api.list_assignments(q="marta")["meta"]["total"], 1)
		self.assertEqual(api.list_assignments(status="Active")["meta"]["total"], 1)
		# another Woreda is refused; a DA may not use the officer list
		self.assertEqual(api.list_assignments(woreda=self.other_woreda)["status"], "error")
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
			("DA-000001", self.kebele, []),
		)

	def test_coverage_states(self):
		frappe.set_user(SUP)
		out = api.coverage()
		self.assertEqual(out["status"], "success", out)
		cov = {k["kebele"]: k for k in out["data"]["kebeles"]}
		self.assertEqual(cov[self.kebele]["coverage"], "Full")  # DA-000001 active
		self.assertEqual(cov[self.kebele_on_leave]["coverage"], "Agent on leave")  # DA-000002
		self.assertEqual(cov[self.kebele_alt]["coverage"], "Uncovered")
		self.assertEqual(cov[self.kebele]["farmers"], 128)
		self.assertIsNone(cov[self.kebele]["households"])
		self.assertEqual(cov[self.kebele]["kebele_name"], geo.display_name("DA Kebele", self.kebele))
		# One tile per active Kebele of the Woreda in the masters; only two carry an agent.
		total = len(geo.kebeles_for_woreda(self.woreda))
		self.assertEqual(
			out["data"]["summary"],
			{"kebeles": total, "full": 1, "uncovered": total - 2, "flagged": 0, "agent_on_leave": 1},
		)
		self.assertEqual(out["data"]["kebeles_not_in_master"], [])

	# -- assign ----------------------------------------------------------------------------

	def test_assign_pushes_to_registry_and_notifies(self):
		frappe.set_user(SUP)
		out = api.assign(
			da_id="DA-000001",
			kebele=self.kebele.lower(),  # case-insensitive Kebele code
			effective_date=add_days(today(), 7),
			reason="Coverage gap",
			mode="Manual",
			da_location={"lat": 9.1236, "lng": 37.5671},
			client_op_id="kas-1",
		)
		self.assertEqual(out["status"], "success", out)
		a = out["data"]
		# Polygons are not on the geography masters yet: the geofence is recorded, not decided.
		self.assertEqual(
			(a["state"], a["geofence_result"], a["mode"]), ("Effective", "Not checked", "Manual")
		)
		self.assertEqual((a["region"], a["zone"], a["woreda"]), (self.region, self.zone, self.woreda))
		self.assertEqual(a["assigned_by"], SUP)
		self.assertEqual(a["registry_sync_state"], "Accepted")
		self.assertTrue(a["registry_reference"].startswith("MOCK-"))
		self.assertEqual(MockDARegistryClient.shared().get_da("DA-000001").kebele, self.kebele)
		self.assertEqual(frappe.db.count("DA Notification", {"user": DA1, "event": "assignment_created"}), 1)
		# the list now shows our row as the source
		row = {r["da_id"]: r for r in api.list_assignments()["data"]}["DA-000001"]
		self.assertEqual((row["assignment_source"], row["assignment_id"]), ("da_services", a["name"]))
		# replay of the same device operation returns the same row
		again = api.assign(da_id="DA-000001", kebele=self.kebele_alt, client_op_id="kas-1")["data"]
		self.assertEqual(again["name"], a["name"])

	def test_reassign_supersedes_the_posting_in_force(self):
		frappe.set_user(SUP)
		kebele, kebele_alt = self.kebele, self.kebele_alt
		first = api.assign(da_id="DA-000001", kebele=kebele, da_location={"lat": 9.12, "lng": 37.56})["data"]
		second = api.assign(
			da_id="DA-000001", kebele=kebele_alt, reason="Moved", da_location={"lat": 9.12, "lng": 37.56}
		)["data"]
		self.assertEqual((second["state"], second["geofence_result"]), ("Effective", "Not checked"))
		self.assertIn("Boundary not available", second["geofence_reason"])
		self.assertEqual(second["supersedes"], first["name"])
		self.assertEqual(
			frappe.db.get_value("DA Kebele Assignment", first["name"], ["state", "superseded_by"]),
			("Superseded", second["name"]),
		)
		self.assertEqual(MockDARegistryClient.shared().get_da("DA-000001").kebele, kebele_alt)
		row = {r["da_id"]: r for r in api.list_assignments()["data"]}["DA-000001"]
		self.assertEqual(
			(row["kebele"], row["assignment_status"], row["geofence"], row["flagged"]),
			(kebele_alt, "Effective", "Not checked", None),
		)
		self.assertEqual(api.list_assignments()["meta"]["summary"]["flagged"], 0)
		cov = {k["kebele"]: k for k in api.coverage()["data"]["kebeles"]}
		self.assertEqual((cov[kebele_alt]["coverage"], cov[kebele]["coverage"]), ("Full", "Uncovered"))
		# Only the latest decision is live; the history keeps every row.
		self.assertEqual(
			frappe.db.count("DA Kebele Assignment", {"da_id": "DA-000001", "state": "Effective"}), 1
		)
		self.assertEqual(frappe.db.count("DA Kebele Assignment", {"da_id": "DA-000001"}), 2)

	def test_coordinates_are_validated(self):
		frappe.set_user(SUP)
		frappe.db.commit()  # nosemgrep: error calls below roll back uncommitted rows
		for bad in (
			{"lat": 91, "lng": 37.5},
			{"lat": 9.1, "lng": -181},
			{"lat": "x", "lng": 37.5},
			{"lat": 9.1},
		):
			out = api.assign(da_id="DA-000001", kebele=self.kebele, da_location=bad)
			self.assertEqual(out["status"], "error", bad)
			self.assertEqual(frappe.response.get("http_status_code"), 400, bad)
		self.assertEqual(frappe.db.count("DA Kebele Assignment", {"da_id": "DA-000001"}), 0)
		with self.assertRaises(ValueError):
			geofence.validate_coordinates(float("nan"), 0)

	def test_client_op_id_is_unique_in_the_database(self):
		frappe.set_user("Administrator")
		a = svc.assign(
			da_id="DA-000001", kebele=self.kebele, client_op_id="kas-db-1", ctx=_ctx("Administrator")
		)
		doc = frappe.get_doc(
			{
				"doctype": "DA Kebele Assignment",
				"user": DA1,  # da_id is fetched from User.da_registry_id
				"kebele": self.kebele_alt,
				"state": "Effective",
				"mode": "Automatic",
				"effective_date": today(),
				"client_op_id": "kas-db-1",
			}
		)
		with self.assertRaises(frappe.DuplicateEntryError):
			doc.insert(ignore_permissions=True)
		self.assertEqual(
			frappe.db.get_value("DA Kebele Assignment", {"client_op_id": "kas-db-1"}, "name"), a["name"]
		)
		self.assertEqual(
			frappe.db.count("DA Kebele Assignment", {"da_id": "DA-000001", "client_op_id": ("is", "set")}), 1
		)

	def test_registry_down_is_503_and_notification_failure_is_not_fatal(self):
		frappe.set_user(SUP)
		frappe.db.commit()  # nosemgrep
		with patch.object(MockDARegistryClient, "list_das", side_effect=DARegistryUnavailable("down")):
			out = api.list_assignments()
			self.assertEqual((out["status"], out["code"]), ("error", "SERVICE_UNAVAILABLE"))
			self.assertEqual(frappe.response.get("http_status_code"), 503)
			self.assertEqual(frappe.response.get("http_status_code"), 503)
		with patch("da_services.services.assignments.notify_da", side_effect=RuntimeError("smtp down")):
			out = api.assign(da_id="DA-000001", kebele=self.kebele, client_op_id="kas-ntf-1")
		self.assertEqual(out["status"], "success", out)
		self.assertEqual(out["data"]["state"], "Effective")
		self.assertEqual(frappe.db.count("DA Notification", {"user": DA1}), 0)

	def test_assign_scope_and_validation(self):
		frappe.set_user(SUP)
		frappe.db.commit()  # nosemgrep: error calls below roll back uncommitted rows
		kebele, other_kebele = self.kebele, self.other_kebele
		self.assertEqual(
			api.assign(da_id="DA-000003", kebele=other_kebele)["status"], "error"
		)  # DA outside Woreda
		self.assertEqual(frappe.response.get("http_status_code"), 403)
		self.assertEqual(
			api.assign(da_id="DA-000001", kebele=other_kebele)["status"], "error"
		)  # Kebele outside Woreda
		self.assertEqual(frappe.response.get("http_status_code"), 403)
		self.assertEqual(api.assign(da_id="DA-000001", kebele=self.woreda)["status"], "error")  # not a Kebele
		self.assertEqual(
			api.assign(da_id="DA-000001", kebele=f"{self.woreda}-K99")["status"], "error"
		)  # unknown
		self.assertEqual(api.assign(da_id="DA-000001", kebele=kebele, mode="Weird")["status"], "error")
		frappe.set_user("Administrator")
		self.assertEqual(
			api.assign(da_id="DA-000003", kebele=other_kebele)["status"], "error"
		)  # separated DA
		frappe.set_user(DA1)
		self.assertEqual(api.assign(da_id="DA-000001", kebele=kebele)["status"], "error")
		frappe.set_user(EXEC)
		self.assertEqual(api.assign(da_id="DA-000001", kebele=kebele)["status"], "error")

	def test_assignment_rows_cannot_be_deleted(self):
		frappe.set_user("Administrator")
		a = svc.assign(da_id="DA-000001", kebele=self.kebele, ctx=_ctx("Administrator"))
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("DA Kebele Assignment", a["name"], ignore_permissions=True)
