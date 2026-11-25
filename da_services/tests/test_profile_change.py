"""DA profile change: request → Woreda Supervisor (level 1) → OAN Administrator (level 2) → Registry.

The mock registry records every change request it is given (``pushed``) together with
the user whose request sent it, so the tests can prove that nothing reaches the registry
before the second approval and that only an Administrator's request writes to it.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.api.v1 import profile_self as api
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import constants as C
from da_services.services import profile as svc
from da_services.services import profile_fields
from da_services.tests.fixtures import as_user, ensure_doctype_schema, ensure_geo_masters, set_da_registry_id


def _pdf() -> bytes:
	"""A real one-page PDF: Frappe opens a PDF when it stores it."""
	import io

	from pypdf import PdfWriter

	writer, out = PdfWriter(), io.BytesIO()
	writer.add_blank_page(width=72, height=72)
	writer.write(out)
	return out.getvalue()


PDF = _pdf()
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

DA1 = "pcr-da1@da.local"  # DA-000001, ET04 / ET04-Z01 / ET04-W01, Kebele ET04-W01-K03
DA3 = "pcr-da3@da.local"  # DA-000004, ET03 / ET03-W02
SUP = "pcr-sup@da.local"  # Supervisor ET04-W01
SUP2 = "pcr-sup2@da.local"  # second Supervisor of ET04-W01
SUP_OTHER = "pcr-sup3@da.local"  # Supervisor ET03-W02
EXEC = "pcr-exec@da.local"  # Executive ET04
ADMIN1 = "pcr-admin1@da.local"
ADMIN2 = "pcr-admin2@da.local"
SUP_ADMIN = "pcr-supadmin@da.local"  # Supervisor of ET04-W01 who is also an OAN Administrator
DUAL = "pcr-dual@da.local"  # Supervisor of ET04-W01 who also holds the Development Agent role
ROLE_ONLY = "pcr-roleonly@da.local"  # DA of ET04-W01 with the Supervisor role but no Supervisor grant
TEST_USERS = (DA1, DA3, SUP, SUP2, SUP_OTHER, EXEC, ADMIN1, ADMIN2, SUP_ADMIN, DUAL, ROLE_ONLY)
TEST_DAS = ("DA-000001", "DA-000004")  # mock registry fixtures
PCR = "DA Profile Change Request"


def _clean():
	"""Remove only what these tests create: never empty a table (a dev site holds real grants)."""
	requests = frappe.get_all(PCR, filters={"da_id": ("in", TEST_DAS)}, pluck="name")
	if requests:
		frappe.db.delete("DA Notification", {"reference_doctype": PCR, "reference_name": ("in", requests)})
		for name in frappe.get_all(
			"File", filters={"attached_to_doctype": PCR, "attached_to_name": ("in", requests)}, pluck="name"
		):
			frappe.delete_doc("File", name, ignore_permissions=True, force=True)  # the file on disk too
	frappe.db.delete("DA Certificate", {"da_id": ("in", TEST_DAS)})
	frappe.db.delete("DA Notification", {"user": ("in", TEST_USERS)})
	frappe.db.delete(PCR, {"da_id": ("in", TEST_DAS)})
	frappe.db.delete("DA RBAC Assignment", {"user": ("in", TEST_USERS)})


CONTACT = {"mobile": "+251911999888", "email": "abebe@moa.gov.et"}


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


def json_ids(names):
	"""ids as a form post sends them: a JSON string."""
	return frappe.as_json(names)


def _grant(user, role, **kw):
	doc = frappe.new_doc("DA RBAC Assignment")
	doc.user = user
	doc.role = role
	doc.effective_from = today()
	for k, v in kw.items():
		setattr(doc, k, v)
	doc.insert(ignore_permissions=True, ignore_links=True)
	return doc.name


class TestProfileChangeRequest(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		ensure_doctype_schema("DA Profile Change Request")
		others = frappe.get_all(
			"DA RBAC Assignment",
			filters={"da_id": ("in", TEST_DAS), "user": ("not in", TEST_USERS), "active": 1},
			pluck="user",
		)
		if [u for u in others if not str(u).endswith("@da.local")]:
			# A transfer moves every grant of the DA-ID and the DA is notified by DA-ID:
			# with a real user bound to a fixture DA these tests would touch their rows.
			# (Users @da.local are other test files' own.)
			self.skipTest(f"Fixture DA-IDs {TEST_DAS} are bound to other users here; run on a test site.")
		_clean()
		MockDARegistryClient.reset()
		_user(DA1, [C.ROLE_DA])
		_user(DA3, [C.ROLE_DA])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_user(SUP2, [C.ROLE_SUPERVISOR])
		_user(SUP_OTHER, [C.ROLE_SUPERVISOR])
		_user(EXEC, [C.ROLE_EXECUTIVE])
		_user(ADMIN1, [C.ROLE_ADMIN])
		_user(ADMIN2, [C.ROLE_ADMIN])
		_user(SUP_ADMIN, [C.ROLE_SUPERVISOR, C.ROLE_ADMIN])
		_grant(
			DA1,
			C.ROLE_DA,
			da_id="DA-000001",
			region_scope="ET04",
			zone_scope="ET04-Z01",
			woreda_scope="ET04-W01",
		)
		_grant(DA3, C.ROLE_DA, da_id="DA-000004", region_scope="ET03", woreda_scope="ET03-W02")
		# A DA grant takes its DA-ID from the User, and a scope names DA Region / Zone /
		# Woreda masters: the transfer creates a grant the normal way and needs both.
		ensure_geo_masters()
		set_da_registry_id(DA1, "DA-000001")
		set_da_registry_id(DA3, "DA-000004")
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		self.sup_other_grant = _grant(
			SUP_OTHER, C.ROLE_SUPERVISOR, region_scope="ET03", woreda_scope="ET03-W02"
		)
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope="ET04")
		# No role skips scope: the administrators reach the test Regions through grants.
		for admin in (ADMIN1, ADMIN2):
			for region in ("ET03", "ET04"):
				_grant(admin, C.ROLE_ADMIN, region_scope=region)
		_grant(SUP_ADMIN, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		frappe.local.request = None
		self.writers = []  # (session user, ProfileUpdate) of every registry write
		original = MockDARegistryClient.push_profile_update

		def recording_push(client, update):
			self.writers.append((frappe.session.user, update))
			return original(client, update)

		patcher = patch.object(MockDARegistryClient, "push_profile_update", recording_push)
		patcher.start()
		self.addCleanup(patcher.stop)

	def tearDown(self):
		frappe.set_user("Administrator")
		_clean()
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		MockDARegistryClient.reset()

	# -- helpers --------------------------------------------------------------------------

	def _decide(self, user, name, decision="Approve", note=None):
		frappe.set_user(user)
		return api.decide(name, decision=decision, note=note)

	def _notified(self, user, event=None):
		filters = {"user": user}
		if event:
			filters["event"] = event
		return frappe.get_all("DA Notification", filters=filters, pluck="title")

	def _error(self, out, status_code):
		self.assertEqual(out["status"], "error", out)
		self.assertEqual(frappe.response.get("http_status_code"), status_code, out)

	# -- view -----------------------------------------------------------------------------

	def test_a_supervisor_sees_a_da_of_their_woreda(self):
		frappe.set_user(SUP)
		d = api.agent_profile("DA-000001")["data"]
		self.assertEqual(d["profile"]["da_id"], "DA-000001")
		self.assertTrue(d["can_request"])

	# -- submit ---------------------------------------------------------------------------

	def test_submit_validation(self):
		frappe.set_user(DA1)
		frappe.db.commit()  # nosemgrep: error calls below roll back uncommitted rows
		cases = [
			{"changes": {"salary": "1"}},  # not a profile field
			{"changes": {"language": "am"}},  # not in the registry
			{"changes": {}},
			{"changes": {"email": "not-an-email"}},
			{"changes": {"home_address": "x" * 101}},  # the registry takes 100 characters
			{"changes": {"home_address": "House 12\nKebele 03"}},  # one line only
			{"changes": {"mobile": "12345"}},
			{"changes": {"education_tier": "PhD"}},
			{"changes": {"mobile": "+251911999888", "specialisation": "Livestock"}},  # two sections
			{"changes": {"mobile": "+251911000001"}},  # same as current
			{"changes": {"fayda_id": "123456789012"}, "reason": "Typo on the card"},  # 12 digits
			{"changes": {"fayda_id": "1234567890123456"}, "reason": "typo"},  # note too short
			{"changes": {"first_name": "Abebe2"}, "reason": "Spelling on the Fayda card"},
			{"changes": {"birth_month_ec": "14"}},
			{"changes": {"joined_date": "2999-01-01"}},
			{"changes": {"joined_date": "01/09/2019"}},
			{"changes": {"gender": "Unknown"}},
			{"changes": {"kebele": "ET04-W01-K05"}},  # location needs all four levels
			{
				"changes": {
					"region": "ET04",
					"zone": "ET04-Z01",
					"woreda": "ET04-W01",
					"kebele": "ET03-W02-K01",
				}
			},  # Kebele outside the Woreda
		]
		for body in cases:
			out = api.submit(**body)
			self._error(out, 400)
		self.assertEqual(frappe.db.count(PCR, {"da_id": ("in", TEST_DAS)}), 0)

	def test_an_administrator_cannot_send_a_request(self):
		frappe.set_user(ADMIN1)
		self._error(api.submit(changes=dict(CONTACT), da_id="DA-000001"), 403)

	# -- two levels -------------------------------------------------------------------------

	def test_nobody_decides_their_own_request(self):
		frappe.set_user(SUP)
		out = api.submit(changes={"specialisation": "Livestock"}, reason="Retrained", da_id="DA-000001")
		r = out["data"]
		frappe.db.commit()  # nosemgrep
		self._error(self._decide(SUP, r["name"]), 403)

	def test_a_supervisor_requests_a_change_for_a_da_of_their_woreda(self):
		frappe.set_user(SUP)
		out = api.submit(changes={"position": "SENIOR_DA"}, reason="Promoted", da_id="DA-000001")
		self.assertEqual(out["status"], "success", out)
		r = out["data"]
		self.assertEqual(
			(r["status"], r["initiated_by_role"], r["l1_by"], r["requested_by"]),
			("Pending Administrator", C.ROLE_SUPERVISOR, SUP, SUP),
		)
		self.assertIn("Administrator", out["message"])
		self.assertEqual(self.writers, [])
		self.assertTrue(any("Supervisor asked" in t for t in self._notified(DA1)))
		frappe.set_user(DA1)
		frappe.db.commit()  # nosemgrep
		self._error(api.cancel(r["name"]), 403)  # not the DA's to withdraw
		d = self._decide(ADMIN1, r["name"])["data"]
		self.assertEqual(d["status"], "Applied")
		self.assertEqual([w[0] for w in self.writers], [ADMIN1])
		self.assertEqual(MockDARegistryClient.shared().get_da("DA-000001").position, "Senior DA")

	def test_a_supervisor_cannot_request_outside_their_woreda(self):
		frappe.set_user(SUP)
		self._error(api.submit(changes={"mobile": "+251911999777"}, da_id="DA-000004"), 403)
		self._error(api.submit(changes={"mobile": "+251911999777"}), 400)  # no DA named

	# -- the registry's answer ---------------------------------------------------------------

	def test_bulk_decide_is_routed(self):
		from da_services.api.router import ensure_routes_registered

		ensure_routes_registered()
		adapter = frappe.api.API_URL_MAP.bind("localhost")
		endpoint, _args = adapter.match(
			"/api/v1/da-services/profile/change-requests/bulk-decide", method="POST"
		)
		self.assertIn("bulk_decide", str(endpoint))

	# -- lists, certificates, photo (DA Registry #66) ------------------------------------------

	def _refused(self, code_or_exc, call):
		with self.assertRaises(code_or_exc):
			call()
		frappe.db.rollback()

	def test_who_may_ask_for_list_and_file_changes(self):
		with as_user(SUP_OTHER):
			self._refused(
				frappe.PermissionError,
				lambda: svc.submit_photo(filename="me.png", content=PNG, da_id="DA-000001"),
			)
		# Who may ask is DA Profile Change Request `create`; an Executive has none.
		with as_user(EXEC):
			self._refused(
				frappe.PermissionError,
				lambda: svc.submit_family_change(
					action="add", values={"name": "A B", "relationship": "REL_SON"}
				),
			)
		with as_user(ADMIN1):
			self._refused(
				frappe.PermissionError,
				lambda: svc.submit_photo(filename="me.png", content=PNG, da_id="DA-000001"),
			)
		with as_user(SUP):
			r = svc.submit_certificate(
				record_id="qual-000001-1",
				filename="a.pdf",
				content=PDF,
				title="A",
				da_id="DA-000001",
			)
		self.assertEqual((r["status"], r["initiated_by_role"]), ("Pending Administrator", C.ROLE_SUPERVISOR))
		with as_user(EXEC):
			self.assertEqual(len(svc.list_certificates("DA-000001")), 2)

	def test_coded_fields_carry_their_options_and_the_current_code(self):
		frappe.set_user(SUP)
		d = api.agent_profile("DA-000001")["data"]
		spec = d["fields"]["specialisation"]
		self.assertIn({"code": "LIVESTOCK", "label": "Livestock"}, spec["options"])
		self.assertEqual(len(spec["options"]), 3)
		# the DA's own value by code, so the screen can mark it in the list
		self.assertEqual((spec["current"], spec["current_label"]), ("CROP_PRODUCTION", "Crop production"))
		# the education level is read only: it follows the main education record
		self.assertNotIn("education_tier", d["fields"])
		self.assertEqual((d["profile"]["education_tier"], d["profile"]["passout_year"]), ("Diploma", 2011))
		self.assertNotIn("options", d["fields"]["mobile"])  # free text, checked by format
		self.assertEqual(d["fields"]["mobile"]["current"], "+251911000001")
		self.assertEqual(d["fields"]["woreda"]["current"], "ET04-W01")
		# where the profile shows the field under another name
		keys = {name: f["profile_key"] for name, f in d["fields"].items()}
		self.assertEqual(
			(keys["telegram"], keys["alternate_mobile"], keys["joined_date"], keys["prefix"], keys["mobile"]),
			("telegram_number", "alternate_number", "joined_at", "salutation", "mobile"),
		)
		for name, key in keys.items():
			self.assertIn(key, d["profile"], name)

	def test_an_executive_reads_a_da_of_their_region_and_cannot_request(self):
		frappe.set_user(EXEC)
		out = api.agent_profile("DA-000001")
		self.assertEqual(out["status"], "success", out)
		self.assertFalse(out["data"]["can_request"])
		frappe.db.commit()  # nosemgrep
		self._error(api.submit(changes=dict(CONTACT), reason="New SIM", da_id="DA-000001"), 403)
		frappe.set_user(ADMIN1)
		self.assertFalse(api.agent_profile("DA-000001")["data"]["can_request"])  # approves, never asks

	def test_ethiopian_year(self):
		from datetime import date

		cases = {
			date(2026, 9, 10): 2018,
			date(2026, 9, 11): 2019,  # Enkutatash
			date(2026, 11, 12): 2019,
			date(2027, 1, 1): 2019,
			date(2023, 9, 11): 2015,  # 2024 is a leap year: the new year falls on 12 September
			date(2023, 9, 12): 2016,
		}
		for day, year in cases.items():
			self.assertEqual(profile_fields.ethiopian_year(day), year, day)


class TestRegistryReadBackWithUserTokens(IntegrationTestCase):
	def test_the_scheduled_read_back_is_skipped_without_a_service_identity(self):
		saved = frappe.conf.get("da_registry_auth")
		frappe.conf.da_registry_auth = "user"
		self.addCleanup(frappe.conf.__setitem__, "da_registry_auth", saved)
		self.assertEqual(svc.sync_registry_approvals(), 0)
