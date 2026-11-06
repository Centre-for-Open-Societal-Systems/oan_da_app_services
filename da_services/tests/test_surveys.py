"""Survey templates and responses (FR-09d).

Registry mock fixtures used here:
- DA-000001 / DA-000002 (ET04-W01, same Kebele) — cross-DA duplicate cases.
- DA-000004 (ET03-W02).

Farmer references are written explicitly so location, Kebele assignment and the
canonical internal ID are deterministic; the Farmer Registry mock is only used
for the collector DA reference.
"""

import io
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today
from openpyxl import load_workbook

from da_services.api.v1 import surveys as api
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import constants as C
from da_services.services import surveys as svc
from da_services.tests.fixtures import create_administrative_areas, ensure_kebele_assignment
from da_services.utils.permissions import get_scope_context

DA1 = "srv-da1@da.local"  # DA-000001, ET04-W01
DA2 = "srv-da2@da.local"  # DA-000002, ET04-W01
DA3 = "srv-da3@da.local"  # DA-000004, ET03-W02
SUP = "srv-sup@da.local"  # Supervisor ET04-W01
SUP_OTHER = "srv-sup3@da.local"  # Supervisor ET03-W02
EXEC = "srv-exec@da.local"  # Executive ET04
ADMIN = "srv-admin@da.local"  # OAN Administrator
COMMS = "srv-comms@da.local"  # Communications Officer
ALL_USERS = (DA1, DA2, DA3, SUP, SUP_OTHER, EXEC, ADMIN, COMMS)
TEST_DAS = ("DA-000001", "DA-000002", "DA-000004")

# canonical internal id, display id, Kebele, Woreda, Zone, Region
FARMERS = {
	"farmer-uuid-001": ("FR-000101", "Bako 01", "ET04-W01", "ET04-Z01", "ET04"),
	"farmer-uuid-002": ("FR-000102", "Bako 02", "ET04-W01", "ET04-Z01", "ET04"),
	"farmer-uuid-003": ("FR-000103", "Kebele 03", "ET03-W02", "ET03-Z02", "ET03"),
	"farmer-uuid-004": ("FR-000104", "Bako 01", "ET04-W01", "ET04-Z01", "ET04"),
}
FARMER_A = "farmer-uuid-001"
FARMER_B = "farmer-uuid-002"  # same Woreda, different Kebele
FARMER_C = "farmer-uuid-003"
FARMER_D = "farmer-uuid-004"  # same Kebele as FARMER_A

DOCTYPES = ("DA Survey Response", "DA Survey Template", "DA RBAC Assignment")


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


def _questions():
	return [
		{"prompt": "How useful was the advice?", "type": "rating", "options": "", "required": True},
		{
			"prompt": "Did you apply it?",
			"type": "single",
			"options": "Yes, fully, Partly, No",
			"required": True,
		},
	]


