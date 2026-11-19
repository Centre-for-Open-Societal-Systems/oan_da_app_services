"""Survey templates and responses (FR-09d).

The service only saves, reads, aggregates and exports. Who may do what comes from Role
DocPerm and the permission hooks, so every call here runs as the session user.
"""

import io
from contextlib import contextmanager

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today
from openpyxl import load_workbook

from da_services.api.v1 import surveys as api
from da_services.services import constants as C
from da_services.services import surveys as svc
from da_services.tests.fixtures import create_administrative_areas, ensure_doctype_schema

DA1 = "srv-da1@da.local"  # DA-000001, ET04-W01
DA2 = "srv-da2@da.local"  # DA-000002, ET04-W01
DA3 = "srv-da3@da.local"  # DA-000004, ET03-W02
SUP = "srv-sup@da.local"  # Supervisor ET04-W01
SUP_OTHER = "srv-sup3@da.local"  # Supervisor ET03-W02
EXEC = "srv-exec@da.local"  # Executive ET04
ALL_USERS = (DA1, DA2, DA3, SUP, SUP_OTHER, EXEC)


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


def _grant(user, role, **kw):
	doc = frappe.new_doc("DA RBAC Assignment")
	doc.update({"user": user, "role": role, "effective_from": today(), **kw})
	doc.insert(ignore_permissions=True, ignore_links=True)


@contextmanager
def _as(user):
	frappe.set_user(user)
	try:
		yield
	finally:
		frappe.set_user("Administrator")


QUESTIONS = [
	{"prompt": "How useful was the advice?", "type": "rating", "options": "", "required": True},
	{"prompt": "Did you apply it?", "type": "single", "options": ["Yes, fully", "Partly", "No"]},
	{"prompt": "Which crops?", "type": "multi", "options": "Teff, Maize, Wheat", "required": False},
]
ANSWERS = {"p4": "4", "p5": "Yes, fully", "p6": ["Teff", "Maize"]}


