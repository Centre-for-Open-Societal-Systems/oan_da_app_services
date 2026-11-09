"""Survey templates and responses (FR-09d).

Registry mock fixtures used here:
- DA-000001 / DA-000002 (ET04-W01, same Kebele) — cross-DA duplicate cases.
- DA-000004 (ET03-W02).

Farmer references are written explicitly so location, Kebele assignment and the
canonical internal ID are deterministic; the Farmer Registry mock is only used
for the collector DA reference.
"""

import io
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime, today
from openpyxl import load_workbook

from da_services.api.v1 import surveys as api
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import constants as C
from da_services.services import surveys as svc
from da_services.tests.fixtures import (
	create_administrative_areas,
	ensure_doctype_schema,
	ensure_kebele_assignment,
)
from da_services.utils.permissions import get_scope_context

DA1 = "srv-da1@da.local"  # DA-000001, ET04-W01
DA2 = "srv-da2@da.local"  # DA-000002, ET04-W01
DA3 = "srv-da3@da.local"  # DA-000004, ET03-W02
SUP = "srv-sup@da.local"  # Supervisor ET04-W01
SUP_OTHER = "srv-sup3@da.local"  # Supervisor ET03-W02
EXEC = "srv-exec@da.local"  # Executive ET04
ADMIN = "srv-admin@da.local"  # OAN Administrator
COMMS = "srv-comms@da.local"  # Communications Officer
# Scope-shape users for the H1 authorization rules. The DocType controller requires a
# Woreda on a Supervisor grant, so the Region-only / Zone-only rows are created valid
# and then narrowed directly in the database to model legacy/backfilled grants.
REGION_SUP = "srv-region-sup@da.local"
ZONE_SUP = "srv-zone-sup@da.local"
NO_SCOPE_SUP = "srv-noscope-sup@da.local"
EXPIRED_SUP = "srv-expired-sup@da.local"
DUAL = "srv-dual@da.local"  # DA-000001 + Supervisor, both ET04-W01
ALL_USERS = (
	DA1,
	DA2,
	DA3,
	SUP,
	SUP_OTHER,
	EXEC,
	ADMIN,
	COMMS,
	REGION_SUP,
	ZONE_SUP,
	NO_SCOPE_SUP,
	EXPIRED_SUP,
	DUAL,
)
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


@contextmanager
def _keep_fixtures():
	"""Keep the test transaction intact while a service recovers from a race.

	Production code calls ``frappe.db.rollback()`` after a unique-constraint error
	to clear the failed request before re-reading the winner. In a test that would
	also discard the fixtures created by ``setUp``, and committing instead would
	leak rows past Frappe's class-level rollback. No-op the rollback: the failed
	insert was never applied, so the re-read still sees the original row.
	"""
	with patch.object(frappe.db, "rollback", lambda *a, **k: None):
		yield


def _questions():
	return [
		{"prompt": "How useful was the advice?", "type": "rating", "options": "", "required": True},
		{
			"prompt": "Did you apply it?",
			"type": "single",
			"options": ["Yes, fully", "Partly", "No"],
			"required": True,
		},
	]