class TestSurveys(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		create_administrative_areas()
		self._clean()
		MockDARegistryClient.reset()

		_user(DA1, [C.ROLE_DA])
		_user(DA2, [C.ROLE_DA])
		_user(DA3, [C.ROLE_DA])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_user(SUP_OTHER, [C.ROLE_SUPERVISOR])
		_user(EXEC, [C.ROLE_EXECUTIVE])
		_user(ADMIN, [C.ROLE_ADMIN])
		_user(COMMS, [C.ROLE_COMMS])

		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA2, C.ROLE_DA, da_id="DA-000002", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA3, C.ROLE_DA, da_id="DA-000004", region_scope="ET03", woreda_scope="ET03-W02")
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP_OTHER, C.ROLE_SUPERVISOR, region_scope="ET03", woreda_scope="ET03-W02")
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope="ET04")

		# Both ET04 DAs cover Bako 01, which is what makes a cross-DA duplicate
		# reachable; DA-000004 covers a Kebele with no test farmer.
		ensure_kebele_assignment("DA-000001", "ET04-W01-K01")
		ensure_kebele_assignment("DA-000002", "ET04-W01-K01")
		ensure_kebele_assignment("DA-000004", "ET03-W02-K01")

		for farmer_id, (reg_id, kebele, woreda, zone, region) in FARMERS.items():
			frappe.get_doc(
				{
					"doctype": "DA Farmer Reference",
					"farmer_id": farmer_id,
					"reg_id": reg_id,
					"farmer_name": f"Test {farmer_id}",
					"kebele": kebele,
					"woreda": woreda,
					"zone": zone,
					"region": region,
				}
			).insert(ignore_permissions=True, ignore_links=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		self._clean()
		MockDARegistryClient.reset()

	def _clean(self):
		frappe.db.delete("DA Survey Response")
		frappe.db.delete("DA Survey Template")
		frappe.db.delete("DA RBAC Assignment", {"user": ("in", ALL_USERS)})
		frappe.db.delete("DA Kebele Assignment", {"da_id": ("in", TEST_DAS)})
		frappe.db.delete("DA Farmer Reference", {"name": ("in", list(FARMERS))})

	def _create(self, user=SUP, **over):
		data = {
			"name": "Meher season farmer satisfaction",
			"type": "Satisfaction",
			"open": today(),
			"close": add_days(today(), 20),
			"languages": ["English", "Amharic"],
			"targetFarmers": 20,
			"questions": _questions(),
		}
		data.update(over)
		return svc.create_template(ctx=_ctx(user), **data)

	def _submit(self, survey_id, user=DA1, farmer_id=FARMER_A, **over):
		data = {
			"farmer_id": farmer_id,
			"consent": "granted",
			"answers": {"p4": "4", "p5": "Yes, fully"},
			"sync_state": "Synced",
		}
		data.update(over)
		return svc.submit_response(survey_id, ctx=_ctx(user), **data)

	# -- create ----------------------------------------------------------------

	def test_supervisor_creates_open_survey_with_standard_params(self):
		out = self._create()
		self.assertEqual(out["status"], "Open")
		self.assertEqual(out["type"], "Satisfaction")
		self.assertEqual(out["targetFarmers"], 20)
		self.assertEqual(out["woreda"], "ET04-W01")
		self.assertTrue(out["templateVersion"].startswith("FSS-T"))
		types = [p["type"] for p in out["parameters"]]
		self.assertEqual(types[:3], ["consent", "auto", "auto"])
		self.assertIn("rating", types)
		self.assertGreaterEqual(out["estMinutes"], 2)

	def test_create_validation(self):
		with self.assertRaises(frappe.ValidationError):
			self._create(name="ab")
		with self.assertRaises(frappe.ValidationError):
			self._create(close=add_days(today(), -1))
		with self.assertRaises(frappe.ValidationError):
			self._create(languages=[])
		with self.assertRaises(frappe.ValidationError):
			self._create(targetFarmers=0)
		with self.assertRaises(frappe.ValidationError):
			self._create(questions=[])
		with self.assertRaises(frappe.ValidationError):
			self._create(
				questions=[{"prompt": "x", "type": "single", "options": "OnlyOne", "required": True}]
			)

	def test_da_cannot_create_template(self):
		with self.assertRaises(frappe.PermissionError):
			self._create(user=DA1)

	def test_client_op_id_idempotent_on_create(self):
		first = self._create(client_op_id="srv-op-1")
		second = self._create(client_op_id="srv-op-1")
		self.assertEqual(first["id"], second["id"])
		self.assertEqual(frappe.db.count("DA Survey Template"), 1)

	def test_template_client_op_id_replay_by_another_user_is_conflict(self):
		self._create(client_op_id="srv-op-2")
		with self.assertRaises(svc.SurveyConflictError) as caught:
			self._create(user=SUP_OTHER, client_op_id="srv-op-2")
		self.assertEqual(caught.exception.http_status_code, 409)
		self.assertEqual(frappe.db.count("DA Survey Template"), 1)

	# -- list scope ------------------------------------------------------------

	def test_list_scope_per_role(self):
		mine = self._create()
		theirs = self._create(user=SUP_OTHER, name="Other woreda needs assessment", type="Needs assessment")

		def ids(user):
			frappe.set_user(user)
			try:
				return [r["id"] for r in svc.list_surveys(_ctx(user))[0]]
			finally:
				frappe.set_user("Administrator")

		self.assertIn(mine["id"], ids(DA1))
		self.assertNotIn(theirs["id"], ids(DA1))
		self.assertIn(theirs["id"], ids(DA3))
		self.assertIn(mine["id"], ids(SUP))
		self.assertNotIn(theirs["id"], ids(SUP))
		self.assertIn(mine["id"], ids(EXEC))
		self.assertNotIn(theirs["id"], ids(EXEC))

	# -- responses: location, canonical id, farmer validation ------------------

	def test_submit_stores_farmer_location_and_canonical_farmer_id(self):
		survey = self._create()
		out = self._submit(survey["id"], farmer_id="FR-000101")

		self.assertEqual(out["farmer_id"], FARMER_A)
		self.assertEqual(out["collector_da_id"], "DA-000001")
		self.assertEqual(out["kebele"], "Bako 01")
		self.assertEqual(out["woreda"], "ET04-W01")
		self.assertEqual(out["zone"], "ET04-Z01")
		self.assertEqual(out["region"], "ET04")
		# Auto answers are normalised to the resolved farmer.
		self.assertEqual(out["answers"]["p2"], "FR-000101")
		self.assertEqual(out["answers"]["p3"], "Bako 01")

		detail = svc.get_survey(survey["id"], _ctx(DA1))
		self.assertEqual(detail["collected"], 1)
		self.assertIn(FARMER_A, detail["answered"])

	def test_farmer_outside_assigned_kebele_is_denied_and_not_stored(self):
		survey = self._create()
		with self.assertRaises(frappe.PermissionError):
			self._submit(survey["id"], farmer_id=FARMER_B)
		self.assertEqual(frappe.db.count("DA Survey Response"), 0)

	def test_unknown_farmer_is_404_and_not_stored(self):
		survey = self._create()
		with self.assertRaises(frappe.DoesNotExistError):
			self._submit(survey["id"], farmer_id="FR-does-not-exist")
		self.assertEqual(frappe.db.count("DA Survey Response"), 0)

	def test_farmer_outside_survey_woreda_is_denied_and_not_stored(self):
		survey = self._create(user=ADMIN, woreda="ET03-W02")
		with self.assertRaises(frappe.PermissionError):
			self._submit(
				survey["id"],
				user=ADMIN,
				da_id="DA-000001",
				farmer_id=FARMER_A,
			)
		self.assertEqual(frappe.db.count("DA Survey Response"), 0)

	def test_closed_survey_rejects_and_stores_nothing(self):
		survey = self._create()
		svc.patch_template(survey["id"], {"status": "Closed"}, ctx=_ctx(SUP))
		with self.assertRaises(frappe.ValidationError):
			self._submit(survey["id"])
		self.assertEqual(frappe.db.count("DA Survey Response"), 0)

	def test_unknown_answer_parameter_is_rejected_and_not_stored(self):
		survey = self._create()
		with self.assertRaises(frappe.ValidationError):
			self._submit(survey["id"], answers={"p4": "4", "p5": "Yes, fully", "p99": "x"})
		self.assertEqual(frappe.db.count("DA Survey Response"), 0)

	def test_missing_required_answer_is_rejected_and_not_stored(self):
		survey = self._create()
		with self.assertRaises(frappe.ValidationError):
			self._submit(survey["id"], answers={"p4": "4"})  # p5 is required
		self.assertEqual(frappe.db.count("DA Survey Response"), 0)

	# -- duplicates ------------------------------------------------------------

	def test_same_da_duplicate_is_409_across_id_formats(self):
		survey = self._create()
		first = self._submit(survey["id"], farmer_id="FR-000101")
		with self.assertRaises(svc.SurveyConflictError) as caught:
			self._submit(survey["id"], farmer_id=FARMER_A)
		self.assertEqual(caught.exception.http_status_code, 409)
		self.assertEqual(frappe.db.count("DA Survey Response"), 1)
		self.assertEqual(frappe.db.get_value("DA Survey Response", first["id"], "farmer_id"), FARMER_A)

	def test_cross_da_duplicate_is_409_and_stores_one_row(self):
		survey = self._create()
		self._submit(survey["id"], user=DA1)
		with self.assertRaises(svc.SurveyConflictError) as caught:
			self._submit(survey["id"], user=DA2)
		self.assertEqual(caught.exception.http_status_code, 409)
		self.assertEqual(frappe.db.count("DA Survey Response"), 1)

	def test_concurrent_duplicate_insert_race_is_409(self):
		"""The unique constraint still wins when two requests pass the pre-check."""
		survey = self._create()
		frappe.get_doc(
			{
				"doctype": "DA Survey Response",
				"survey": survey["id"],
				"da_id": "DA-000001",
				"farmer_id": FARMER_A,
				"consent": "granted",
				"answers_json": "{}",
				"sync_state": "Synced",
				"duplicate_guard_key": f"{survey['id']}|{FARMER_A}",
				"kebele": "Bako 01",
			}
		).insert(ignore_permissions=True)

		real_exists = frappe.db.exists

		def racy_exists(doctype, filters=None, *args, **kwargs):
			# Pretend the competing request has not committed yet, so the helper's
			# pre-check misses the row and the insert must hit the unique constraint.
			if (
				doctype == "DA Survey Response"
				and isinstance(filters, dict)
				and filters.get("duplicate_guard_key")
			):
				return False
			return real_exists(doctype, filters, *args, **kwargs)

		with patch.object(frappe.db, "exists", side_effect=racy_exists):
			with self.assertRaises(svc.SurveyConflictError) as caught:
				self._submit(survey["id"], farmer_id=FARMER_A)
		self.assertEqual(caught.exception.http_status_code, 409)
		self.assertEqual(frappe.db.count("DA Survey Response"), 1)

	def test_same_farmer_on_a_different_template_is_allowed(self):
		first = self._create()
		second = self._create(name="Post-harvest needs assessment", type="Needs assessment")
		self._submit(first["id"], farmer_id=FARMER_A)
		out = self._submit(second["id"], farmer_id=FARMER_A)
		self.assertEqual(out["survey_id"], second["id"])
		self.assertEqual(frappe.db.count("DA Survey Response"), 2)

	def test_declined_consent_does_not_lock_farmer(self):
		survey = self._create()
		self._submit(survey["id"], farmer_id=FARMER_A, consent="declined", answers={})
		detail = svc.get_survey(survey["id"], _ctx(DA1))
		self.assertEqual(detail["collected"], 0)
		self.assertNotIn(FARMER_A, detail["answered"])

		# Farmer can be surveyed again after a decline (pending business decision).
		out = self._submit(survey["id"], farmer_id=FARMER_A, answers={"p4": "5", "p5": "Partly"})
		self.assertEqual(out["sync_state"], "Synced")

	def test_draft_skips_required_validation(self):
		survey = self._create()
		out = self._submit(
			survey["id"],
			farmer_id=FARMER_A,
			consent=None,
			answers={},
			sync_state="Draft",
		)
		self.assertEqual(out["sync_state"], "Draft")

	# -- idempotency security --------------------------------------------------

	def test_response_client_op_id_replay_same_payload(self):
		survey = self._create()
		first = self._submit(survey["id"], farmer_id="FR-000101", client_op_id="resp-op-1")
		second = self._submit(survey["id"], farmer_id=FARMER_A, client_op_id="resp-op-1")
		self.assertEqual(first["id"], second["id"])
		self.assertEqual(frappe.db.count("DA Survey Response"), 1)

	def test_response_client_op_id_replay_with_different_payload_is_conflict(self):
		survey = self._create()
		self._submit(survey["id"], farmer_id=FARMER_A, client_op_id="resp-op-2")
		with self.assertRaises(svc.SurveyConflictError):
			self._submit(
				survey["id"],
				farmer_id=FARMER_D,
				client_op_id="resp-op-2",
			)
		self.assertEqual(frappe.db.count("DA Survey Response"), 1)

	def test_response_client_op_id_replay_on_another_survey_is_conflict(self):
		first = self._create()
		second = self._create(name="Post-harvest needs assessment", type="Needs assessment")
		self._submit(first["id"], farmer_id=FARMER_A, client_op_id="resp-op-3")
		with self.assertRaises(svc.SurveyConflictError):
			self._submit(second["id"], farmer_id=FARMER_A, client_op_id="resp-op-3")
		self.assertEqual(frappe.db.count("DA Survey Response"), 1)

	def test_response_client_op_id_cannot_leak_another_collectors_row(self):
		survey = self._create()
		self._submit(survey["id"], user=DA1, farmer_id=FARMER_A, client_op_id="resp-op-4")
		with self.assertRaises(svc.SurveyConflictError):
			self._submit(survey["id"], user=DA2, farmer_id=FARMER_A, client_op_id="resp-op-4")
		self.assertEqual(frappe.db.count("DA Survey Response"), 1)

	# -- patch / results -------------------------------------------------------

	def test_patch_close_and_questions_blocked_after_response(self):
		survey = self._create()
		closed = svc.patch_template(survey["id"], {"status": "Closed"}, ctx=_ctx(SUP))
		self.assertEqual(closed["status"], "Closed")

		# Re-open for the questions check.
		svc.patch_template(survey["id"], {"status": "Open"}, ctx=_ctx(SUP))
		self._submit(survey["id"], farmer_id=FARMER_A, answers={"p4": "2", "p5": "Yes, fully"})
		with self.assertRaises(frappe.ValidationError):
			svc.patch_template(
				survey["id"],
				{"questions": [{"prompt": "New Q", "type": "text", "options": "", "required": False}]},
				ctx=_ctx(SUP),
			)

	def test_patch_questions_before_responses_bumps_version(self):
		survey = self._create()
		before = survey["templateVersion"]
		out = svc.patch_template(
			survey["id"],
			{
				"questions": [
					{"prompt": "Rate the visit", "type": "rating", "options": "", "required": True},
				]
			},
			ctx=_ctx(SUP),
		)
		self.assertNotEqual(out["templateVersion"], before)

	def test_results_aggregates(self):
		survey = self._create()
		self._submit(survey["id"], farmer_id=FARMER_A, answers={"p4": "4", "p5": "Yes, fully"})
		self._submit(survey["id"], farmer_id=FARMER_D, answers={"p4": "2", "p5": "No"})
		results = svc.survey_results(survey["id"], _ctx(SUP))
		self.assertEqual(results["collected"], 2)
		self.assertEqual(results["by_status"].get("Synced"), 2)
		rating = next(p for p in results["parameters"] if p["type"] == "rating")
		self.assertEqual(rating["summary"]["average"], 3.0)

	def test_executive_reads_results_cannot_create(self):
		survey = self._create()
		out = svc.survey_results(survey["id"], _ctx(EXEC))
		self.assertEqual(out["survey_id"], survey["id"])
		with self.assertRaises(frappe.PermissionError):
			self._create(user=EXEC)

	# -- Excel export ----------------------------------------------------------

	def test_export_workbook_contains_headers_and_responses(self):
		survey = self._create()
		self._submit(survey["id"], farmer_id=FARMER_A)

		filename, content = svc.export_responses(survey["id"], _ctx(SUP))
		self.assertTrue(filename.endswith(".xlsx"))
		self.assertIsInstance(content, bytes)

		workbook = load_workbook(filename=io.BytesIO(content))
		sheet = workbook.active
		headers = [cell.value for cell in sheet[1]]
		self.assertEqual(headers[:3], ["Response ID", "Survey ID", "Template Version"])
		self.assertIn("Farmer ID", headers)
		self.assertIn("Kebele", headers)
		self.assertEqual(len(sheet.iter_rows()), 2)
		row = [cell.value for cell in sheet[2]]
		self.assertEqual(row[headers.index("Farmer ID")], FARMER_A)
		self.assertEqual(row[headers.index("Farmer Registry ID")], "FR-000101")
		self.assertEqual(row[headers.index("Collector DA ID")], "DA-000001")
		self.assertEqual(row[headers.index("Kebele")], "Bako 01")

	def test_export_empty_survey_is_headers_only(self):
		survey = self._create()
		_filename, content = svc.export_responses(survey["id"], _ctx(SUP))
		workbook = load_workbook(filename=io.BytesIO(content))
		self.assertEqual(len(workbook.active.iter_rows()), 1)

	def test_export_is_restricted_to_result_roles(self):
		survey = self._create()
		for user in (DA1, COMMS):
			with self.assertRaises(frappe.PermissionError):
				svc.export_responses(survey["id"], _ctx(user))

	def test_export_route_sets_a_download_response(self):
		survey = self._create()
		self._submit(survey["id"], farmer_id=FARMER_A)
		frappe.set_user(SUP)
		frappe.response.clear()
		try:
			api.export_responses(survey["id"])
			self.assertEqual(frappe.response.get("type"), "download")
			self.assertTrue(str(frappe.response.get("filename")).endswith(".xlsx"))
			self.assertTrue(frappe.response.get("filecontent"))
		finally:
			frappe.set_user("Administrator")
			frappe.response.clear()
