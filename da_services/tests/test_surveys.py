"""Survey templates and responses (FR-09d).

Registry mock fixtures: DA-000001 (ET04 / ET04-W01), DA-000004 (ET03 / ET03-W02).
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import constants as C
from da_services.services import surveys as svc
from da_services.utils.permissions import get_scope_context

DA1 = "srv-da1@da.local"  # DA-000001, ET04-W01
DA3 = "srv-da3@da.local"  # DA-000004, ET03-W02
SUP = "srv-sup@da.local"  # Supervisor ET04-W01
SUP_OTHER = "srv-sup3@da.local"  # Supervisor ET03-W02
EXEC = "srv-exec@da.local"  # Executive ET04
ALL_USERS = (DA1, DA3, SUP, SUP_OTHER, EXEC)
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
		self._clean()
		MockDARegistryClient.reset()
		_user(DA1, [C.ROLE_DA])
		_user(DA3, [C.ROLE_DA])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_user(SUP_OTHER, [C.ROLE_SUPERVISOR])
		_user(EXEC, [C.ROLE_EXECUTIVE])
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA3, C.ROLE_DA, da_id="DA-000004", region_scope="ET03", woreda_scope="ET03-W02")
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP_OTHER, C.ROLE_SUPERVISOR, region_scope="ET03", woreda_scope="ET03-W02")
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope="ET04")

	def tearDown(self):
		frappe.set_user("Administrator")
		self._clean()
		MockDARegistryClient.reset()

	def _clean(self):
		frappe.db.delete("DA Survey Response")
		frappe.db.delete("DA Survey Template")
		frappe.db.delete("DA RBAC Assignment", {"user": ("in", ALL_USERS)})

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

	# -- responses -------------------------------------------------------------

	def test_submit_response_and_duplicate(self):
		survey = self._create()
		answers = {"p4": "4", "p5": "Yes, fully"}
		out = svc.submit_response(
			survey["id"],
			farmer_id="FR-1001",
			consent="granted",
			answers=answers,
			sync_state="Synced",
			ctx=_ctx(DA1),
		)
		self.assertEqual(out["sync_state"], "Synced")
		self.assertEqual(out["collector_da_id"], "DA-000001")

		detail = svc.get_survey(survey["id"], _ctx(DA1))
		self.assertEqual(detail["collected"], 1)
		self.assertIn("FR-1001", detail["answered"])

		with self.assertRaises(frappe.ValidationError):
			svc.submit_response(
				survey["id"],
				farmer_id="FR-1001",
				consent="granted",
				answers=answers,
				sync_state="Synced",
				ctx=_ctx(DA1),
			)

	def test_declined_consent_does_not_lock_farmer(self):
		survey = self._create()
		svc.submit_response(
			survey["id"],
			farmer_id="FR-2002",
			consent="declined",
			answers={},
			ctx=_ctx(DA1),
		)
		detail = svc.get_survey(survey["id"], _ctx(DA1))
		self.assertEqual(detail["collected"], 0)
		self.assertNotIn("FR-2002", detail["answered"])

		# Farmer can be surveyed again after a decline.
		out = svc.submit_response(
			survey["id"],
			farmer_id="FR-2002",
			consent="granted",
			answers={"p4": "5", "p5": "Partly"},
			sync_state="Synced",
			ctx=_ctx(DA1),
		)
		self.assertEqual(out["sync_state"], "Synced")

	def test_response_client_op_id_replay(self):
		survey = self._create()
		first = svc.submit_response(
			survey["id"],
			farmer_id="FR-3003",
			consent="granted",
			answers={"p4": "3", "p5": "No"},
			client_op_id="resp-op-1",
			ctx=_ctx(DA1),
		)
		second = svc.submit_response(
			survey["id"],
			farmer_id="FR-9999",
			consent="granted",
			answers={"p4": "1", "p5": "No"},
			client_op_id="resp-op-1",
			ctx=_ctx(DA1),
		)
		self.assertEqual(first["id"], second["id"])
		self.assertEqual(first["farmer_id"], "FR-3003")

	def test_draft_skips_required_validation(self):
		survey = self._create()
		out = svc.submit_response(
			survey["id"],
			farmer_id="FR-4004",
			answers={},
			sync_state="Draft",
			ctx=_ctx(DA1),
		)
		self.assertEqual(out["sync_state"], "Draft")

	# -- patch / results -------------------------------------------------------

	def test_patch_close_and_questions_blocked_after_response(self):
		survey = self._create()
		closed = svc.patch_template(survey["id"], {"status": "Closed"}, ctx=_ctx(SUP))
		self.assertEqual(closed["status"], "Closed")

		# Re-open for the questions check.
		svc.patch_template(survey["id"], {"status": "Open"}, ctx=_ctx(SUP))
		svc.submit_response(
			survey["id"],
			farmer_id="FR-5005",
			consent="granted",
			answers={"p4": "2", "p5": "Yes, fully"},
			ctx=_ctx(DA1),
		)
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
		self.assertTrue(out["templateVersion"].endswith(".1") or out["templateVersion"] != before)

	def test_results_aggregates(self):
		survey = self._create()
		svc.submit_response(
			survey["id"],
			farmer_id="FR-6001",
			consent="granted",
			answers={"p4": "4", "p5": "Yes, fully"},
			ctx=_ctx(DA1),
		)
		svc.submit_response(
			survey["id"],
			farmer_id="FR-6002",
			consent="granted",
			answers={"p4": "2", "p5": "No"},
			ctx=_ctx(DA1),
		)
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