class TestSurveys(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		ensure_doctype_schema("DA Survey Parameter")
		create_administrative_areas()
		self._clean()
		_user(DA1, [C.ROLE_DA])
		_user(DA2, [C.ROLE_DA])
		_user(DA3, [C.ROLE_DA])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_user(SUP_OTHER, [C.ROLE_SUPERVISOR])
		_user(EXEC, [C.ROLE_EXECUTIVE])
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA2, C.ROLE_DA, da_id="DA-000002", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA3, C.ROLE_DA, da_id="DA-000004", region_scope="ET03", woreda_scope="ET03-W02")
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP_OTHER, C.ROLE_SUPERVISOR, region_scope="ET03", woreda_scope="ET03-W02")
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope="ET04")

	def tearDown(self):
		frappe.set_user("Administrator")
		self._clean()

	def _clean(self):
		frappe.db.delete("DA Survey Response")
		frappe.db.delete("DA Survey Template")
		frappe.db.delete("DA Task", {"generated_by_rule": ("like", f"{svc.ASSIGNMENT_RULE_PREFIX}%")})
		frappe.db.delete("DA RBAC Assignment", {"user": ("in", ALL_USERS)})

	def _create(self, user=SUP, **over) -> str:
		data = {
			"name": "Meher season farmer satisfaction",
			"type": "Satisfaction",
			"open": today(),
			"close": add_days(today(), 20),
			"languages": ["English", "Amharic"],
			"targetFarmers": 20,
			"questions": QUESTIONS,
			**over,
		}
		with _as(user):
			return svc.create_template(**data)

	def _respond(self, survey_id, user=DA1, farmer_id="FR-000101", **over):
		data = {"farmer_id": farmer_id, "consent": "granted", "answers": ANSWERS, "kebele": "Bako 01", **over}
		with _as(user):
			svc.create_response(survey_id, **data)
		return frappe.get_last_doc("DA Survey Response", filters={"survey": survey_id})

	# -- POST /surveys/templates ---------------------------------------------------

	def test_create_saves_template_with_standard_questions_and_area(self):
		survey_id = self._create()
		doc = frappe.get_doc("DA Survey Template", survey_id)
		self.assertEqual(doc.status, "Open")
		self.assertEqual(doc.template_version.split("-")[0], "FSS")
		self.assertEqual(doc.created_by_user, SUP)
		# The Supervisor's only Woreda is used, Region and Zone come from it.
		self.assertEqual((doc.region, doc.zone, doc.woreda), ("ET04", "ET04-Z01", "ET04-W01"))
		self.assertEqual([p.parameter_id for p in doc.parameters], ["p1", "p2", "p3", "p4", "p5", "p6"])
		self.assertEqual(doc.language, "English, Amharic")
		multi = doc.parameters[5]
		self.assertEqual([multi.option_1, multi.option_2, multi.option_3], ["Teff", "Maize", "Wheat"])

	def test_create_outside_own_woreda_or_by_a_da_is_denied(self):
		with self.assertRaises(frappe.PermissionError):
			self._create(woreda="ET03-W02")
		with self.assertRaises(frappe.PermissionError):
			self._create(user=DA1, woreda="ET04-W01")

	def test_create_with_selected_das_assigns_them(self):
		survey_id = self._create(assignmentMode="Selected", assignedDaIds=["DA-000002"])
		with _as(SUP):
			out = svc.get_template(survey_id)
		self.assertEqual(out["assignmentMode"], "Selected")
		self.assertEqual(out["assignedDaIds"], ["DA-000002"])

	def test_create_api_returns_the_new_id(self):
		with _as(SUP):
			out = api.create_template(
				name="API survey",
				type="Satisfaction",
				open=today(),
				close=add_days(today(), 5),
				languages=["English"],
				targetFarmers=5,
				questions=QUESTIONS,
			)
		self.assertTrue(frappe.db.exists("DA Survey Template", out["data"]["id"]))

	# -- PATCH /surveys/templates/<id> ---------------------------------------------

	def test_patch_sets_fields_in_place(self):
		survey_id = self._create()
		with _as(SUP):
			saved = svc.patch_template(survey_id, {"name": "Renamed", "status": "Closed", "targetFarmers": 7})
		self.assertEqual(saved, survey_id)
		doc = frappe.get_doc("DA Survey Template", survey_id)
		self.assertEqual((doc.title, doc.status, doc.target_farmers), ("Renamed", "Closed", 7))

	def test_patch_questions_without_responses_edits_in_place(self):
		survey_id = self._create()
		version = frappe.db.get_value("DA Survey Template", survey_id, "template_version")
		with _as(SUP):
			saved = svc.patch_template(survey_id, {"questions": QUESTIONS[:1]})
		doc = frappe.get_doc("DA Survey Template", saved)
		self.assertEqual(saved, survey_id)
		self.assertEqual(len(doc.parameters), 4)
		self.assertNotEqual(doc.template_version, version)

	def test_patch_questions_after_responses_creates_a_new_version(self):
		survey_id = self._create()
		self._respond(survey_id)
		with _as(SUP):
			saved = svc.patch_template(survey_id, {"questions": QUESTIONS[:1]})
		self.assertNotEqual(saved, survey_id)
		self.assertEqual(len(frappe.get_doc("DA Survey Template", survey_id).parameters), 6)
		self.assertEqual(len(frappe.get_doc("DA Survey Template", saved).parameters), 4)

	def test_patch_assignment_mode_switches(self):
		survey_id = self._create(assignmentMode="Selected", assignedDaIds=["DA-000002"])
		with _as(SUP):
			svc.patch_template(survey_id, {"assignmentMode": "Woreda"})
			self.assertEqual(svc.get_template(survey_id)["assignmentMode"], "Woreda")

	def test_executive_or_other_supervisor_cannot_patch(self):
		survey_id = self._create()
		for user in (EXEC, SUP_OTHER):
			with _as(user), self.assertRaises(frappe.PermissionError):
				svc.patch_template(survey_id, {"name": "Nope"})

	# -- GET /surveys/templates[/<id>] ---------------------------------------------

	def test_list_templates_is_scoped_and_filtered(self):
		mine = self._create(name="Mine")
		closed = self._create(name="Mine closed")
		with _as(SUP):
			svc.patch_template(closed, {"status": "Closed"})
		self._create(user=SUP_OTHER, name="Other Woreda")
		with _as(SUP):
			rows, total = svc.list_templates()
			self.assertEqual({r["id"] for r in rows}, {mine, closed})
			self.assertEqual(total, 2)
			rows, total = svc.list_templates(status="Closed")
			self.assertEqual(([r["id"] for r in rows], total), ([closed], 1))
			rows, total = svc.list_templates(limit=1)
			self.assertEqual((len(rows), total), (1, 2))

	def test_get_template_outside_scope_is_denied(self):
		survey_id = self._create()
		with _as(SUP_OTHER), self.assertRaises(frappe.PermissionError):
			svc.get_template(survey_id)

	# -- GET /surveys[/<id>] (DA) ---------------------------------------------------

	def test_da_lists_woreda_and_own_assigned_surveys_with_own_counts(self):
		woreda_wide = self._create(name="Woreda wide")
		for_da2 = self._create(name="Only DA2", assignmentMode="Selected", assignedDaIds=["DA-000002"])
		self._respond(woreda_wide, user=DA2, farmer_id="FR-000102")
		with _as(DA1):
			rows, _total = svc.list_surveys()
			self.assertEqual([r["id"] for r in rows], [woreda_wide])
			self.assertEqual(rows[0]["collected"], 0)  # DA2's response is not DA1's
			self.assertNotIn("assignedDaIds", rows[0])
		with _as(DA2):
			rows, _total = svc.list_surveys()
			self.assertEqual({r["id"] for r in rows}, {woreda_wide, for_da2})

	def test_get_survey_returns_parameters_and_denies_other_woreda(self):
		survey_id = self._create()
		with _as(DA1):
			out = svc.get_survey(survey_id)
		self.assertEqual(len(out["parameters"]), 6)
		with _as(DA3), self.assertRaises(frappe.PermissionError):
			svc.get_survey(survey_id)

	# -- POST /surveys/<id>/responses -----------------------------------------------

	def test_da_saves_a_response(self):
		survey_id = self._create()
		row = self._respond(survey_id)
		self.assertEqual((row.da_id, row.farmer_id, row.sync_state), ("DA-000001", "FR-000101", "Synced"))
		self.assertEqual(
			(row.region, row.zone, row.woreda, row.kebele), ("ET04", "ET04-Z01", "ET04-W01", "Bako 01")
		)
		self.assertEqual(
			[(a.parameter_id, a.answer_text, a.answer_rating) for a in row.answers],
			[("p4", "4", 4), ("p5", "Yes, fully", 0), ("p6", "Teff", 0), ("p6", "Maize", 0)],
		)

	def test_da_cannot_save_for_another_da_or_outside_their_woreda(self):
		survey_id = self._create()
		with self.assertRaises(frappe.PermissionError):
			self._respond(survey_id, da_id="DA-000002")
		with self.assertRaises(frappe.PermissionError):
			self._respond(survey_id, user=DA3)

	def test_supervisor_saves_for_a_da_in_scope_only(self):
		survey_id = self._create()
		row = self._respond(survey_id, user=SUP, da_id="DA-000002")
		self.assertEqual(row.da_id, "DA-000002")
		with self.assertRaises(frappe.PermissionError):
			self._respond(survey_id, user=SUP_OTHER, da_id="DA-000004")

	def test_submit_api_is_message_only(self):
		survey_id = self._create()
		with _as(DA1):
			out = api.submit_response(survey_id, farmer_id="FR-000101", consent="granted", answers=ANSWERS)
		self.assertEqual(out["message"], "Survey response saved")
		self.assertFalse(out.get("data"))

	# -- GET /surveys/<id>/responses ------------------------------------------------

	def test_da_lists_own_responses_supervisor_lists_all(self):
		survey_id = self._create()
		self._respond(survey_id, user=DA1, farmer_id="FR-000101")
		self._respond(survey_id, user=DA2, farmer_id="FR-000102")
		with _as(DA1):
			rows, total = svc.list_responses(survey_id)
		self.assertEqual(([r["farmer_id"] for r in rows], total), (["FR-000101"], 1))
		self.assertEqual(rows[0]["answers"], ANSWERS)
		with _as(SUP):
			_rows, total = svc.list_responses(survey_id)
			self.assertEqual(total, 2)
			rows, total = svc.list_responses(survey_id, da_id="DA-000002")
			self.assertEqual(([r["collector_da_id"] for r in rows], total), (["DA-000002"], 1))

	# -- GET /surveys/<id>/results ---------------------------------------------------

	def test_results_aggregate_answers(self):
		survey_id = self._create()
		self._respond(survey_id, user=DA1, farmer_id="FR-000101")
		self._respond(survey_id, user=DA2, farmer_id="FR-000102", answers={"p4": "2", "p5": "No"})
		self._respond(
			survey_id, user=DA2, farmer_id="FR-000103", consent="declined", answers={}, sync_state="Declined"
		)
		with _as(EXEC):
			out = svc.survey_results(survey_id)
		self.assertEqual((out["collected"], out["by_status"]), (2, {"Synced": 2, "Declined": 1}))
		rating, single, multi = out["parameters"]
		self.assertEqual(rating["summary"]["average"], 3.0)
		self.assertEqual(single["summary"]["counts"], {"Yes, fully": 1, "No": 1})
		self.assertEqual(multi["summary"]["counts"], {"Teff": 1, "Maize": 1})

	# -- GET /surveys/<id>/responses/export -------------------------------------------

	def test_export_builds_a_workbook_of_readable_responses(self):
		survey_id = self._create()
		self._respond(survey_id)
		with _as(SUP):
			filename, content = svc.export_responses(survey_id)
		self.assertEqual(filename, f"survey-{survey_id}-responses.xlsx")
		workbook = load_workbook(io.BytesIO(content))
		self.assertEqual(workbook[svc.EXPORT_SHEET].max_row, 2)
		self.assertEqual(workbook[svc.EXPORT_ANSWERS_SHEET].max_row, 4)
		with _as(SUP_OTHER), self.assertRaises(frappe.PermissionError):
			svc.export_responses(survey_id)

	def test_export_route_sets_a_download_response(self):
		survey_id = self._create()
		self._respond(survey_id)
		frappe.response.clear()
		try:
			with _as(SUP):
				api.export_responses(survey_id)
			self.assertEqual(frappe.response.get("type"), "download")
			self.assertEqual(frappe.response.get("content_type"), api.XLSX_MIME)
			self.assertTrue(frappe.response.get("filecontent"))
		finally:
			frappe.response.clear()
