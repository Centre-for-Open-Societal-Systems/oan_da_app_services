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
from da_services.integrations.da_registry.client import DARegistryUnavailable
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.integrations.da_registry.schemas import IntegrationResult
from da_services.services import constants as C
from da_services.services import profile as svc
from da_services.services import profile_fields
from da_services.tests.fixtures import ensure_doctype_schema
from da_services.utils.permissions import get_scope_context

DA1 = "pcr-da1@da.local"  # DA-000001, ET04 / ET04-Z01 / ET04-W01, Kebele ET04-W01-K03
DA3 = "pcr-da3@da.local"  # DA-000004, ET03 / ET03-W02
SUP = "pcr-sup@da.local"  # Supervisor ET04-W01
SUP2 = "pcr-sup2@da.local"  # second Supervisor of ET04-W01
SUP_OTHER = "pcr-sup3@da.local"  # Supervisor ET03-W02
EXEC = "pcr-exec@da.local"  # Executive ET04
ADMIN1 = "pcr-admin1@da.local"
ADMIN2 = "pcr-admin2@da.local"
SUP_ADMIN = "pcr-supadmin@da.local"  # Supervisor of ET04-W01 who is also an OAN Administrator
TEST_USERS = (DA1, DA3, SUP, SUP2, SUP_OTHER, EXEC, ADMIN1, ADMIN2, SUP_ADMIN)
TEST_DAS = ("DA-000001", "DA-000004")  # mock registry fixtures
PCR = "DA Profile Change Request"