class TestSurveys(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		ensure_doctype_schema("DA Survey Parameter")
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
		for user in (REGION_SUP, ZONE_SUP, NO_SCOPE_SUP, EXPIRED_SUP):
			_user(user, [C.ROLE_SUPERVISOR])
		_user(DUAL, [C.ROLE_DA, C.ROLE_SUPERVISOR])

		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA2, C.ROLE_DA, da_id="DA-000002", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA3, C.ROLE_DA, da_id="DA-000004", region_scope="ET03", woreda_scope="ET03-W02")
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP_OTHER, C.ROLE_SUPERVISOR, region_scope="ET03", woreda_scope="ET03-W02")
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope="ET04")

		# Region-only / Zone-only grants: create them valid, then remove the Woreda so
		# the scope model has to derive the target from master data.
		region_grant = _grant(REGION_SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		frappe.db.set_value("DA RBAC Assignment", region_grant.name, "woreda_scope", None)
		zone_grant = _grant(
			ZONE_SUP,
			C.ROLE_SUPERVISOR,
			region_scope="ET04",
			zone_scope="ET04-Z01",
			woreda_scope="ET04-W01",
		)
		frappe.db.set_value("DA RBAC Assignment", zone_grant.name, "woreda_scope", None)
		_grant(
			EXPIRED_SUP,
			C.ROLE_SUPERVISOR,
			region_scope="ET04",
			woreda_scope="ET04-W01",
			effective_from=add_days(today(), -10),
			effective_to=add_days(today(), -1),
		)
		_grant(DUAL, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DUAL, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")

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
		frappe.db.delete("DA Task", {"generated_by_rule": ("like", f"{svc.ASSIGNMENT_RULE_PREFIX}%")})
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

	def _bump_questions(self, survey_id):
		return svc.patch_template(
			survey_id,
			{
				"questions": [
					*_questions(),
					{
						"prompt": "Any other feedback?",
						"type": "text",
						"options": "",
						"required": False,
					},
				]
			},
			ctx=_ctx(SUP),
		)

	def _force_template_version(self, survey_id, version="FSS-T999-v9.9"):
		frappe.db.set_value("DA Survey Template", survey_id, "template_version", version)
		frappe.clear_document_cache("DA Survey Template", survey_id)

	def _insert_historical_draft(self, survey, farmer_id=FARMER_A):
		row = frappe.get_doc(
			{
				"doctype": "DA Survey Response",
				"survey": survey["id"],
				"template_version": survey["templateVersion"],
				"da_id": "DA-000001",
				"farmer_id": farmer_id,
				"answers_json": "{}",
				"sync_state": "Draft",
				"started_at": now_datetime(),
				"region": "ET04",
				"zone": "ET04-Z01",
				"woreda": "ET04-W01",
				"kebele": "Bako 01",
			}
		)
		row.insert(ignore_permissions=True)
		return row.name

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

	def test_questions_store_multilingual_prompts(self):
		questions = _questions()
		questions[0]["translations"] = {
			"Amharic": "ምክሩ ምን ያህል ጠቃሚ ነበር?",
			"om": "Gorsichi hangam fayyadaa ture?",
		}
		out = self._create(questions=questions, languages=["English", "Amharic", "Afaan Oromoo"])
		question = next(p for p in out["parameters"] if p["id"] == "p4")
		self.assertEqual(
			question["translations"],
			{
				"Amharic": "ምክሩ ምን ያህል ጠቃሚ ነበር?",
				"Afaan Oromoo": "Gorsichi hangam fayyadaa ture?",
			},
		)
		self.assertEqual(question["prompt"], "How useful was the advice?")

		questions[0]["translations"] = {"am": "አዲስ ጥያቄ"}
		updated = svc.patch_template(survey_id=out["id"], body={"questions": questions}, ctx=_ctx(SUP))
		question = next(p for p in updated["parameters"] if p["id"] == "p4")
		self.assertEqual(question["translations"], {"Amharic": "አዲስ ጥያቄ"})

	def test_unknown_question_translation_language_is_rejected(self):
		questions = _questions()
		questions[0]["translations"] = {"Klingon": "nuqneH"}
		with self.assertRaises(frappe.ValidationError):
			self._create(questions=questions)

	def test_multilingual_payload_is_idempotent(self):
		questions = _questions()
		questions[0]["translations"] = {"Amharic": "ምክሩ ጠቃሚ ነበር?"}
		first = self._create(client_op_id="srv-i18n-op", questions=questions)
		second = self._create(client_op_id="srv-i18n-op", questions=questions)
		self.assertEqual(first["id"], second["id"])
		self.assertEqual(frappe.db.count("DA Survey Template"), 1)

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

	def test_template_client_op_id_replay_with_different_payload_is_conflict(self):
		self._create(client_op_id="srv-op-diff")
		with self.assertRaises(svc.SurveyConflictError) as caught:
			self._create(client_op_id="srv-op-diff", name="A different survey")
		self.assertEqual(caught.exception.http_status_code, 409)
		self.assertEqual(frappe.db.count("DA Survey Template"), 1)

	def test_template_client_op_id_concurrent_replay_returns_existing(self):
		first = self._create(client_op_id="srv-op-race")
		real_lookup = svc._find_template_by_client_op
		calls = {"n": 0}

		def flaky_lookup(client_op_id):
			calls["n"] += 1
			return None if calls["n"] == 1 else real_lookup(client_op_id)

		with (
			patch.object(svc, "_find_template_by_client_op", side_effect=flaky_lookup),
			_keep_fixtures(),
		):
			second = self._create(client_op_id="srv-op-race")
		self.assertEqual(second["id"], first["id"])
		self.assertEqual(frappe.db.count("DA Survey Template"), 1)

	def test_template_client_op_id_concurrent_payload_mismatch_is_conflict(self):
		self._create(client_op_id="srv-op-race-diff")
		real_lookup = svc._find_template_by_client_op
		calls = {"n": 0}

		def flaky_lookup(client_op_id):
			calls["n"] += 1
			return None if calls["n"] == 1 else real_lookup(client_op_id)

		with (
			patch.object(svc, "_find_template_by_client_op", side_effect=flaky_lookup),
			_keep_fixtures(),
		):
			with self.assertRaises(svc.SurveyConflictError) as caught:
				self._create(client_op_id="srv-op-race-diff", name="Different payload")
		self.assertEqual(caught.exception.http_status_code, 409)
		self.assertEqual(frappe.db.count("DA Survey Template"), 1)

	# -- template scope authorization (H1) ------------------------------------

	def test_create_stores_authoritative_woreda_hierarchy(self):
		out = self._create(user=SUP, woreda="ET04-W01")
		self.assertEqual(
			(out["region"], out["zone"], out["woreda"]),
			("ET04", "ET04-Z01", "ET04-W01"),
		)
		# Lower-case input resolves through master data and is stored canonically.
		lower = self._create(user=SUP, woreda="et04-w01", name="Lower case target")
		self.assertEqual(lower["woreda"], "ET04-W01")

	def test_matching_explicit_region_and_zone_are_accepted(self):
		out = self._create(
			user=SUP,
			woreda="ET04-W01",
			region="ET04",
			zone="ET04-Z01",
		)
		self.assertEqual((out["region"], out["zone"]), ("ET04", "ET04-Z01"))

	def test_region_only_supervisor_can_publish_inside_region(self):
		out = self._create(user=REGION_SUP, woreda="ET04-W01")
		self.assertEqual(out["woreda"], "ET04-W01")
		with self.assertRaises(frappe.PermissionError):
			self._create(user=REGION_SUP, woreda="ET03-W02", name="Other region")

	def test_zone_only_supervisor_can_publish_inside_zone(self):
		out = self._create(user=ZONE_SUP, woreda="ET04-W01")
		self.assertEqual(out["zone"], "ET04-Z01")
		with self.assertRaises(frappe.PermissionError):
			self._create(user=ZONE_SUP, woreda="ET03-W02", name="Other zone")

	def test_supervisor_without_active_assignment_cannot_publish(self):
		with self.assertRaises(frappe.PermissionError):
			self._create(user=NO_SCOPE_SUP, woreda="ET04-W01")

	def test_expired_assignment_cannot_publish(self):
		with self.assertRaises(frappe.PermissionError):
			self._create(user=EXPIRED_SUP, woreda="ET04-W01")

	def test_forged_woreda_is_rejected(self):
		with self.assertRaises(frappe.PermissionError):
			self._create(user=SUP, woreda="ET03-W02")

	def test_forged_or_mismatched_region_and_zone_are_rejected(self):
		for kwargs in (
			{"region": "ET03"},
			{"zone": "ET03-Z02"},
			{"region": "ET03", "zone": "ET03-Z02"},
		):
			with self.assertRaises(frappe.ValidationError):
				self._create(user=SUP, woreda="ET04-W01", **kwargs)

	def test_unknown_or_wrong_level_area_is_rejected(self):
		for code in ("ET04-Z01", "ET99-W99"):
			with self.assertRaises(frappe.ValidationError):
				self._create(user=SUP, woreda=code)

	def test_admin_can_select_explicit_target_but_still_needs_a_valid_hierarchy(self):
		out = self._create(user=ADMIN, woreda="ET03-W02")
		self.assertEqual(
			(out["region"], out["zone"], out["woreda"]),
			("ET03", "ET03-Z02", "ET03-W02"),
		)
		with self.assertRaises(frappe.ValidationError):
			self._create(user=ADMIN, woreda="ET03-W02", region="ET04")
		with self.assertRaises(frappe.ValidationError):
			self._create(user=ADMIN, woreda="ET99-W99")
		with self.assertRaises(frappe.ValidationError):
			self._create(user=ADMIN, woreda="ET03-W02", zone="ET04-Z01")

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

	def test_individual_assignment_limits_da_access_and_keeps_one_marker(self):
		survey = self._create(
			assignmentMode="Selected",
			assignedDaIds=["DA-000002", "DA-000002"],
		)
		self.assertEqual(survey["assignmentMode"], "Selected")
		self.assertEqual(survey["assignedDaIds"], ["DA-000002"])
		markers = frappe.get_all(
			"DA Task",
			filters={"generated_by_rule": f"{svc.ASSIGNMENT_RULE_PREFIX}{survey['id']}"},
			fields=["da_id", "status"],
		)
		self.assertEqual([(m.da_id, m.status) for m in markers], [("DA-000002", "Open")])

		frappe.set_user(DA1)
		try:
			rows, _total = svc.list_surveys(_ctx(DA1))
			self.assertNotIn(survey["id"], [row["id"] for row in rows])
			with self.assertRaises(frappe.PermissionError):
				svc.get_survey(survey["id"], _ctx(DA1))
		finally:
			frappe.set_user("Administrator")

		frappe.set_user(DA2)
		try:
			rows, _total = svc.list_surveys(_ctx(DA2))
			self.assertIn(survey["id"], [row["id"] for row in rows])
			self.assertEqual(svc.get_survey(survey["id"], _ctx(DA2))["id"], survey["id"])
		finally:
			frappe.set_user("Administrator")

	def test_unassigned_da_cannot_submit_and_supervisor_cannot_collect_for_them(self):
		survey = self._create(assignmentMode="Selected", assignedDaIds=["DA-000002"])
		with self.assertRaises(frappe.PermissionError):
			self._submit(survey["id"], user=DA1)
		with self.assertRaises(frappe.PermissionError):
			self._submit(survey["id"], user=SUP, da_id="DA-000001")
		self.assertEqual(frappe.db.count("DA Survey Response"), 0)

	def test_assignment_can_switch_between_woreda_wide_and_selected(self):
		survey = self._create()
		self.assertEqual(survey["assignmentMode"], "Woreda")
		self.assertEqual(survey.get("assignedDaIds"), [])
		for user in (DA1, DA2):
			frappe.set_user(user)
			try:
				rows, _total = svc.list_surveys(_ctx(user))
				self.assertIn(survey["id"], [row["id"] for row in rows])
			finally:
				frappe.set_user("Administrator")

		updated = svc.patch_template(
			survey["id"],
			{"assignmentMode": "Selected", "assignedDaIds": ["DA-000002"]},
			ctx=_ctx(SUP),
		)
		self.assertEqual(updated["assignmentMode"], "Selected")
		frappe.set_user(DA1)
		try:
			rows, _total = svc.list_surveys(_ctx(DA1))
			self.assertNotIn(survey["id"], [row["id"] for row in rows])
		finally:
			frappe.set_user("Administrator")

		updated = svc.patch_template(survey["id"], {"assignmentMode": "Woreda"}, ctx=_ctx(SUP))
		self.assertEqual(updated["assignmentMode"], "Woreda")
		self.assertEqual(updated["assignedDaIds"], [])
		frappe.set_user(DA1)
		try:
			rows, _total = svc.list_surveys(_ctx(DA1))
			self.assertIn(survey["id"], [row["id"] for row in rows])
		finally:
			frappe.set_user("Administrator")

	def test_assignment_rejects_da_outside_survey_woreda(self):
		with self.assertRaises(frappe.PermissionError):
			self._create(assignmentMode="Selected", assignedDaIds=["DA-000004"])
		with self.assertRaises(frappe.ValidationError):
			self._create(
				user=ADMIN,
				woreda="ET04-W01",
				assignmentMode="Selected",
				assignedDaIds=["DA-000004"],
			)

	# -- template list: filters, derived status and pagination ----------------

	def test_list_templates_filters_before_pagination_and_reports_matching_total(self):
		open_t = self._create(name="Open season survey", close=add_days(today(), 20))
		soon = self._create(
			name="Closing soon survey",
			type="Service quality",
			close=add_days(today(), 3),
		)
		expired = self._create(
			name="Expired survey",
			type="Needs assessment",
			open=add_days(today(), -20),
			close=add_days(today(), -1),
		)
		stored_closed = self._create(name="Stored closed survey", close=add_days(today(), 20))
		svc.patch_template(stored_closed["id"], {"status": "Closed"}, ctx=_ctx(SUP))

		frappe.set_user(SUP)
		try:
			rows, total = svc.list_templates(_ctx(SUP), status="Closed", limit=1, start=0)
			self.assertEqual(total, 2)
			self.assertEqual(len(rows), 1)
			self.assertEqual(rows[0]["status"], "Closed")

			rows, total = svc.list_templates(_ctx(SUP), status="Closed", limit=1, start=1)
			self.assertEqual(total, 2)
			self.assertEqual(len(rows), 1)
			self.assertEqual(rows[0]["status"], "Closed")

			rows, total = svc.list_templates(_ctx(SUP), status="Closing soon")
			self.assertEqual(total, 1)
			self.assertEqual(rows[0]["id"], soon["id"])

			rows, total = svc.list_templates(_ctx(SUP), status="Open")
			self.assertEqual(total, 1)
			self.assertEqual(rows[0]["id"], open_t["id"])

			# Free-text search is applied before pagination and the total is the
			# count of the matched set, not the unfiltered table.
			rows, total = svc.list_templates(_ctx(SUP), q="expired", limit=1, start=0)
			self.assertEqual(total, 1)
			self.assertEqual(rows[0]["id"], expired["id"])

			rows, total = svc.list_templates(_ctx(SUP), limit=2, start=0)
			self.assertEqual(total, 4)
			self.assertEqual(len(rows), 2)
			rows, total = svc.list_templates(_ctx(SUP), limit=2, start=2)
			self.assertEqual(total, 4)
			self.assertEqual(len(rows), 2)
		finally:
			frappe.set_user("Administrator")

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

	def test_list_responses_filters_by_area_and_rejects_out_of_scope_selection(self):
		survey = self._create()
		self._submit(survey["id"], user=DA1, farmer_id=FARMER_A)
		self._submit(survey["id"], user=DA2, farmer_id=FARMER_D)

		rows, total = svc.list_responses(survey["id"], _ctx(SUP), woreda="ET04-W01")
		self.assertEqual(total, 2)
		self.assertEqual({row["collector_da_id"] for row in rows}, {"DA-000001", "DA-000002"})

		rows, total = svc.list_responses(survey["id"], _ctx(SUP), kebele="ET04-W01-K01")
		self.assertEqual(total, 2)
		rows, total = svc.list_responses(survey["id"], _ctx(SUP), kebele="ET04-W01-K03")
		self.assertEqual(total, 0)

		with self.assertRaises(frappe.PermissionError):
			svc.list_responses(survey["id"], _ctx(SUP), woreda="ET03-W02")
		with self.assertRaises(frappe.PermissionError):
			svc.list_responses(survey["id"], _ctx(DA1), kebele="ET04-W01-K03")

		rows, total = svc.list_responses(survey["id"], _ctx(DA1), kebele="ET04-W01-K01")
		self.assertEqual(total, 1)
		self.assertEqual(rows[0]["collector_da_id"], "DA-000001")
		rows, total = svc.list_responses(survey["id"], _ctx(ADMIN), woreda="ET03-W02")
		self.assertEqual(total, 0)

	def test_submit_stores_template_version_at_capture(self):
		survey = self._create()
		out = self._submit(survey["id"])

		self.assertEqual(out["template_version"], survey["templateVersion"])
		self.assertEqual(
			frappe.db.get_value("DA Survey Response", out["id"], "template_version"),
			survey["templateVersion"],
		)
		rows, _total = svc.list_responses(survey["id"], _ctx(SUP))
		self.assertEqual(rows[0]["template_version"], survey["templateVersion"])

	def test_historical_draft_keeps_capture_version_after_template_bump(self):
		survey = self._create()
		original = survey["templateVersion"]
		draft_id = self._insert_historical_draft(survey)

		updated = self._bump_questions(survey["id"])
		self.assertNotEqual(updated["templateVersion"], original)
		self.assertEqual(
			frappe.db.get_value("DA Survey Response", draft_id, "template_version"),
			original,
		)

	def test_declined_response_keeps_capture_version_after_template_bump(self):
		survey = self._create()
		original = survey["templateVersion"]
		out = self._submit(
			survey["id"],
			farmer_id=FARMER_A,
			consent="declined",
			answers={},
		)

		updated = self._bump_questions(survey["id"])
		self.assertNotEqual(updated["templateVersion"], original)
		self.assertEqual(out["template_version"], original)
		self.assertEqual(
			frappe.db.get_value("DA Survey Response", out["id"], "template_version"),
			original,
		)

	def test_completed_response_keeps_capture_version_when_template_version_changes(self):
		survey = self._create()
		original = survey["templateVersion"]
		out = self._submit(survey["id"])

		self._force_template_version(survey["id"])
		self.assertEqual(
			frappe.db.get_value("DA Survey Response", out["id"], "template_version"),
			original,
		)
		rows, _total = svc.list_responses(survey["id"], _ctx(SUP))
		self.assertEqual(rows[0]["template_version"], original)

	def test_idempotent_replay_keeps_original_response_version(self):
		survey = self._create()
		original = survey["templateVersion"]
		first = self._submit(survey["id"], client_op_id="resp-version-op")

		self._force_template_version(survey["id"])
		second = self._submit(survey["id"], client_op_id="resp-version-op")
		self.assertEqual(second["id"], first["id"])
		self.assertEqual(second["template_version"], original)
		self.assertEqual(
			frappe.db.get_value("DA Survey Response", first["id"], "template_version"),
			original,
		)

	def test_export_uses_stored_response_version_not_current_template_version(self):
		survey = self._create()
		original = survey["templateVersion"]
		self._submit(survey["id"])
		self._force_template_version(survey["id"])
		self.assertEqual(
			frappe.db.get_value("DA Survey Template", survey["id"], "template_version"),
			"FSS-T999-v9.9",
		)

		_filename, content = svc.export_responses(survey["id"], _ctx(SUP))
		workbook = load_workbook(filename=io.BytesIO(content))
		sheet = workbook.active
		headers = [cell.value for cell in sheet[1]]
		self.assertEqual(
			sheet.cell(row=2, column=headers.index("Template Version") + 1).value,
			original,
		)

	def test_legacy_response_without_version_exports_blank(self):
		survey = self._create()
		out = self._submit(survey["id"])
		frappe.db.set_value("DA Survey Response", out["id"], "template_version", None)
		frappe.clear_document_cache("DA Survey Response", out["id"])

		rows, _total = svc.list_responses(survey["id"], _ctx(SUP))
		self.assertIsNone(rows[0]["template_version"])

		_filename, content = svc.export_responses(survey["id"], _ctx(SUP))
		workbook = load_workbook(filename=io.BytesIO(content))
		sheet = workbook.active
		headers = [cell.value for cell in sheet[1]]
		self.assertIn(
			sheet.cell(row=2, column=headers.index("Template Version") + 1).value,
			(None, ""),
		)

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

	def test_draft_submission_is_rejected(self):
		survey = self._create()
		with self.assertRaises(frappe.ValidationError):
			self._submit(
				survey["id"],
				farmer_id=FARMER_A,
				consent=None,
				answers={},
				sync_state="Draft",
			)
		self.assertEqual(frappe.db.count("DA Survey Response"), 0)

	def test_historical_draft_does_not_reserve_the_farmer(self):
		survey = self._create()
		draft_id = self._insert_historical_draft(survey)
		synced = self._submit(survey["id"], farmer_id=FARMER_A)
		self.assertNotEqual(draft_id, synced["id"])
		self.assertEqual(frappe.db.count("DA Survey Response"), 2)
		rows, _total = svc.list_responses(survey["id"], _ctx(SUP))
		self.assertEqual({row["sync_state"] for row in rows}, {"Draft", "Synced"})

	def test_client_cannot_supply_server_only_sync_states(self):
		survey = self._create()
		for state in ("Draft", "Rejected", "Conflict"):
			with self.assertRaises(frappe.ValidationError):
				self._submit(survey["id"], sync_state=state)
		with self.assertRaises(frappe.ValidationError):
			self._submit(survey["id"], sync_state="Declined", consent="granted")
		with self.assertRaises(frappe.ValidationError):
			self._submit(survey["id"], sync_state="Declined", consent=None)
		with self.assertRaises(frappe.ValidationError):
			self._submit(survey["id"], sync_state="Synced", consent=None)
		with self.assertRaises(frappe.ValidationError):
			self._submit(survey["id"], sync_state="Queued", consent=None)
		self.assertEqual(frappe.db.count("DA Survey Response"), 0)

	def test_declined_consent_forces_declined_state(self):
		survey = self._create()
		for state in ("Synced", "Declined"):
			out = self._submit(
				survey["id"],
				farmer_id=FARMER_A,
				consent="declined",
				answers={},
				sync_state=state,
			)
			self.assertEqual(out["sync_state"], "Declined")
			self.assertIsNone(frappe.db.get_value("DA Survey Response", out["id"], "duplicate_guard_key"))
			# Declined rows do not reserve the farmer, so a later granted response is allowed.
			frappe.db.delete("DA Survey Response", {"name": out["id"]})

	def test_queued_response_reserves_the_farmer(self):
		survey = self._create()
		first = self._submit(survey["id"], sync_state="Queued")
		self.assertEqual(first["sync_state"], "Queued")
		with self.assertRaises(svc.SurveyConflictError):
			self._submit(survey["id"], sync_state="Synced")
		self.assertEqual(frappe.db.count("DA Survey Response"), 1)

	def test_rejected_or_conflict_attempts_never_bypass_the_guard(self):
		survey = self._create()
		for state in ("Rejected", "Conflict", "Declined"):
			with self.assertRaises(frappe.ValidationError):
				self._submit(survey["id"], farmer_id=FARMER_A, sync_state=state)
		self.assertEqual(frappe.db.count("DA Survey Response"), 0)
		out = self._submit(survey["id"], farmer_id=FARMER_A)
		self.assertEqual(out["sync_state"], "Synced")
		with self.assertRaises(svc.SurveyConflictError):
			self._submit(survey["id"], farmer_id=FARMER_A)
		self.assertEqual(frappe.db.count("DA Survey Response"), 1)

	# -- collection window -----------------------------------------------------

	def test_response_before_window_open_is_rejected(self):
		survey = self._create(open=add_days(today(), 1), close=add_days(today(), 10))
		with self.assertRaises(frappe.ValidationError):
			self._submit(survey["id"])
		self.assertEqual(frappe.db.count("DA Survey Response"), 0)

	def test_response_on_opening_boundary_is_allowed(self):
		survey = self._create(open=today(), close=add_days(today(), 10))
		out = self._submit(survey["id"])
		self.assertEqual(out["survey_id"], survey["id"])

	def test_response_in_active_window_is_allowed(self):
		survey = self._create(open=add_days(today(), -1), close=add_days(today(), 1))
		out = self._submit(survey["id"])
		self.assertEqual(out["survey_id"], survey["id"])

	def test_response_after_close_is_rejected(self):
		survey = self._create(open=add_days(today(), -10), close=add_days(today(), -1))
		with self.assertRaises(frappe.ValidationError):
			self._submit(survey["id"])
		self.assertEqual(frappe.db.count("DA Survey Response"), 0)

	# -- response timestamps ---------------------------------------------------

	def test_timestamps_must_be_ordered_and_inside_the_window(self):
		survey = self._create(open=add_days(today(), -1), close=add_days(today(), 2))
		now = now_datetime()
		bad = (
			{"started_at": str(now), "completed_at": str(now - timedelta(hours=1))},
			{"started_at": str(now - timedelta(days=2))},
			{"started_at": str(now + timedelta(days=1))},
			{"completed_at": str(now + timedelta(minutes=1))},
			{"started_at": "not-a-datetime"},
		)
		for payload in bad:
			with self.assertRaises(frappe.ValidationError):
				self._submit(survey["id"], **payload)
		self.assertEqual(frappe.db.count("DA Survey Response"), 0)

		out = self._submit(
			survey["id"],
			started_at=str(now - timedelta(minutes=5)),
			completed_at=str(now),
		)
		self.assertTrue(out["id"])

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

	def test_response_client_op_id_concurrent_replay_returns_existing(self):
		survey = self._create()
		first = self._submit(survey["id"], client_op_id="resp-op-race")
		real_get_value = frappe.db.get_value
		real_exists = frappe.db.exists
		pending = {"precheck": True}

		def flaky_get_value(doctype, filters=None, *args, **kwargs):
			if (
				doctype == "DA Survey Response"
				and isinstance(filters, dict)
				and filters.get("client_op_id") == "resp-op-race"
				and pending["precheck"]
			):
				pending["precheck"] = False
				return None
			return real_get_value(doctype, filters, *args, **kwargs)

		def racy_exists(doctype, filters=None, *args, **kwargs):
			# Simulate the competing request not being visible to the pre-checks.
			if (
				doctype == "DA Survey Response"
				and isinstance(filters, dict)
				and (filters.get("client_op_id") or filters.get("duplicate_guard_key"))
			):
				return False
			return real_exists(doctype, filters, *args, **kwargs)

		with (
			patch.object(frappe.db, "get_value", side_effect=flaky_get_value),
			patch.object(frappe.db, "exists", side_effect=racy_exists),
			_keep_fixtures(),
		):
			second = self._submit(survey["id"], client_op_id="resp-op-race")
		self.assertEqual(second["id"], first["id"])
		self.assertEqual(frappe.db.count("DA Survey Response"), 1)

	def test_response_client_op_id_concurrent_payload_mismatch_is_conflict(self):
		survey = self._create()
		self._submit(survey["id"], client_op_id="resp-op-race-diff")
		real_get_value = frappe.db.get_value
		real_exists = frappe.db.exists
		pending = {"precheck": True}

		def flaky_get_value(doctype, filters=None, *args, **kwargs):
			if (
				doctype == "DA Survey Response"
				and isinstance(filters, dict)
				and filters.get("client_op_id") == "resp-op-race-diff"
				and pending["precheck"]
			):
				pending["precheck"] = False
				return None
			return real_get_value(doctype, filters, *args, **kwargs)

		def racy_exists(doctype, filters=None, *args, **kwargs):
			if (
				doctype == "DA Survey Response"
				and isinstance(filters, dict)
				and (filters.get("client_op_id") or filters.get("duplicate_guard_key"))
			):
				return False
			return real_exists(doctype, filters, *args, **kwargs)

		with (
			patch.object(frappe.db, "get_value", side_effect=flaky_get_value),
			patch.object(frappe.db, "exists", side_effect=racy_exists),
			_keep_fixtures(),
		):
			with self.assertRaises(svc.SurveyConflictError) as caught:
				self._submit(
					survey["id"],
					client_op_id="resp-op-race-diff",
					answers={"p4": "2", "p5": "No"},
				)
		self.assertEqual(caught.exception.http_status_code, 409)
		self.assertEqual(frappe.db.count("DA Survey Response"), 1)

	# -- dual role: DA + live officer -----------------------------------------

	def test_dual_role_list_and_export_use_live_officer_scope(self):
		survey = self._create()
		self._submit(survey["id"], user=DA1, farmer_id=FARMER_A)
		self._submit(survey["id"], user=DA2, farmer_id=FARMER_D)

		frappe.set_user(DUAL)
		try:
			rows, total = svc.list_responses(survey["id"], _ctx(DUAL))
			self.assertEqual(total, 2)
			self.assertEqual(
				{row["collector_da_id"] for row in rows},
				{"DA-000001", "DA-000002"},
			)
		finally:
			frappe.set_user("Administrator")

		filename, content = svc.export_responses(survey["id"], _ctx(DUAL))
		self.assertTrue(filename.endswith(".xlsx"))
		workbook = load_workbook(filename=io.BytesIO(content))
		self.assertEqual(workbook.active.max_row, 3)

	def test_dual_role_without_live_officer_falls_back_to_da_and_cannot_export(self):
		survey = self._create()
		self._submit(survey["id"], user=DA1, farmer_id=FARMER_A)
		self._submit(survey["id"], user=DA2, farmer_id=FARMER_D)

		grant = frappe.db.get_value(
			"DA RBAC Assignment",
			{"user": DUAL, "role": C.ROLE_SUPERVISOR},
			"name",
		)
		frappe.db.set_value("DA RBAC Assignment", grant, "active", 0)

		frappe.set_user(DUAL)
		try:
			rows, total = svc.list_responses(survey["id"], _ctx(DUAL))
			self.assertEqual(total, 1)
			self.assertEqual(rows[0]["collector_da_id"], "DA-000001")
			with self.assertRaises(frappe.PermissionError):
				svc.export_responses(survey["id"], _ctx(DUAL))
			# The stale officer role must not turn the active DA assignment into
			# officer-level results access.
			with self.assertRaises(frappe.PermissionError):
				svc.survey_results(survey["id"], _ctx(DUAL))
		finally:
			frappe.set_user("Administrator")

	def test_dual_role_officer_scope_does_not_union_da_scope_for_results(self):
		da_survey = self._create(name="DA Woreda survey")
		self._submit(da_survey["id"], user=DA1, farmer_id=FARMER_A)
		officer_survey = self._create(
			user=SUP_OTHER,
			name="Officer Woreda survey",
			type="Needs assessment",
		)

		# Move the live officer grant to ET03-W02 while the DA grant stays ET04-W01.
		grant = frappe.db.get_value(
			"DA RBAC Assignment",
			{"user": DUAL, "role": C.ROLE_SUPERVISOR},
			"name",
		)
		frappe.db.set_value("DA RBAC Assignment", grant, "region_scope", "ET03")
		frappe.db.set_value("DA RBAC Assignment", grant, "woreda_scope", "ET03-W02")

		frappe.set_user(DUAL)
		try:
			# DA scope alone must not authorize results in the old Woreda.
			with self.assertRaises(frappe.PermissionError):
				svc.survey_results(da_survey["id"], _ctx(DUAL))
			# The live officer assignment still authorizes its own Woreda.
			out = svc.survey_results(officer_survey["id"], _ctx(DUAL))
			self.assertEqual(out["survey_id"], officer_survey["id"])
			self.assertEqual(out["collected"], 0)
		finally:
			frappe.set_user("Administrator")

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
		self.assertEqual(sheet.max_row, 2)
		row = [cell.value for cell in sheet[2]]
		self.assertEqual(row[headers.index("Farmer ID")], FARMER_A)
		self.assertEqual(row[headers.index("Farmer Registry ID")], "FR-000101")
		self.assertEqual(row[headers.index("Collector DA ID")], "DA-000001")
		self.assertEqual(row[headers.index("Kebele")], "Bako 01")

	def test_export_empty_survey_is_headers_only(self):
		survey = self._create()
		_filename, content = svc.export_responses(survey["id"], _ctx(SUP))
		workbook = load_workbook(filename=io.BytesIO(content))
		self.assertEqual(workbook.active.max_row, 1)

	def test_export_includes_historical_draft_rows(self):
		survey = self._create()
		self._insert_historical_draft(survey)
		self._submit(survey["id"], farmer_id=FARMER_D)

		_filename, content = svc.export_responses(survey["id"], _ctx(SUP))
		workbook = load_workbook(filename=io.BytesIO(content))
		sheet = workbook.active
		headers = [cell.value for cell in sheet[1]]
		states = [sheet.cell(row=r, column=headers.index("Sync State") + 1).value for r in (2, 3)]
		self.assertEqual(sorted(states), ["Draft", "Synced"])

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
			self.assertEqual(frappe.response.get("content_type"), api.XLSX_MIME)
			self.assertEqual(
				frappe.response.get("filename"),
				f"survey-{survey['id']}-responses.xlsx",
			)
			self.assertTrue(frappe.response.get("filecontent"))
		finally:
			frappe.set_user("Administrator")
			frappe.response.clear()

	def test_export_route_returns_real_xlsx_over_http(self):
		import frappe.api
		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		from da_services.api.router import ensure_routes_registered

		ensure_routes_registered()
		survey = self._create()
		self._submit(survey["id"], farmer_id=FARMER_A)

		path = f"/api/v1/da-services/surveys/{survey['id']}/responses/export"
		req = Request(
			EnvironBuilder(path=path, method="GET", base_url="http://testsite.localhost").get_environ()
		)
		frappe.set_user(SUP)
		frappe.local.request = req
		frappe.local.form_dict = frappe._dict({})
		frappe.local.response = frappe._dict({})
		try:
			res = frappe.api.handle(req)
		finally:
			frappe.set_user("Administrator")
			frappe.local.response = frappe._dict({})

		self.assertEqual(res.status_code, 200)
		self.assertEqual(res.mimetype, api.XLSX_MIME)
		disposition = res.headers.get("Content-Disposition", "")
		self.assertIn("attachment", disposition)
		self.assertIn(f"survey-{survey['id']}-responses.xlsx", disposition)

		content = res.get_data()
		self.assertTrue(content)
		workbook = load_workbook(filename=io.BytesIO(content))
		self.assertEqual(workbook.active.max_row, 2)

	def test_executive_can_export_responses(self):
		survey = self._create()
		self._submit(survey["id"], farmer_id=FARMER_A)
		filename, content = svc.export_responses(survey["id"], _ctx(EXEC))
		self.assertTrue(filename.endswith(".xlsx"))
		self.assertIsInstance(content, bytes)

def test_patch_questions_creates_new_template_version(self):
	survey = self._create()
	self._submit(survey["id"], farmer_id=FARMER_A)
	original_version = survey["templateVersion"]

	# Patch questions after responses exist creates a new template version,
	# not a 409 error. The new version has a different template ID.
	out = api.patch_template(
	survey["id"],
		questions=[{"prompt": "New Q", "type": "text", "options": "", "required": False}],
	)
	# Editing questions after responses exist creates a new template version,
# not a 409 error. The new version has a different template ID.
	self.assertNotEqual(out["data"]["templateVersion"], original_version)
	self.assertIn(out["status"], ("success", "Open"))

	# New version has a different template version and can be edited independently.
	new_version_doc = frappe.get_doc("DA Survey Template", out["id"])
	self.assertIsNotNone(new_version_doc)
	self.assertNotEqual(new_version_doc.template_version, original_version)

	# Original template remains unchanged at the original version.
	self.assertEqual(survey["templateVersion"], original_version)
	def test_api_rejects_client_supplied_invalid_sync_state(self):
		survey = self._create()
		frappe.set_user(DA1)
		frappe.response.clear()
		try:
			with _keep_fixtures():
				out = api.submit_response(
					survey["id"],
					farmer_id=FARMER_A,
					consent="granted",
					answers={"p4": "4", "p5": "Yes, fully"},
					sync_state="Rejected",
				)
			self.assertEqual(out["status"], "error")
			self.assertEqual(frappe.response.get("http_status_code"), 400)
		finally:
			frappe.set_user("Administrator")
			frappe.response.clear()

	def test_api_list_templates_reports_filtered_total(self):
		closed = self._create(name="Stored closed survey")
		svc.patch_template(closed["id"], {"status": "Closed"}, ctx=_ctx(SUP))
		self._create(name="Open survey")
		self._create(name="Expired survey", open=add_days(today(), -20), close=add_days(today(), -1))

		frappe.set_user(SUP)
		try:
			out = api.list_templates(status="Closed", limit=1, start=0)
			self.assertEqual(out["status"], "success")
			self.assertEqual(out["meta"]["total"], 2)
			self.assertEqual(len(out["data"]), 1)
			self.assertEqual(out["data"][0]["status"], "Closed")
		finally:
			frappe.set_user("Administrator")