def _clean():
	"""Remove only what these tests create: never empty a table (a dev site holds real grants)."""
	requests = frappe.get_all(PCR, filters={"da_id": ("in", TEST_DAS)}, pluck="name")
	if requests:
		frappe.db.delete("DA Notification", {"reference_doctype": PCR, "reference_name": ("in", requests)})
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
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		self.sup_other_grant = _grant(
			SUP_OTHER, C.ROLE_SUPERVISOR, region_scope="ET03", woreda_scope="ET03-W02"
		)
		_grant(EXEC, C.ROLE_EXECUTIVE, region_scope="ET04")
		_grant(ADMIN1, C.ROLE_ADMIN)
		_grant(ADMIN2, C.ROLE_ADMIN)
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

	def _submit(self, user=DA1, **kw):
		frappe.set_user(user)
		body = {"changes": dict(CONTACT), "reason": "New SIM"}
		body.update(kw)
		return api.submit(**body)

	def _decide(self, user, name, decision="Approve", note=None):
		frappe.set_user(user)
		return api.decide(name, decision=decision, note=note)

	def _approve_both(self, name, level1=SUP, level2=ADMIN1):
		out = self._decide(level1, name)
		self.assertEqual(out["status"], "success", out)
		self.assertEqual(out["data"]["status"], "Pending Administrator", out)
		return self._decide(level2, name)

	def _row(self, name):
		return frappe.get_doc("DA Profile Change Request", name)

	def _notified(self, user, event=None):
		filters = {"user": user}
		if event:
			filters["event"] = event
		return frappe.get_all("DA Notification", filters=filters, pluck="title")

	def _error(self, out, status_code):
		self.assertEqual(out["status"], "error", out)
		self.assertEqual(frappe.response.get("http_status_code"), status_code, out)

	# -- view -----------------------------------------------------------------------------

	def test_my_profile_shows_registry_values_and_every_section(self):
		frappe.set_user(DA1)
		out = api.my_profile()
		self.assertEqual(out["status"], "success", out)
		d = out["data"]
		self.assertEqual((d["profile"]["da_id"], d["profile"]["mobile"]), ("DA-000001", "+251911000001"))
		for field in ("mobile", "position", "joined_date", "fayda_id", "first_name", "kebele"):
			self.assertIn(field, d["editable_fields"])
		self.assertNotIn("language", d["editable_fields"])  # the registry does not hold it
		self.assertTrue(d["fields"]["fayda_id"]["note_required"])
		self.assertEqual(d["fields"]["kebele"]["section"], "Location")
		self.assertEqual(d["pending_requests"], [])

	def test_a_supervisor_sees_a_da_of_their_woreda_only(self):
		frappe.set_user(SUP)
		d = api.agent_profile("DA-000001")["data"]
		self.assertEqual(d["profile"]["da_id"], "DA-000001")
		self.assertTrue(d["can_request"])
		self._error(api.agent_profile("DA-000004"), 403)

	# -- submit ---------------------------------------------------------------------------

	def test_submit_records_diff_scope_and_is_idempotent(self):
		out = self._submit(client_op_id="pcr-op-1")
		self.assertEqual(out["status"], "success", out)
		r = out["data"]
		self.assertEqual(
			(r["status"], r["section"], r["da_id"], r["woreda"], r["initiated_by_role"]),
			("Pending Supervisor", "Contact", "DA-000001", "ET04-W01", C.ROLE_DA),
		)
		self.assertEqual(r["status_label"], "Waiting for the Supervisor")
		self.assertEqual(r["changes"]["mobile"], {"from": "+251911000001", "to": "+251911999888"})
		self.assertEqual(r["requested_by"], DA1)
		self.assertFalse(r["no_supervisor_found"])
		again = self._submit(client_op_id="pcr-op-1")
		self.assertEqual(again["data"]["name"], r["name"])
		self.assertEqual(frappe.db.count(PCR, {"da_id": ("in", TEST_DAS)}), 1)
		frappe.set_user(DA1)
		d = api.my_profile()["data"]
		self.assertEqual([p["name"] for p in d["pending_requests"]], [r["name"]])
		self.assertEqual(d["profile"]["mobile"], "+251911000001")  # registry unchanged
		self.assertEqual(self.writers, [])

	def test_every_supervisor_of_the_woreda_is_told(self):
		_grant(SUP2, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		self._submit()
		for user in (SUP, SUP2, SUP_ADMIN):
			self.assertEqual(len(self._notified(user, "profile_change_waiting")), 1, user)
		self.assertEqual(self._notified(SUP_OTHER), [])

	def test_a_woreda_without_supervisor_is_flagged(self):
		# Every Supervisor grant on the Woreda, not only this file's: another test file or a
		# local sign-in user may hold one too. They are switched back on afterwards.
		grants = frappe.get_all(
			"DA RBAC Assignment",
			filters={"role": C.ROLE_SUPERVISOR, "woreda_scope": "ET03-W02", "active": 1},
			fields=["name", "user"],
		)
		if [g for g in grants if not str(g.user).endswith("@da.local")]:
			self.skipTest("A real user supervises ET03-W02 here; run on a test site.")
		others = [g.name for g in grants if g.name != self.sup_other_grant]

		def restore():
			for name in others:
				frappe.db.set_value("DA RBAC Assignment", name, "active", 1)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

		self.addCleanup(restore)
		for g in grants:
			frappe.db.set_value("DA RBAC Assignment", g.name, "active", 0)
		out = self._submit(user=DA3, changes={"mobile": "+251911999777"})
		self.assertEqual(out["status"], "success", out)
		self.assertTrue(out["data"]["no_supervisor_found"])
		self.assertEqual(out["data"]["status"], "Pending Supervisor")  # it waits; nobody else decides
		other = self._submit()["data"]  # a Woreda that has its Supervisor
		# the Administrator's screen loads the ones that need a Supervisor assigned
		frappe.set_user(ADMIN1)
		rows = [
			r for r in api.list_requests(no_supervisor_found=1, limit=500)["data"] if r["da_id"] in TEST_DAS
		]
		self.assertEqual([r["name"] for r in rows], [out["data"]["name"]])
		self.assertFalse(rows[0]["can_decide"])  # level 1 stays the Supervisor's
		self.assertNotIn(other["name"], [r["name"] for r in rows])
		frappe.db.set_value(PCR, out["data"]["name"], "status", "Cancelled")
		rows = api.list_requests(no_supervisor_found=1, limit=500)["data"]
		self.assertNotIn(out["data"]["name"], [r["name"] for r in rows])  # no longer waiting

	def test_da_id_in_the_request_is_ignored_for_a_da(self):
		out = self._submit(da_id="DA-000004", internal_record_id="someone-else")
		self.assertEqual(out["status"], "success", out)
		self.assertEqual(out["data"]["da_id"], "DA-000001")
		self.assertFalse(frappe.db.exists("DA Profile Change Request", {"da_id": "DA-000004"}))

	def test_submit_validation(self):
		frappe.set_user(DA1)
		frappe.db.commit()  # nosemgrep: error calls below roll back uncommitted rows
		cases = [
			{"changes": {"salary": "1"}},  # not a profile field
			{"changes": {"language": "am"}},  # not in the registry
			{"changes": {}},
			{"changes": {"email": "not-an-email"}},
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

	def test_identity_change_with_a_note(self):
		out = self._submit(
			changes={"fayda_id": "1234567890123456", "gender": "Male", "birth_month_ec": "9"},
			reason="The Fayda card shows a different number",
		)
		self.assertEqual(out["status"], "success", out)
		r = out["data"]
		self.assertEqual(r["section"], "Identity")
		self.assertEqual(
			r["changes"]["gender"], {"from": None, "from_label": None, "to": "MALE", "to_label": "Male"}
		)
		self.assertEqual(r["changes"]["birth_month_ec"]["to"], 9)
		self._approve_both(r["name"])
		(writer, update) = self.writers[0]
		self.assertEqual(writer, ADMIN1)
		self.assertEqual(
			update.changes, {"fayda_id": "1234567890123456", "gender": "MALE", "birth_month_ec": 9}
		)

	def test_one_open_request_per_field_and_cancel(self):
		first = self._submit(changes={"email": "abebe@moa.gov.et"})["data"]
		frappe.db.commit()  # nosemgrep
		out = self._submit(changes={"email": "other@moa.gov.et"})  # the same field again
		self._error(out, 400)
		self.assertIn("open change request for email", out["message"])
		# the field is taken even when the new request also brings a free one
		self._error(self._submit(changes={"email": "other@moa.gov.et", "telegram": "+251911222333"}), 400)
		second = self._submit(changes={"mobile": "+251911999888"})  # another field of the same section
		self.assertEqual(second["status"], "success", second)
		self.assertEqual(second["data"]["section"], "Contact")
		ok = self._submit(changes={"specialisation": "Livestock"})  # another section
		self.assertEqual(ok["status"], "success", ok)
		frappe.set_user(DA1)
		self.assertEqual(len(api.my_profile()["data"]["pending_requests"]), 3)
		out = api.cancel(first["name"])
		self.assertEqual(out["data"]["status"], "Cancelled")
		self.assertEqual(self._submit(changes={"email": "other@moa.gov.et"})["status"], "success")

	def test_two_requests_of_one_section_are_applied_one_after_the_other(self):
		a = self._submit(changes={"email": "abebe@moa.gov.et"})["data"]
		b = self._submit(changes={"mobile": "+251911999888"})["data"]
		self.assertEqual(self._approve_both(a["name"])["data"]["status"], "Applied")
		self.assertEqual(self._approve_both(b["name"])["data"]["status"], "Applied")
		self.assertEqual(
			[w[1].changes for w in self.writers], [{"email": "abebe@moa.gov.et"}, {"phone": "+251911999888"}]
		)
		ref = MockDARegistryClient.shared().get_da("DA-000001")
		self.assertEqual((ref.phone, ref.email), ("+251911999888", "abebe@moa.gov.et"))

	def test_a_second_request_refused_while_the_registry_holds_the_first_can_be_retried(self):
		# Dev until the registry's own approval is switched off: the first change waits
		# there, and the registry takes one pending change per DA and section.
		a = self._submit(changes={"email": "abebe@moa.gov.et"})["data"]
		b = self._submit(changes={"mobile": "+251911999888"})["data"]
		with patch.object(MockDARegistryClient, "change_request_status", return_value="Pending"):
			self.assertEqual(self._approve_both(a["name"])["data"]["status"], "Sent to registry")
			self._decide(SUP, b["name"])
			busy = IntegrationResult(
				accepted=False,
				message="A pending change request already exists for this record and section",
				error_code="REQ-VAL-000",
			)
			with patch.object(MockDARegistryClient, "push_profile_update", return_value=busy):
				d = self._decide(ADMIN1, b["name"])["data"]
			self.assertEqual((d["status"], d["registry_error_code"]), ("Registry failed", "REQ-VAL-000"))
		frappe.set_user(ADMIN1)  # the first one has been applied meanwhile
		self.assertEqual(api.retry(b["name"])["data"]["status"], "Applied")

	def test_an_administrator_cannot_send_a_request(self):
		frappe.set_user(ADMIN1)
		self._error(api.submit(changes=dict(CONTACT), da_id="DA-000001"), 403)

	# -- two levels -------------------------------------------------------------------------

	def test_two_approvals_then_one_change_request_with_the_administrators_token(self):
		r = self._submit()["data"]
		frappe.set_user(SUP)
		self.assertEqual(
			[q["name"] for q in api.list_requests(status="Pending Supervisor")["data"]], [r["name"]]
		)
		out = self._decide(SUP, r["name"])
		d = out["data"]
		self.assertEqual((d["status"], d["l1_by"]), ("Pending Administrator", SUP))
		self.assertIn("waits for an OAN Administrator", out["message"])
		self.assertEqual(self.writers, [])  # nothing sent at level 1
		self.assertEqual(len(self._notified(ADMIN1, "profile_change_waiting")), 1)

		out = self._decide(ADMIN1, r["name"], note="Checked")
		d = out["data"]
		self.assertEqual(
			(d["status"], d["l2_by"], d["registry_sync_state"], d["registry_approval"]),
			("Applied", ADMIN1, "Accepted", "Approved"),
		)
		self.assertTrue(d["registry_reference"].startswith("MOCK-"))
		self.assertEqual([w[0] for w in self.writers], [ADMIN1])  # exactly one, the Administrator's
		self.assertEqual(self.writers[0][1].changes, {"phone": "+251911999888", "email": "abebe@moa.gov.et"})
		ref = MockDARegistryClient.shared().get_da("DA-000001")
		self.assertEqual((ref.phone, ref.email), ("+251911999888", "abebe@moa.gov.et"))
		titles = self._notified(DA1, "profile_change_decided")
		self.assertTrue(any("waits for the Administrator" in t for t in titles), titles)
		self.assertTrue(any("is now in the DA Registry" in t for t in titles), titles)
		events = frappe.get_all(
			"DA Audit Event", filters={"entity_name": r["name"]}, pluck="action", order_by="creation asc"
		)
		for action in (
			"profile_change_requested",
			"profile_change_level1_decided",
			"profile_change_level2_decided",
		):
			self.assertIn(action, events)
		frappe.set_user(DA1)
		self.assertEqual(api.my_profile()["data"]["profile"]["mobile"], "+251911999888")

	def test_the_same_person_cannot_give_both_approvals(self):
		r = self._submit()["data"]
		self.assertEqual(self._decide(SUP_ADMIN, r["name"])["data"]["status"], "Pending Administrator")
		frappe.db.commit()  # nosemgrep
		self._error(self._decide(SUP_ADMIN, r["name"]), 403)
		self.assertEqual(self.writers, [])
		self.assertEqual(self._decide(ADMIN2, r["name"])["data"]["status"], "Applied")

	def test_who_may_decide_which_level(self):
		r = self._submit()["data"]
		frappe.db.commit()  # nosemgrep
		self._error(self._decide(ADMIN1, r["name"]), 403)  # an Administrator does not give level 1
		self._error(self._decide(SUP_OTHER, r["name"]), 403)  # other Woreda
		self._error(self._decide(EXEC, r["name"]), 403)
		self._decide(SUP, r["name"])
		frappe.db.commit()  # nosemgrep
		self._error(self._decide(SUP2, r["name"]), 403)  # no Supervisor at level 2
		self._error(self._decide(ADMIN1, r["name"], "Request changes", "Attach the receipt"), 400)
		self.assertEqual(self.writers, [])

	def test_two_supervisors_one_decision(self):
		_grant(SUP2, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		r = self._submit()["data"]
		self._decide(SUP, r["name"])
		frappe.db.commit()  # nosemgrep
		out = self._decide(SUP2, r["name"])
		self._error(out, 403)
		self.assertIn(f"already approved by {SUP}", out["message"])

	def test_nobody_decides_their_own_request(self):
		frappe.set_user(SUP)
		out = api.submit(changes={"specialisation": "Livestock"}, reason="Retrained", da_id="DA-000001")
		r = out["data"]
		frappe.db.commit()  # nosemgrep
		self._error(self._decide(SUP, r["name"]), 403)

	def test_reject_and_request_changes_need_a_note(self):
		r = self._submit()["data"]
		frappe.db.commit()  # nosemgrep
		self._error(self._decide(SUP, r["name"], "Reject"), 400)
		self._error(self._decide(SUP, r["name"], "Maybe", "whatever"), 400)
		out = self._decide(SUP, r["name"], "Request changes", "Attach the new SIM receipt")
		self.assertEqual(out["data"]["status"], "Changes requested")
		frappe.db.commit()  # nosemgrep
		self._error(self._decide(SUP, r["name"]), 400)  # already decided
		self.assertEqual(self.writers, [])

	def test_the_administrator_rejects(self):
		r = self._submit()["data"]
		self._decide(SUP, r["name"])
		out = self._decide(ADMIN1, r["name"], "Reject", "Number belongs to another DA")
		self.assertEqual(out["data"]["status"], "Rejected")
		self.assertEqual(out["data"]["l2_note"], "Number belongs to another DA")
		self.assertEqual(self.writers, [])
		self.assertTrue(any("rejected" in t for t in self._notified(DA1, "profile_change_decided")))

	def test_scope_on_read(self):
		r = self._submit()["data"]
		frappe.db.commit()  # nosemgrep
		frappe.set_user(SUP_OTHER)
		self.assertEqual(api.list_requests()["data"], [])
		self._error(api.get_request(r["name"]), 403)
		frappe.set_user(EXEC)  # Region read
		self.assertEqual(api.get_request(r["name"])["status"], "success")
		frappe.set_user(DA3)
		self._error(api.get_request(r["name"]), 403)
		self._error(api.cancel(r["name"]), 403)

	# -- the Supervisor's own request --------------------------------------------------------

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

	def test_registry_pending_waits_and_is_picked_up_later(self):
		r = self._submit()["data"]
		with patch.object(MockDARegistryClient, "change_request_status", return_value="Pending"):
			d = self._approve_both(r["name"])["data"]
			self.assertEqual((d["status"], d["registry_approval"]), ("Sent to registry", "Pending"))
			self.assertEqual(d["status_label"], "Approved, waiting for the DA Registry")
			self.assertTrue(any("waiting for the DA Registry" in t for t in self._notified(DA1)))
			frappe.db.commit()  # nosemgrep
			out = self._submit(changes={"mobile": "+251911777666"})
			self._error(out, 400)  # the section stays open while the registry decides
			frappe.set_user(DA1)
			self.assertEqual([p["name"] for p in api.my_profile()["data"]["pending_requests"]], [r["name"]])
		# The registry has applied it: the DA's next visit reads that back with their own token.
		frappe.set_user(DA1)
		d = api.my_profile()["data"]
		self.assertEqual(d["pending_requests"], [])
		self.assertEqual(self._row(r["name"]).status, "Applied")
		self.assertEqual(len(self._notified(DA1, "profile_change_registry_decided")), 1)
		self.assertEqual(self._submit(changes={"mobile": "+251911777666"})["status"], "success")
		self.assertEqual(len(self.writers), 1)

	def test_the_registry_rejects_later(self):
		r = self._submit()["data"]
		with patch.object(MockDARegistryClient, "change_request_status", return_value="Pending"):
			self._approve_both(r["name"])
		with patch.object(MockDARegistryClient, "change_request_status", return_value="Rejected"):
			frappe.set_user(ADMIN1)
			d = api.get_request(r["name"])["data"]
		self.assertEqual((d["status"], d["registry_approval"]), ("Registry failed", "Rejected"))
		self.assertTrue(any("refused" in t for t in self._notified(DA1, "profile_change_registry_decided")))

	def test_a_refused_change_shows_the_reason_and_can_be_retried(self):
		r = self._submit(changes={"fayda_id": "1234567890123456"}, reason="New Fayda card issued")["data"]
		self._decide(SUP, r["name"])
		refusal = IntegrationResult(
			accepted=False,
			message="DA Registry refused to create change request: Fayda ID 1234567890123456 is already registered as DA-7485687914",
			error_code="REQ-VAL-000",
		)
		with patch.object(MockDARegistryClient, "push_profile_update", return_value=refusal):
			out = self._decide(ADMIN1, r["name"])
		d = out["data"]
		self.assertEqual(
			(d["status"], d["registry_sync_state"], d["registry_error_code"]),
			("Registry failed", "Refused", "REQ-VAL-000"),
		)
		self.assertIn("already registered as DA-7485687914", out["message"])  # the Administrator sees why
		self.assertIn("already registered", d["registry_error"])
		self.assertTrue(any("refused" in t for t in self._notified(DA1, "profile_change_decided")))
		frappe.db.commit()  # nosemgrep
		self._error(self._submit(changes={"fayda_id": "1234567890123457"}, reason="Another number here"), 400)

		frappe.set_user(SUP)
		self._error(api.retry(r["name"]), 403)
		frappe.set_user(ADMIN2)
		out = api.retry(r["name"])
		self.assertEqual(out["data"]["status"], "Applied", out)
		self.assertEqual(out["data"]["push_attempts"], 2)
		self.assertEqual([w[0] for w in self.writers], [ADMIN2])

	def test_a_refused_change_can_be_closed_so_the_da_can_send_another(self):
		r = self._submit()["data"]
		self._decide(SUP, r["name"])
		refusal = IntegrationResult(
			accepted=False, message="Phone: enter an Ethiopian number", error_code="INVALID_FIELD"
		)
		with patch.object(MockDARegistryClient, "push_profile_update", return_value=refusal):
			self._decide(ADMIN1, r["name"])
		frappe.set_user(ADMIN1)
		frappe.db.commit()  # nosemgrep
		self._error(api.close(r["name"], note="no"), 400)
		out = api.close(r["name"], note="Number refused, send a corrected one")
		self.assertEqual((out["data"]["status"], out["data"]["closed_by"]), ("Closed", ADMIN1))
		self.assertEqual(len(self._notified(DA1, "profile_change_closed")), 1)
		self.assertEqual(self._submit(changes={"mobile": "+251911777666"})["status"], "success")

	def test_registry_unavailable_takes_the_retry_path(self):
		r = self._submit()["data"]
		self._decide(SUP, r["name"])
		with patch.object(
			MockDARegistryClient, "push_profile_update", side_effect=DARegistryUnavailable("down")
		):
			out = self._decide(ADMIN1, r["name"])
		d = out["data"]
		self.assertEqual((d["status"], d["registry_sync_state"]), ("Registry failed", "Unavailable"))
		self.assertIn("unavailable", out["message"])
		self.assertTrue(any("unavailable" in t for t in self._notified(DA1, "profile_change_decided")))
		frappe.set_user(ADMIN1)
		self.assertEqual(api.retry(r["name"])["data"]["status"], "Applied")

	def test_a_retry_after_a_lost_answer_does_not_create_a_second_change_request(self):
		r = self._submit()["data"]
		self._decide(SUP, r["name"])
		recorded = MockDARegistryClient.push_profile_update  # applies the change and records the writer

		def applied_then_timeout(client, update):
			recorded(client, update)  # the registry took it ...
			raise DARegistryUnavailable("read timed out")  # ... but its answer never arrived

		with patch.object(MockDARegistryClient, "push_profile_update", applied_then_timeout):
			d = self._decide(ADMIN1, r["name"])["data"]
		self.assertEqual((d["status"], d["registry_sync_state"]), ("Registry failed", "Unavailable"))
		self.assertEqual(len(self.writers), 1)
		frappe.set_user(ADMIN1)
		d = api.retry(r["name"])["data"]
		self.assertEqual((d["status"], d["registry_sync_state"]), ("Applied", "Accepted"))
		self.assertEqual(len(self.writers), 1)  # the record already holds the values: no duplicate
		actions = frappe.get_all("DA Audit Event", filters={"entity_name": r["name"]}, pluck="details")
		self.assertTrue(any("already_applied" in (a or "") for a in actions))

	def test_the_approval_survives_once_the_registry_was_asked(self):
		# The decision is committed before the request ends (the registry write cannot be
		# rolled back): a failure later in the request cannot lose it.
		r = self._submit()["data"]
		self._decide(SUP, r["name"])
		self._decide(ADMIN1, r["name"])
		frappe.db.rollback()
		row = self._row(r["name"])
		self.assertEqual((row.status, row.l1_by, row.l2_by), ("Applied", SUP, ADMIN1))
		self.assertTrue(row.registry_reference)

	def test_no_registry_write_in_a_da_or_supervisor_request(self):
		r = self._submit()["data"]
		self._decide(SUP, r["name"])
		doc = self._row(r["name"])
		for user in (DA1, SUP):
			frappe.set_user(user)
			with self.assertRaises(frappe.PermissionError):
				svc._send_to_registry(doc, get_scope_context(user))
		self.assertEqual(self.writers, [])

	# -- Approvals screen: filters, names, bulk -----------------------------------------------

	def test_the_queue_filters_by_several_statuses_woreda_and_section(self):
		a = self._submit()["data"]  # Contact, Woreda ET04-W01
		b = self._submit(changes={"specialisation": "Livestock"})["data"]  # Qualifications
		c = self._submit(user=DA3, changes={"mobile": "+251911999777"})["data"]  # Woreda ET03-W02
		self._approve_both(a["name"])
		self._decide(SUP, b["name"])
		frappe.set_user(ADMIN1)
		rows = api.list_requests(status="Pending Administrator,Pending Supervisor", limit=500)["data"]
		rows = [r for r in rows if r["da_id"] in TEST_DAS]  # an Administrator sees the whole site
		self.assertEqual({r["name"] for r in rows}, {b["name"], c["name"]})
		rows = api.list_requests(status="Pending Administrator,Pending Supervisor", woreda="ET03-W02")["data"]
		self.assertEqual([r["name"] for r in rows], [c["name"]])
		rows = api.list_requests(section="Qualifications", da_id="DA-000001")["data"]
		self.assertEqual([r["name"] for r in rows], [b["name"]])
		self.assertEqual(rows[0]["da_name"], "Abebe Kebede")  # shown without a registry read
		out = api.list_requests(woreda="ET04-W01,ET03-W02", limit=500)
		self.assertEqual(len([r for r in out["data"] if r["da_id"] in TEST_DAS]), 3)
		self.assertGreaterEqual(out["meta"]["total"], 3)
		self._error(api.list_requests(status="Pending"), 400)  # the old one-level state
		frappe.set_user(SUP)  # the scope still applies under a Woreda filter
		self.assertEqual(api.list_requests(woreda="ET03-W02")["data"], [])

	def test_bulk_decide_goes_on_past_requests_it_cannot_decide(self):
		a = self._submit()["data"]
		b = self._submit(changes={"specialisation": "Livestock"})["data"]
		c = self._submit(user=DA3, changes={"mobile": "+251911999777"})["data"]  # other Woreda
		frappe.set_user(SUP)
		out = api.bulk_decide(ids=[a["name"], b["name"], c["name"], "DA-PCR-NOPE"], decision="Approve")
		self.assertEqual(out["status"], "success", out)
		d = out["data"]
		self.assertEqual((d["decided"], d["failed"]), (2, 2))
		by_name = {r["name"]: r for r in d["results"]}
		self.assertEqual(by_name[a["name"]]["status"], "Pending Administrator")
		self.assertFalse(by_name[c["name"]]["ok"])
		self.assertEqual(self._row(c["name"]).status, "Pending Supervisor")  # untouched
		self.assertEqual(self.writers, [])

		with patch.object(MockDARegistryClient, "change_request_status", return_value="Pending"):
			frappe.set_user(ADMIN1)
			out = api.bulk_decide(ids=json_ids([a["name"], b["name"]]), decision="Approve")["data"]
		self.assertEqual([r["status"] for r in out["results"]], ["Sent to registry", "Sent to registry"])
		self.assertEqual([w[0] for w in self.writers], [ADMIN1, ADMIN1])  # one change request each

		frappe.set_user(ADMIN1)
		frappe.db.commit()  # nosemgrep
		self._error(api.bulk_decide(ids=[], decision="Approve"), 400)
		self._error(api.bulk_decide(ids=[f"X{i}" for i in range(51)], decision="Approve"), 400)

	def test_bulk_decide_is_routed(self):
		from da_services.api.router import ensure_routes_registered

		ensure_routes_registered()
		adapter = frappe.api.API_URL_MAP.bind("localhost")
		endpoint, _args = adapter.match(
			"/api/v1/da-services/profile/change-requests/bulk-decide", method="POST"
		)
		self.assertIn("bulk_decide", str(endpoint))

	# -- what the screens are told ---------------------------------------------------------

	def test_coded_fields_carry_their_options_and_the_current_code(self):
		frappe.set_user(SUP)
		d = api.agent_profile("DA-000001")["data"]
		tier = d["fields"]["education_tier"]
		self.assertIn({"code": "DIPLOMA", "label": "Diploma"}, tier["options"])
		self.assertEqual(len(tier["options"]), 4)
		# the DA's own value by code, so the screen can mark it in the list
		self.assertEqual((tier["current"], tier["current_label"]), ("DIPLOMA", "Diploma"))
		self.assertEqual(d["profile"]["education_tier"], "Diploma")
		self.assertEqual(d["fields"]["specialisation"]["current"], "CROP_PRODUCTION")
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

	def test_the_profile_still_loads_when_a_list_cannot_be_read(self):
		frappe.set_user(DA1)
		with patch.object(
			MockDARegistryClient, "attribute_values", side_effect=DARegistryUnavailable("Master Data is down")
		):
			out = api.my_profile()
		self.assertEqual(out["status"], "success", out)
		tier = out["data"]["fields"]["education_tier"]
		self.assertIsNone(tier["options"])  # no list now: the value is checked on submit
		self.assertIsNone(tier["current"])
		self.assertEqual(tier["current_label"], "Diploma")

	def test_a_change_keeps_the_old_and_the_new_label(self):
		r = self._submit(changes={"specialisation": "Livestock"})["data"]
		self.assertEqual(
			r["changes"]["specialisation"],
			{
				"from": "CROP_PRODUCTION",
				"from_label": "Crop production",
				"to": "LIVESTOCK",
				"to_label": "Livestock",
			},
		)
		frappe.set_user(DA1)
		profile = api.my_profile()["data"]["profile"]
		self.assertEqual(
			(r["region_name"], r["zone_name"], r["woreda_name"]),
			(profile["region_name"], profile["zone_name"], profile["woreda_name"]),
		)

	def test_each_request_says_what_the_caller_may_do(self):
		r = self._submit()["data"]
		self.assertEqual((r["can_cancel"], r["can_decide"], r["allowed_decisions"]), (True, False, []))
		self.assertEqual(r["requested_by_name"], frappe.db.get_value("User", DA1, "full_name"))
		self.assertIsNone(r["l1_by_name"])

		def seen_by(user):
			frappe.set_user(user)
			return api.get_request(r["name"])["data"]

		sup = seen_by(SUP)
		self.assertEqual(sup["allowed_decisions"], ["Approve", "Reject", "Request changes"])
		self.assertEqual((sup["can_decide"], sup["can_cancel"], sup["can_retry"]), (True, False, False))
		self.assertFalse(seen_by(ADMIN1)["can_decide"])  # level 1 is the Supervisor's
		self.assertFalse(seen_by(EXEC)["can_decide"])

		self._decide(SUP_ADMIN, r["name"])  # a Supervisor who is also an Administrator
		admin = seen_by(ADMIN1)
		self.assertEqual((admin["can_decide"], admin["allowed_decisions"]), (True, ["Approve", "Reject"]))
		self.assertEqual(admin["l1_by_name"], frappe.db.get_value("User", SUP_ADMIN, "full_name"))
		self.assertFalse(seen_by(SUP_ADMIN)["can_decide"])  # gave level 1
		self.assertFalse(seen_by(SUP)["can_decide"])
		self.assertTrue(seen_by(DA1)["can_cancel"])  # still waiting for a decision

		refusal = IntegrationResult(accepted=False, message="refused", error_code="REQ-VAL-000")
		with patch.object(MockDARegistryClient, "push_profile_update", return_value=refusal):
			d = self._decide(ADMIN1, r["name"])["data"]
		self.assertEqual(
			(d["can_retry"], d["can_close"], d["can_decide"], d["can_sync_local"]), (True, True, False, False)
		)
		da = seen_by(DA1)
		self.assertEqual((da["can_cancel"], da["can_retry"], da["can_close"]), (False, False, False))
		# the flags are the rule the action applies
		frappe.db.commit()  # nosemgrep
		self._error(self._decide(SUP, r["name"]), 400)

	def test_refusals_a_screen_treats_differently_have_their_own_code(self):
		r = self._submit(changes={"email": "abebe@moa.gov.et"})["data"]
		frappe.db.commit()  # nosemgrep: error answers roll back uncommitted rows

		out = self._submit(changes={"email": "other@moa.gov.et"})
		self._error(out, 400)
		self.assertEqual(out["code"], "OPEN_REQUEST_EXISTS")

		out = self._submit(changes={"mobile": "+251911000001"})
		self._error(out, 400)
		self.assertEqual(out["code"], "NOTHING_TO_CHANGE")

		out = self._submit(changes={"education_tier": "PhD"})
		self._error(out, 400)
		self.assertEqual(out["code"], "VALIDATION_ERROR")
		self.assertIn("must be one of", out["details"]["education_tier"])  # shown under the field

		out = self._submit(changes={"mobile": "+251911999888", "specialisation": "Livestock"})
		self._error(out, 400)  # two sections: about the request, not one field
		self.assertEqual((out["code"], out["details"]), ("VALIDATION_ERROR", {}))

		self.assertEqual(self._decide(SUP, r["name"], "Reject", "Not this address")["status"], "success")
		frappe.db.commit()  # nosemgrep
		out = self._decide(SUP_ADMIN, r["name"])
		self._error(out, 400)
		self.assertEqual(out["code"], "ALREADY_DECIDED")  # the screen reloads the request
		frappe.set_user(DA1)
		out = api.cancel(r["name"])
		self._error(out, 400)
		self.assertEqual(out["code"], "ALREADY_DECIDED")
		self.assertEqual(self._row(r["name"]).status, "Rejected")

		other = self._submit(changes={"telegram": "+251911222333"})["data"]
		frappe.db.commit()  # nosemgrep
		out = self._decide(SUP_OTHER, other["name"])
		self._error(out, 403)  # not theirs to decide: the shared handler's code
		self.assertEqual(out["code"], "PERMISSION_DENIED")

	def test_a_supervisor_lists_the_requests_they_sent(self):
		own = self._submit()["data"]  # the DA's
		frappe.set_user(SUP)
		sent = api.submit(changes={"position": "SENIOR_DA"}, reason="Promoted", da_id="DA-000001")["data"]
		self.assertEqual(
			[r["name"] for r in api.list_requests(status="Pending Supervisor")["data"]], [own["name"]]
		)
		mine = api.list_requests(mine=1)
		self.assertEqual([r["name"] for r in mine["data"]], [sent["name"]])
		self.assertEqual(mine["meta"]["total"], 1)
		self.assertTrue(mine["data"][0]["can_cancel"])
		self.assertEqual(api.cancel(sent["name"])["data"]["status"], "Cancelled")
		frappe.set_user(DA1)
		self.assertEqual([r["name"] for r in api.list_requests(mine="true")["data"]], [own["name"]])

	def test_an_executive_reads_a_da_of_their_region_and_cannot_request(self):
		frappe.set_user(EXEC)
		out = api.agent_profile("DA-000001")
		self.assertEqual(out["status"], "success", out)
		self.assertFalse(out["data"]["can_request"])
		frappe.db.commit()  # nosemgrep
		self._error(api.agent_profile("DA-000004"), 403)  # another Region
		self._error(api.submit(changes=dict(CONTACT), reason="New SIM", da_id="DA-000001"), 403)
		frappe.set_user(ADMIN1)
		self.assertFalse(api.agent_profile("DA-000001")["data"]["can_request"])  # approves, never asks

	def test_bulk_results_carry_the_label_and_a_code(self):
		a = self._submit()["data"]
		c = self._submit(user=DA3, changes={"mobile": "+251911999777"})["data"]  # other Woreda
		frappe.set_user(SUP)
		results = api.bulk_decide(ids=[a["name"], c["name"], "DA-PCR-NOPE"], decision="Approve")["data"][
			"results"
		]
		by_name = {r["name"]: r for r in results}
		self.assertEqual(
			(by_name[a["name"]]["status_label"], by_name[a["name"]]["code"]),
			("Waiting for the Administrator", None),
		)
		self.assertEqual(by_name[c["name"]]["code"], "PERMISSION_DENIED")
		self.assertEqual(by_name["DA-PCR-NOPE"]["code"], "NOT_FOUND")
		again = api.bulk_decide(ids=[a["name"]], decision="Approve")["data"]["results"][0]
		self.assertFalse(again["ok"])  # level 2 is not the Supervisor's

	# -- location ----------------------------------------------------------------------------

	def test_a_transfer_sends_the_full_chain_and_moves_the_da(self):
		new = {"region": "ET03", "zone": "ET03-Z02", "woreda": "ET03-W02", "kebele": "ET03-W02-K01"}
		out = self._submit(changes=new, reason="Transfer to Ankober")
		self.assertEqual(out["status"], "success", out)
		r = out["data"]
		self.assertEqual(r["section"], "Location")
		self.assertEqual(r["woreda"], "ET04-W01")  # the current area's Supervisor decides level 1
		self.assertEqual(
			r["changes"]["kebele"],
			{"from": "ET04-W01-K03", "from_label": None, "to": "ET03-W02-K01", "to_label": "Ankober 01"},
		)
		frappe.db.commit()  # nosemgrep
		self._error(self._decide(SUP_OTHER, r["name"]), 403)
		d = self._approve_both(r["name"])["data"]
		self.assertEqual(d["status"], "Applied", d)

		(_writer, update) = self.writers[0]
		self.assertEqual(set(update.changes), {"kebele", "geo_code_hierarchy_json"})
		chain = update.changes["geo_code_hierarchy_json"]
		self.assertEqual(
			[(row["level"], row["level_value_id"]) for row in chain["hierarchy"]],
			[
				("country", "ET"),
				("region", "ET03"),
				("zone", "ET03-Z02"),
				("woreda", "ET03-W02"),
				("kebele", "ET03-W02-K01"),
			],
		)
		self.assertEqual(chain["lowest_level_value_id"], "ET03-W02-K01")
		self.assertEqual(chain["hierarchy"][0]["level_id"], "l0")

		live = frappe.get_all(
			"DA RBAC Assignment",
			filters={"user": DA1, "role": C.ROLE_DA, "active": 1},
			fields=["region_scope", "zone_scope", "woreda_scope"],
		)
		self.assertEqual(
			[(g.region_scope, g.zone_scope, g.woreda_scope) for g in live], [("ET03", "ET03-Z02", "ET03-W02")]
		)
		self.assertEqual(frappe.db.count("DA RBAC Assignment", {"user": DA1, "active": 0}), 1)  # history kept
		self.assertEqual(len(self._notified(SUP_OTHER, "profile_change_transfer")), 1)
		self.assertTrue(
			any("Kebele Assignment" in t for t in self._notified(ADMIN1, "profile_change_transfer"))
		)
		frappe.set_user(DA1)
		self.assertEqual(api.my_profile()["data"]["profile"]["woreda"], "ET03-W02")

	def test_a_failed_local_update_after_a_transfer_is_flagged_and_can_be_repeated(self):
		new = {"region": "ET03", "zone": "ET03-Z02", "woreda": "ET03-W02", "kebele": "ET03-W02-K01"}
		r = self._submit(changes=new, reason="Transfer to Ankober")["data"]
		self._decide(SUP, r["name"])
		with patch.object(svc, "_move_da_grant", side_effect=RuntimeError("database gone")):
			d = self._decide(ADMIN1, r["name"])["data"]
		# The registry applied the change, so the request is Applied; the local part is flagged.
		self.assertEqual((d["status"], d["local_sync_state"]), ("Applied", "Failed"))
		self.assertIn("database gone", d["local_sync_error"])
		live = frappe.get_all(
			"DA RBAC Assignment", filters={"user": DA1, "role": C.ROLE_DA, "active": 1}, pluck="woreda_scope"
		)
		self.assertEqual(live, ["ET04-W01"])  # nothing half-moved
		self.assertTrue(
			any("was not updated" in t for t in self._notified(ADMIN1, "profile_change_transfer"))
		)
		self.assertEqual(self._notified(SUP_OTHER, "profile_change_transfer"), [])

		frappe.set_user(SUP)
		frappe.db.commit()  # nosemgrep
		self._error(api.sync_local(r["name"]), 403)
		frappe.set_user(ADMIN2)
		d = api.sync_local(r["name"])["data"]
		self.assertEqual((d["local_sync_state"], d["local_sync_error"]), ("Done", None))
		live = frappe.get_all(
			"DA RBAC Assignment", filters={"user": DA1, "role": C.ROLE_DA, "active": 1}, pluck="woreda_scope"
		)
		self.assertEqual(live, ["ET03-W02"])
		self.assertEqual(len(self._notified(SUP_OTHER, "profile_change_transfer")), 1)
		frappe.db.commit()  # nosemgrep
		self._error(api.sync_local(r["name"]), 400)  # nothing left to repeat

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

	# -- read-back modes -------------------------------------------------------------------

	def test_the_scheduled_read_back_works_in_service_mode(self):
		saved = frappe.conf.get("da_registry_auth")
		frappe.conf.da_registry_auth = "service"
		self.addCleanup(frappe.conf.__setitem__, "da_registry_auth", saved)
		r = self._submit()["data"]
		with patch.object(MockDARegistryClient, "change_request_status", return_value="Pending"):
			self._approve_both(r["name"])
		MockDARegistryClient.reset()  # the scheduler worker's mock never saw the push
		frappe.set_user("Administrator")
		self.assertEqual(svc.sync_registry_approvals(), 1)
		self.assertEqual(self._row(r["name"]).status, "Applied")
		self.assertEqual(svc.sync_registry_approvals(), 0)

	def test_rows_cannot_be_deleted_or_edited_through_desk(self):
		frappe.set_user(DA1)
		r = svc.submit_change_request(changes={"telegram": "+251911222333"}, ctx=get_scope_context(DA1))
		doc = self._row(r["name"])
		self.assertFalse(frappe.has_permission("DA Profile Change Request", "write", doc, user=SUP))
		self.assertTrue(frappe.has_permission("DA Profile Change Request", "read", doc, user=SUP))
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("DA Profile Change Request", r["name"], ignore_permissions=True)


class TestRegistryReadBackWithUserTokens(IntegrationTestCase):
	def test_the_scheduled_read_back_is_skipped_without_a_service_identity(self):
		saved = frappe.conf.get("da_registry_auth")
		frappe.conf.da_registry_auth = "user"
		self.addCleanup(frappe.conf.__setitem__, "da_registry_auth", saved)
		self.assertEqual(svc.sync_registry_approvals(), 0)
