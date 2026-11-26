"""DA profile change: request → Supervisor above the DA (level 1) → OAN Administrator (level 2) → Registry.

The steps are the Frappe Workflow of DA Profile Change Request; who is above whom comes
from DA Hierarchy (here: SUP_ADMIN ─ SUP ─ DA1, and SUP_OTHER ─ DA3).

The mock registry records every change request it is given (``pushed``) together with
the user whose request sent it, so the tests can prove that nothing reaches the registry
before the second approval and that only an Administrator's request writes to it.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.api.v1 import profile_self as api
from da_services.da_services.doctype.da_hierarchy.da_hierarchy import ensure_root
from da_services.integrations.da_registry.client import DARegistryAuthError
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import constants as C
from da_services.services import profile as svc
from da_services.services import profile_fields
from da_services.tests.fixtures import as_user, ensure_doctype_schema, ensure_geo_masters, set_da_registry_id
from da_services.workflows import desk
from da_services.workflows import profile_change as workflow


def _pdf() -> bytes:
	"""A real one-page PDF: Frappe opens a PDF when it stores it."""
	import io

	from pypdf import PdfWriter

	writer, out = PdfWriter(), io.BytesIO()
	writer.add_blank_page(width=72, height=72)
	writer.write(out)
	return out.getvalue()


PDF = _pdf()


def _jpeg_with_exif() -> bytes:
	"""A photo as a phone takes it: Frappe re-encodes such a file without the EXIF data when
	it stores it, so what is stored is not what was uploaded."""
	import io

	from PIL import Image

	exif = Image.Exif()
	exif[0x010F] = "PhoneMaker"  # Make
	exif[0x0112] = 6  # Orientation
	out = io.BytesIO()
	Image.new("RGB", (16, 8), (200, 60, 60)).save(out, format="JPEG", exif=exif)
	return out.getvalue()


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
TEST_DAS = ("DA-000001", "DA-000002", "DA-000004")  # mock registry fixtures
PCR = "DA Profile Change Request"
# For the tests that change the Workflow as an administrator would in the Desk
TEST_LEVEL = "PCR Test Level"
SEND_BACK = "PCR Test Send Back"
STATE_ROW = ("state", "doc_status", "allow_edit", "update_field", "update_value", "evaluate_as_expression")
TRANSITION_ROW = ("state", "action", "next_state", "allowed", "allow_self_approval", "condition")
WORKFLOW_BACKUP = "pcr_test_workflow_backup"


def _put_the_workflow_back():
	"""Undo ``_change_the_workflow`` from what it kept on the site. Raises when it could not:
	a test must not pass with the site's Workflow changed."""
	frappe.set_user("Administrator")
	kept = frappe.db.get_global(WORKFLOW_BACKUP)
	if not kept:
		return
	kept = frappe.parse_json(kept)
	doc = frappe.get_doc("Workflow", kept["name"])
	doc.set("states", kept["states"])
	doc.set("transitions", kept["transitions"])
	doc.save()
	frappe.db.delete("Custom DocPerm", {"parent": PCR})
	for row in kept["permissions"]:
		frappe.get_doc({**row, "doctype": "Custom DocPerm"}).db_insert()
	for doctype, value in (("Workflow State", TEST_LEVEL), ("Workflow Action Master", SEND_BACK)):
		frappe.delete_doc_if_exists(doctype, value, force=True)
	frappe.db.set_global(WORKFLOW_BACKUP, None)
	frappe.cache.hdel("workflow", PCR)
	frappe.clear_cache(doctype=PCR)
	frappe.db.commit()  # nosemgrep


def _clean():
	"""Remove only what these tests create: never empty a table (a dev site holds real grants)."""
	requests = frappe.get_all(PCR, filters={"da_id": ("in", TEST_DAS)}, pluck="name")
	if requests:
		# what Frappe keeps for each step of these requests
		frappe.db.delete("Comment", {"reference_doctype": PCR, "reference_name": ("in", requests)})
		frappe.db.delete("Version", {"ref_doctype": PCR, "docname": ("in", requests)})
		frappe.db.delete("DA Notification", {"reference_doctype": PCR, "reference_name": ("in", requests)})
		for name in frappe.get_all(
			"File", filters={"attached_to_doctype": PCR, "attached_to_name": ("in", requests)}, pluck="name"
		):
			frappe.delete_doc("File", name, ignore_permissions=True, force=True)  # the file on disk too
	frappe.db.delete("DA Certificate", {"da_id": ("in", TEST_DAS)})
	frappe.db.delete("DA Notification", {"user": ("in", TEST_USERS)})
	frappe.db.delete(PCR, {"da_id": ("in", TEST_DAS)})
	frappe.db.delete("DA RBAC Assignment", {"user": ("in", TEST_USERS)})
	for user in (DA1, DA3, SUP, SUP_OTHER, SUP_ADMIN):  # leaves before their groups
		if frappe.db.exists("DA Hierarchy", user):
			frappe.delete_doc("DA Hierarchy", user, ignore_permissions=True, force=True)
	frappe.db.delete("User Permission", {"user": ("in", TEST_USERS), "allow": "User"})


def _node(user, parent=None, group=0):
	"""Place a user in DA Hierarchy; the hierarchy sync gives everyone above a User Permission."""
	frappe.get_doc(
		{"doctype": "DA Hierarchy", "user": user, "is_group": group, "parent_da_hierarchy": parent}
	).insert(ignore_permissions=True)


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
		workflow.ensure_workflow()
		# a run that died while a test had the Workflow switched off or half saved
		frappe.db.set_value("Workflow", {"document_type": PCR}, "is_active", 1)
		frappe.cache.hdel("workflow", PCR)
		_clean()
		_put_the_workflow_back()  # only when an earlier run was killed before it did
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
		ensure_root()
		_node(SUP_ADMIN, group=1)
		_node(SUP, parent=SUP_ADMIN, group=1)
		_node(DA1, parent=SUP)
		_node(SUP_OTHER, group=1)
		_node(DA3, parent=SUP_OTHER)
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

	def _submit(self, values, **kw):
		"""Ask for new values of fields; the section is the one the first known field is in."""
		section = next(
			(profile_fields.FIELDS[f].section for f in values if f in profile_fields.FIELDS), "Contact"
		)
		return api.submit(section=section, values=values, **kw)

	def _act(self, user, name, action, note=None):
		frappe.set_user(user)
		frappe.db.commit()  # nosemgrep: a refused call rolls back what is not committed
		return api.act(request_id=name, action=action, note=note)

	def _decide(self, user, name, action=workflow.APPROVE, note=None):
		return self._act(user, name, action, note)

	def _notified(self, user, event=None):
		filters = {"user": user}
		if event:
			filters["event"] = event
		return frappe.get_all("DA Notification", filters=filters, pluck="title")

	def _error(self, out, status_code):
		self.assertEqual(out["status"], "error", out)
		self.assertEqual(frappe.response.get("http_status_code"), status_code, out)

	# -- view -----------------------------------------------------------------------------

	def test_a_supervisor_sees_a_da_of_their_team(self):
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
			{"changes": {"fayda_id": "1234567890123456"}, "reason": "Typo on the card"},  # not offered
			{"changes": {"first_name": "Abebech"}, "reason": "typo"},  # a name needs a longer reason
			{"changes": {"position": "SENIOR_DA"}, "reason": "Promoted"},  # not the DA's own to ask
			{"changes": {"reports_to": "DA-000004"}},  # nor whom they report to
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
			out = self._submit(body["changes"], reason=body.get("reason"))
			self._error(out, 400)
		self.assertEqual(frappe.db.count(PCR, {"da_id": ("in", TEST_DAS)}), 0)

	def test_an_administrator_cannot_send_a_request(self):
		frappe.set_user(ADMIN1)
		self._error(self._submit(dict(CONTACT), da_id="DA-000001"), 403)

	# -- two levels -------------------------------------------------------------------------

	def test_nobody_decides_their_own_request(self):
		frappe.set_user(SUP)
		out = self._submit({"specialisation": "Livestock"}, reason="Retrained", da_id="DA-000001")
		r = out["data"]
		frappe.db.commit()  # nosemgrep
		self._error(self._decide(SUP, r["name"]), 403)

	def test_a_supervisor_requests_a_change_for_a_da_of_their_team(self):
		frappe.set_user(SUP)
		out = self._submit({"position": "SENIOR_DA"}, reason="Promoted", da_id="DA-000001")
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
		self._error(self._act(DA1, r["name"], workflow.CANCEL), 403)  # not the DA's to withdraw
		d = self._decide(ADMIN1, r["name"])["data"]
		self.assertEqual(d["status"], "Applied")
		self.assertEqual([w[0] for w in self.writers], [ADMIN1])
		self.assertEqual(MockDARegistryClient.shared().get_da("DA-000001").position, "Senior DA")

	# -- a DA's own request, both levels (the Workflow) ----------------------------------------

	def _own_request(self, changes=None, reason=None):
		"""DA1 asks for a change to their own record (no ``da_id``)."""
		frappe.set_user(DA1)
		out = self._submit(dict(changes or CONTACT), reason=reason)
		self.assertEqual(out["status"], "success", out)
		self.assertEqual(frappe.response.get("http_status_code"), 201)
		return out["data"]["name"]

	def _history(self, name):
		"""(state reached, by whom) of each step recorded on the request, oldest first."""
		return [
			(step["to"], step.get("system") or step["by"])
			for step in workflow.steps(frappe.get_doc(PCR, name))
		]

	def test_a_das_request_goes_through_both_levels(self):
		name = self._own_request()
		row = frappe.db.get_value(
			PCR, name, ["status", "user", "owner", "initiated_by_role", "no_supervisor_found"], as_dict=True
		)
		self.assertEqual(
			(row.status, row.user, row.owner, row.initiated_by_role, row.no_supervisor_found),
			("Pending Supervisor", DA1, DA1, C.ROLE_DA, 0),
		)
		# those above the DA who can give level 1 are told; nobody else
		self.assertTrue(self._notified(SUP, "profile_change_waiting"))
		self.assertTrue(self._notified(SUP_ADMIN, "profile_change_waiting"))
		self.assertFalse(self._notified(SUP_OTHER, "profile_change_waiting"))

		for user in (DA1, SUP_OTHER, ADMIN1):  # own request; another team; level 2 does not skip level 1
			self._error(self._decide(user, name), 403)
		d = self._decide(SUP, name)["data"]
		self.assertEqual((d["status"], d["l1_by"], d["approver"]), ("Pending Administrator", SUP, SUP))
		self.assertTrue(d["l1_at"])
		self.assertEqual(self.writers, [])  # nothing reaches the registry before level 2
		self.assertTrue(self._notified(ADMIN1, "profile_change_waiting"))

		self._error(self._decide(SUP, name), 403)  # level 2 is not the Supervisor's
		self._error(self._decide(ADMIN1, name, workflow.REQUEST_CHANGES, "Please check the number"), 400)
		d = self._decide(ADMIN1, name, note="Checked")["data"]
		self.assertEqual((d["status"], d["l2_by"], d["l2_note"]), ("Applied", ADMIN1, "Checked"))
		self.assertEqual([w[0] for w in self.writers], [ADMIN1])
		self.assertEqual(MockDARegistryClient.shared().get_da("DA-000001").phone, CONTACT["mobile"])
		self.assertEqual(
			self._history(name),
			[("Pending Administrator", SUP), ("Sent to registry", ADMIN1), ("Applied", "DA Registry")],
		)
		out = self._decide(ADMIN2, name)  # decided: the screen reloads
		self.assertEqual((out["status"], out["code"]), ("error", "ALREADY_DECIDED"))

	def test_the_da_is_told_through_the_requests_user(self):
		# A DA known only by their User (no Development Agent assignment names their DA-ID).
		frappe.db.delete("DA RBAC Assignment", {"user": DA1})
		name = self._own_request()
		self._decide(SUP, name)
		self.assertTrue(
			any("waits for the next approval" in t for t in self._notified(DA1)), self._notified(DA1)
		)

	def test_a_step_on_a_request_one_may_not_see_says_nothing_about_it(self):
		name = self._own_request()
		for user in (SUP_OTHER, DA3):
			out = self._decide(user, name)
			self._error(out, 403)
			self.assertNotIn("DA-000001", out["message"], out)
			self.assertNotIn(SUP, out["message"], out)
		self.assertEqual(frappe.db.get_value(PCR, name, "status"), "Pending Supervisor")

	def test_a_da_with_no_user_falls_to_the_woreda_rule(self):
		# DA-000002 is posted in ET04-W01 and, here, has never signed in: no User, no place
		# in the hierarchy. SUP holds a User Permission on that Woreda (their grant).
		with patch.object(svc, "_user_of", return_value=None):
			frappe.set_user(SUP)
			out = self._submit({"telegram": "+251911222333"}, da_id="DA-000002")
			self.assertEqual(out["status"], "success", out)
			sent = out["data"]
			self.assertEqual(
				(sent["status"], sent["user"], sent["l1_by"]), ("Pending Administrator", None, SUP)
			)
			frappe.db.commit()  # nosemgrep
			frappe.set_user(SUP_OTHER)
			self._error(self._submit({"telegram": "+251911222444"}, da_id="DA-000002"), 403)
		# a request from before requests named the DA's User, still waiting for level 1
		frappe.set_user("Administrator")
		old = frappe.get_doc(
			{
				"doctype": PCR,
				"da_id": "DA-000002",
				"section": "Contact",
				"requested_by": DA1,
				"initiated_by_role": C.ROLE_DA,
				"changes": frappe.as_json({"email": {"from": None, "to": "old@moa.gov.et"}}),
				"region": "ET04",
				"zone": "ET04-Z01",
				"woreda": "ET04-W01",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()  # nosemgrep
		for user, sees in ((SUP, True), (ADMIN1, True), (SUP_OTHER, False), (DA3, False)):
			with as_user(user):
				rows, _total = svc.list_change_requests(da_id="DA-000002")
				self.assertEqual({r["name"] for r in rows} >= {old.name, sent["name"]}, sees, user)
		self._error(self._decide(SUP_OTHER, old.name), 403)
		self.assertEqual(self._decide(SUP, old.name)["data"]["status"], "Pending Administrator")

	def test_input_of_the_wrong_kind_is_a_400(self):
		frappe.set_user(DA1)
		frappe.db.commit()  # nosemgrep
		self._error(api.submit(section="Certificates", action="add", values=["title"]), 400)
		self._error(self._submit(dict(CONTACT), reason="r" * 501), 400)
		name = self._own_request()
		self._error(self._decide(SUP, name, workflow.REJECT, "n" * 501), 400)
		self._error(self._decide(SUP, name, workflow.REJECT, 123), 400)  # read as text: too short
		frappe.set_user(SUP)
		self._error(api.list_requests(limit="abc"), 400)

	def test_an_operation_id_belongs_to_who_used_it(self):
		frappe.set_user(DA1)
		first = self._submit(dict(CONTACT), client_op_id="op-pcr-1")["data"]
		again = self._submit(dict(CONTACT), client_op_id="op-pcr-1")["data"]
		self.assertEqual(first["name"], again["name"])  # the same call, sent twice
		frappe.db.commit()  # nosemgrep
		frappe.set_user(SUP)
		out = self._submit({"telegram": "+251911222333"}, da_id="DA-000001", client_op_id="op-pcr-1")
		self._error(out, 400)
		self.assertNotIn(first["name"], str(out))

	def test_the_person_who_gave_level_1_does_not_give_level_2(self):
		name = self._own_request()
		self.assertEqual(self._decide(SUP_ADMIN, name)["data"]["status"], "Pending Administrator")
		self._error(self._decide(SUP_ADMIN, name), 403)
		self.assertEqual(self.writers, [])
		self.assertEqual(self._decide(ADMIN1, name)["data"]["status"], "Applied")

	def test_reject_and_request_changes_need_a_note_and_end_the_request(self):
		name = self._own_request()
		self._error(self._decide(SUP, name, workflow.REJECT), 400)
		d = self._decide(SUP, name, workflow.REQUEST_CHANGES, "Send the number with the country code")["data"]
		self.assertEqual(
			(d["status"], d["l1_note"]), ("Changes requested", "Send the number with the country code")
		)
		self.assertTrue(any("Changes requested" in t for t in self._notified(DA1)))
		second = self._own_request()  # the DA sends a new request
		self.assertEqual(self._decide(SUP, second)["data"]["status"], "Pending Administrator")
		d = self._decide(ADMIN1, second, workflow.REJECT, "Not the number on record")["data"]
		self.assertEqual((d["status"], d["l2_by"]), ("Rejected", ADMIN1))
		self.assertEqual(self.writers, [])
		with as_user(DA1):
			self.assertEqual(svc.get_change_request(second)["allowed_actions"], [])

	def test_only_the_requester_cancels(self):
		name = self._own_request()
		for user in (SUP, ADMIN1):
			self._error(self._act(user, name, workflow.CANCEL), 403)
		self._decide(SUP, name)
		out = self._act(DA1, name, workflow.CANCEL)  # still theirs while it waits for the Administrator
		self.assertEqual(out["data"]["status"], "Cancelled", out)
		out = self._act(DA1, name, workflow.CANCEL)
		self.assertEqual((out["status"], out["code"]), ("error", "ALREADY_DECIDED"))
		self._error(self._decide(ADMIN1, name), 400)

	def test_a_change_the_registry_did_not_take_is_retried_or_closed(self):
		from da_services.integrations.da_registry.client import DARegistryUnavailable

		def down(client, update):
			raise DARegistryUnavailable("registry is down")

		first, second = self._own_request(), self._own_request({"telegram": "+251911222333"})
		for name in (first, second):
			self._decide(SUP, name)
		with patch.object(MockDARegistryClient, "push_profile_update", down):
			for name in (first, second):
				d = self._decide(ADMIN1, name)["data"]
				self.assertEqual((d["status"], d["registry_sync_state"]), ("Registry failed", "Unavailable"))
				self.assertEqual(d["allowed_actions"], [workflow.RETRY, workflow.CLOSE])
		self.assertEqual(self._history(first)[-1], ("Registry failed", "DA Registry"))

		for user in (SUP, DA1):  # the Workflow gives retry and close to the Administrator only
			self._error(self._act(user, first, workflow.RETRY), 403)
			self._error(self._act(user, first, workflow.CLOSE, "Not needed any more"), 403)
			self.assertEqual(svc.get_change_request(first)["allowed_actions"], [])

		self.assertEqual(self._act(ADMIN2, first, workflow.RETRY)["data"]["status"], "Applied")
		self.assertEqual(
			self._history(first)[-2:], [("Sent to registry", ADMIN2), ("Applied", "DA Registry")]
		)
		self._error(self._act(ADMIN2, second, workflow.CLOSE), 400)  # a note is required
		d = self._act(ADMIN2, second, workflow.CLOSE, "The DA will send it again")["data"]
		self.assertEqual((d["status"], d["closed_by"]), ("Closed", ADMIN2))
		self.assertEqual(self._history(second)[-1], ("Closed", ADMIN2))

	def test_each_person_is_offered_what_the_workflow_gives_them(self):
		name = self._own_request()
		offered = {}
		for user in (DA1, SUP, SUP_ADMIN, ADMIN1):
			with as_user(user):
				rows, _total = svc.list_change_requests(da_id="DA-000001")
				offered[user] = next(r for r in rows if r["name"] == name)["allowed_actions"]
		level_1 = [workflow.APPROVE, workflow.REJECT, workflow.REQUEST_CHANGES]
		self.assertEqual(offered, {DA1: [workflow.CANCEL], SUP: level_1, SUP_ADMIN: level_1, ADMIN1: []})
		for user in (SUP_OTHER, DA3):  # another team: the request is not theirs to see
			with as_user(user):
				rows, _total = svc.list_change_requests(da_id="DA-000001")
				self.assertEqual(rows, [], user)
				with self.assertRaises(frappe.PermissionError):
					svc.get_change_request(name)
		self._decide(SUP, name)
		with as_user(ADMIN1):
			row = svc.get_change_request(name)
		self.assertEqual(row["allowed_actions"], [workflow.APPROVE, workflow.REJECT])
		self.assertEqual(
			[(h["state"], h["by"]) for h in row["history"]],
			[("Pending Supervisor", DA1), ("Pending Administrator", SUP)],
		)
		with as_user(SUP):
			self.assertEqual(svc.get_change_request(name)["allowed_actions"], [])

	# -- the Workflow changed the way an administrator would in the Desk ------------------------
	# Each test edits nothing but documents of the Desk (a Workflow State, the Workflow's rows,
	# a role permission) and then runs requests through the service: no code knows the shape.

	def _change_the_workflow(self, edit, *, write_for=None):
		"""Save the site's Workflow after ``edit(workflow)``, for this test; put it back after.

		What is put back is exactly what was there (this Workflow's rows and this DocType's
		custom permissions), kept on the site until then so that a run that is killed is
		undone by the next one.
		"""
		from frappe.permissions import add_permission, update_permission_property

		frappe.set_user("Administrator")
		name = frappe.db.get_value("Workflow", {"document_type": PCR, "is_active": 1}, "name")
		w = frappe.get_doc("Workflow", name)
		frappe.db.set_global(
			WORKFLOW_BACKUP,
			frappe.as_json(
				{
					"name": name,
					"states": [{k: row.get(k) for k in STATE_ROW} for row in w.states],
					"transitions": [{k: row.get(k) for k in TRANSITION_ROW} for row in w.transitions],
					"permissions": frappe.get_all("Custom DocPerm", filters={"parent": PCR}, fields=["*"]),
				}
			),
		)
		frappe.db.commit()  # nosemgrep
		self.addCleanup(_put_the_workflow_back)  # after tearDown has removed the requests
		for doctype, field, value in (
			("Workflow State", "workflow_state_name", TEST_LEVEL),
			("Workflow Action Master", "workflow_action_name", SEND_BACK),
		):
			if not frappe.db.exists(doctype, value):
				frappe.get_doc({"doctype": doctype, field: value}).insert()
		edit(w)
		w.save()
		if write_for:  # Role Permission Manager: taking a step is a save
			add_permission(PCR, write_for, 0)
			update_permission_property(PCR, write_for, 0, "write", 1)
		frappe.cache.hdel("workflow", PCR)
		frappe.clear_cache(doctype=PCR)
		frappe.db.commit()  # nosemgrep

	@staticmethod
	def _drop(w, state):
		"""Take a state and every transition that touches it out of the Workflow."""
		w.set("states", [row for row in w.states if row.state != state])
		w.set("transitions", [t for t in w.transitions if state not in (t.state, t.next_state)])

	@staticmethod
	def _an_executive_level(w, *, after, before):
		"""A level for the Executive between two states."""
		for t in w.transitions:
			if t.state == after and t.next_state == before:
				t.next_state = TEST_LEVEL
		w.append("states", {"state": TEST_LEVEL, "doc_status": "0", "allow_edit": C.ROLE_EXECUTIVE})
		step = {"state": TEST_LEVEL, "allowed": C.ROLE_EXECUTIVE, "allow_self_approval": 0}
		w.append("transitions", {**step, "action": workflow.APPROVE, "next_state": before})
		w.append("transitions", {**step, "action": workflow.REJECT, "next_state": "Rejected"})
		w.append(
			"transitions",
			{
				"state": TEST_LEVEL,
				"action": workflow.CANCEL,
				"next_state": "Cancelled",
				"allowed": workflow.EVERYONE,
				"allow_self_approval": 1,
				"condition": workflow.IS_THE_REQUESTER,
			},
		)

	def _three_levels(self):
		self._change_the_workflow(
			lambda w: self._an_executive_level(w, after="Pending Supervisor", before="Pending Administrator"),
			write_for=C.ROLE_EXECUTIVE,
		)

	def test_a_level_added_in_the_desk_needs_no_code(self):
		self._three_levels()
		name = self._own_request()
		d = self._decide(SUP, name, note="Checked with the DA")["data"]
		self.assertEqual((d["status"], d["status_label"], d["l1_by"]), (TEST_LEVEL, TEST_LEVEL, SUP))
		# the new level is told, the last one not yet; the DA hears where the request waits
		self.assertTrue(self._notified(EXEC, "profile_change_waiting"))
		self.assertFalse(self._notified(ADMIN1, "profile_change_waiting"))
		self.assertTrue(any(TEST_LEVEL in t for t in self._notified(DA1)), self._notified(DA1))
		# it is an open request like any other: its field is taken, and it can be listed by state
		frappe.set_user(DA1)
		frappe.db.commit()  # nosemgrep
		out = self._submit(dict(CONTACT))
		self.assertEqual((out["status"], out["code"]), ("error", "OPEN_REQUEST_EXISTS"))
		with as_user(EXEC):
			rows, _total = svc.list_change_requests(status=TEST_LEVEL)
			self.assertEqual(
				[(r["name"], r["allowed_actions"]) for r in rows], [(name, ["Approve", "Reject"])]
			)
		with as_user(DA1):
			self.assertEqual(svc.get_change_request(name)["allowed_actions"], [workflow.CANCEL])

		self._error(self._decide(ADMIN1, name), 403)  # not the Administrator's turn
		self._error(self._decide(SUP, name), 403)  # nor the Supervisor's again
		self.assertEqual(self.writers, [])
		d = self._decide(EXEC, name, note="Seen by the Executive")["data"]
		self.assertEqual((d["status"], d["approver"]), ("Pending Administrator", EXEC))
		self.assertTrue(self._notified(ADMIN1, "profile_change_waiting"))

		# one person decides a request at one level: an Executive who is also an Administrator
		frappe.set_user("Administrator")
		frappe.get_doc("User", EXEC).add_roles(C.ROLE_ADMIN)
		self.addCleanup(lambda: frappe.get_doc("User", EXEC).remove_roles(C.ROLE_ADMIN))
		out = self._decide(EXEC, name)
		self._error(out, 403)
		self.assertIn("already decided", out["message"])

		d = self._decide(ADMIN1, name, note="Approved for the registry")["data"]
		self.assertEqual((d["status"], d["l2_by"]), ("Applied", ADMIN1))
		self.assertEqual([w[0] for w in self.writers], [ADMIN1])
		with as_user(DA1):
			history = svc.get_change_request(name)["history"]
		self.assertEqual(
			[(h["state"], h["action"], h["by"], h["note"]) for h in history],
			[
				("Pending Supervisor", None, DA1, None),
				(TEST_LEVEL, "Approve", SUP, "Checked with the DA"),
				("Pending Administrator", "Approve", EXEC, "Seen by the Executive"),
				("Sent to registry", "Approve", ADMIN1, "Approved for the registry"),
				("Applied", None, None, None),
			],
		)
		self.assertEqual(history[-1]["by_name"], "DA Registry")

	def test_a_supervisors_request_enters_the_chain_where_the_workflow_says(self):
		self._three_levels()
		frappe.set_user(SUP)
		out = self._submit({"position": "SENIOR_DA"}, reason="Promoted", da_id="DA-000001")
		self.assertEqual(out["status"], "success", out)
		self.assertEqual(out["data"]["status"], TEST_LEVEL)
		self.assertIn(TEST_LEVEL, out["message"])
		name = out["data"]["name"]
		self.assertTrue(self._notified(EXEC, "profile_change_waiting"))
		self.assertEqual(self._decide(EXEC, name)["data"]["status"], "Pending Administrator")
		self.assertEqual(self._decide(ADMIN1, name)["data"]["status"], "Applied")

	def test_one_level_the_administrator_alone(self):
		self._change_the_workflow(lambda w: self._drop(w, "Pending Supervisor"))
		name = self._own_request()
		row = frappe.db.get_value(PCR, name, ["status", "no_supervisor_found"], as_dict=True)
		self.assertEqual((row.status, row.no_supervisor_found), ("Pending Administrator", 0))
		self.assertTrue(self._notified(ADMIN1, "profile_change_waiting"))
		self._error(self._decide(SUP, name), 403)  # the Supervisor has no step in this Workflow
		d = self._decide(ADMIN1, name)["data"]
		self.assertEqual((d["status"], d["l2_by"]), ("Applied", ADMIN1))
		self.assertEqual([w[0] for w in self.writers], [ADMIN1])

		# a Supervisor still asks for a DA below them; with no level to skip it waits like any other
		frappe.set_user(SUP)
		out = self._submit({"position": "SENIOR_DA"}, reason="Promoted", da_id="DA-000001")
		self.assertEqual(out["status"], "success", out)
		asked = out["data"]
		self.assertEqual(
			(asked["status"], asked["initiated_by_role"]), ("Pending Administrator", C.ROLE_SUPERVISOR)
		)
		self.assertEqual(asked["allowed_actions"], [workflow.CANCEL])
		self.assertEqual(self._decide(ADMIN1, asked["name"])["data"]["status"], "Applied")
		# and an Administrator who is above the DA cannot approve what they asked for
		frappe.set_user(SUP_ADMIN)
		out = self._submit({"telegram": "+251911222333"}, da_id="DA-000001")
		self.assertEqual(out["status"], "success", out)
		self._error(self._decide(SUP_ADMIN, out["data"]["name"]), 403)
		self.assertEqual(self._decide(ADMIN2, out["data"]["name"])["data"]["status"], "Applied")

	def test_one_level_the_supervisor_alone(self):
		def only_the_supervisor(w):
			self._drop(w, "Pending Administrator")  # with it go the steps into it (Approve, Submit for DA)
			w.append(
				"transitions",
				{
					"state": "Pending Supervisor",
					"action": workflow.APPROVE,
					"next_state": "Sent to registry",
					"allowed": C.ROLE_SUPERVISOR,
					"allow_self_approval": 0,
					"condition": workflow.ABOVE_THE_DA,
				},
			)

		self._change_the_workflow(only_the_supervisor)
		name = self._own_request()
		# giving the last approval does not open every request to the role: the hierarchy still decides
		for user in (SUP_OTHER, SUP2):
			with as_user(user):
				rows, _total = svc.list_change_requests()
				self.assertNotIn(name, [r["name"] for r in rows], user)
			self._error(self._decide(user, name), 403)
		d = self._decide(SUP, name)["data"]
		self.assertEqual((d["status"], d["l1_by"]), ("Applied", SUP))
		self.assertEqual([w[0] for w in self.writers], [SUP])  # sent with the approver's own sign-in
		self.assertEqual(svc._last_approver(frappe.get_doc(PCR, name)), SUP)

		# asked by the Supervisor: it does not go from its sender to the registry; someone above decides
		frappe.set_user(SUP)
		out = self._submit({"telegram": "+251911222333"}, da_id="DA-000001")
		self.assertEqual(out["status"], "success", out)
		asked = out["data"]["name"]
		self.assertEqual(out["data"]["status"], "Pending Supervisor")
		self._error(self._decide(SUP, asked), 403)
		self.assertEqual(len(self.writers), 1)
		self.assertEqual(self._decide(SUP_ADMIN, asked)["data"]["status"], "Applied")

	def test_a_level_after_the_administrator_and_a_send_back(self):
		def edit(w):
			self._an_executive_level(w, after="Pending Administrator", before="Sent to registry")
			w.append(
				"transitions",
				{
					"state": TEST_LEVEL,
					"action": SEND_BACK,
					"next_state": "Pending Administrator",
					"allowed": C.ROLE_EXECUTIVE,
					"allow_self_approval": 0,
				},
			)

		self._change_the_workflow(edit, write_for=C.ROLE_EXECUTIVE)
		name = self._own_request()
		self._decide(SUP, name)
		self.assertEqual(self._decide(ADMIN1, name)["data"]["status"], TEST_LEVEL)
		self.assertEqual(self.writers, [])  # the Administrator's is no longer the last approval
		d = self._act(EXEC, name, SEND_BACK, "Please check the number again")["data"]
		self.assertEqual((d["status"], d["allowed_actions"]), ("Pending Administrator", []))
		self.assertTrue(any("Pending" in t or "Waiting" in t for t in self._notified(DA1)))
		# back at their own level the Administrator decides again; the Executive still cannot there
		frappe.set_user("Administrator")
		frappe.get_doc("User", EXEC).add_roles(C.ROLE_ADMIN)
		self.addCleanup(lambda: frappe.get_doc("User", EXEC).remove_roles(C.ROLE_ADMIN))
		self._error(self._decide(EXEC, name), 403)
		self.assertEqual(self._decide(ADMIN1, name)["data"]["status"], TEST_LEVEL)
		d = self._decide(EXEC, name)["data"]
		self.assertEqual(d["status"], "Applied")
		self.assertEqual([w[0] for w in self.writers], [EXEC])
		doc = frappe.get_doc(PCR, name)
		self.assertEqual(svc._last_approver(doc), EXEC)  # not l2_by, who is the Administrator
		self.assertEqual(doc.l2_by, ADMIN1)
		self.assertEqual(
			[(s["action"], s["to"]) for s in workflow.steps(doc)][-4:],
			[
				(SEND_BACK, "Pending Administrator"),
				("Approve", TEST_LEVEL),
				("Approve", "Sent to registry"),
				(None, "Applied"),
			],
		)

	def test_a_workflow_the_app_could_not_run_safely_is_refused_at_save(self):
		frappe.set_user("Administrator")
		name = frappe.db.get_value("Workflow", {"document_type": PCR, "is_active": 1}, "name")

		def row(w, state, action):
			return next(t for t in w.transitions if (t.state, t.action) == (state, action))

		def tick_a_decision(w):  # what the Desk does by default on a new row
			row(w, "Pending Administrator", workflow.APPROVE).allow_self_approval = 1

		def untick_cancel(w):
			row(w, "Pending Supervisor", workflow.CANCEL).allow_self_approval = 0

		def a_button_to_applied(w):
			row(w, "Pending Administrator", workflow.REJECT).next_state = "Applied"

		def a_shortcut_to_the_registry(w):
			row(w, "Pending Supervisor", workflow.SUBMIT_FOR_DA).next_state = "Sent to registry"

		def no_last_approval(w):
			row(w, "Pending Administrator", workflow.APPROVE).next_state = "Rejected"

		def no_system_state(w):
			w.set("states", [s for s in w.states if s.state != "Registry failed"])
			w.set("transitions", [t for t in w.transitions if t.state != "Registry failed"])

		def cancel_into_the_registry(w):
			row(w, "Pending Supervisor", workflow.CANCEL).next_state = "Sent to registry"

		def a_decision_for_everyone(w):
			row(w, "Pending Administrator", workflow.REJECT).allowed = workflow.EVERYONE

		def failures_for_a_hierarchy_role(w):
			row(w, "Registry failed", workflow.CLOSE).allowed = C.ROLE_SUPERVISOR

		def one_action_two_ways(w):
			w.append(
				"transitions",
				{
					"state": "Pending Administrator",
					"action": workflow.REJECT,
					"next_state": "Changes requested",
					"allowed": C.ROLE_ADMIN,
					"allow_self_approval": 0,
				},
			)

		def drop_a_state_that_holds_requests(w):
			self._drop(w, "Pending Supervisor")

		def a_task_inside_a_step(w):
			tasks = frappe.get_doc({"doctype": "Workflow Transition Tasks", "tasks": []})
			tasks.insert(set_name="PCR test tasks")  # rolled back with the refused save
			row(w, "Pending Administrator", workflow.APPROVE).transition_tasks = tasks.name

		def a_state_that_says_who_decided(w):
			next(row for row in w.states if row.state == "Pending Administrator").update(
				{"update_field": "l2_by", "update_value": "frappe.session.user", "evaluate_as_expression": 1}
			)

		waiting = self._own_request()  # a request at Pending Supervisor
		self.assertEqual(frappe.db.get_value(PCR, waiting, "status"), "Pending Supervisor")
		frappe.set_user("Administrator")

		for edit, said in (
			(tick_a_decision, "untick Allow Self Approval"),
			(untick_cancel, "tick Allow Self Approval"),
			(a_button_to_applied, "written by the system"),
			(a_shortcut_to_the_registry, "second person"),
			(no_last_approval, "could never be approved"),
			(no_system_state, "must stay"),
			(cancel_into_the_registry, "requester's own step cannot lead"),
			(a_decision_for_everyone, "role All is for the requester"),
			(failures_for_a_hierarchy_role, "sees every request"),
			(one_action_two_ways, "more than one state"),
			(drop_a_state_that_holds_requests, "finish or move them"),
			(a_state_that_says_who_decided, "clear Update Field"),
			(a_task_inside_a_step, "clear Transition Tasks"),
		):
			w = frappe.get_doc("Workflow", name)
			edit(w)
			with self.assertRaises(frappe.ValidationError, msg=said) as ctx:
				w.save()
			self.assertIn(said, str(ctx.exception))
			frappe.db.rollback()
		workflow.validate_shape(frappe.get_doc(workflow.definition()))  # the app's own passes

	# -- what is sent is what was approved; nothing stays half sent ---------------------------------

	def test_the_file_sent_to_the_registry_is_the_one_the_approvers_saw(self):
		uploaded = []
		original = MockDARegistryClient.upload_document

		def recording_upload(client, filename, content, content_type):
			uploaded.append((filename, content))
			return original(client, filename, content, content_type)

		with as_user(DA1):
			sent = svc.submit_certificate(record_id="qual-000001-1", filename="a.pdf", content=PDF, title="A")
		name = sent["name"]
		self.assertTrue(sent["changes"]["certificates:qual-000001-1"]["to"]["sha256"])
		# whoever may save the request may attach another file to it in Frappe, after the look
		self._decide(SUP, name)
		with as_user(DA1):
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": "other.png",
					"attached_to_doctype": PCR,
					"attached_to_name": name,
					"is_private": 1,
					"content": PNG,
				}
			).insert(ignore_permissions=True)
		with as_user(ADMIN1):
			self.assertEqual(svc.request_file(name)[1], PDF)  # what the approver is shown
		with patch.object(MockDARegistryClient, "upload_document", recording_upload):
			self.assertEqual(self._decide(ADMIN1, name)["data"]["status"], "Applied")
		self.assertEqual([content for _filename, content in uploaded], [PDF])  # and what is sent

	def test_a_file_frappe_rewrites_when_it_stores_it_is_still_shown_and_sent(self):
		jpeg, uploaded = _jpeg_with_exif(), []
		original = MockDARegistryClient.upload_document

		def recording_upload(client, filename, content, content_type):
			uploaded.append(content)
			return original(client, filename, content, content_type)

		with as_user(DA1):
			certificate = svc.submit_certificate(
				record_id="qual-000001-1", filename="scan.jpg", content=jpeg, title="A scan"
			)["name"]
			photo = svc.submit_photo(filename="me.jpg", content=jpeg)["name"]
		shown = {}
		for name in (certificate, photo):
			self._decide(SUP, name)
			with as_user(ADMIN1):
				shown[name] = svc.request_file(name)[1]
			self.assertTrue(shown[name])
		self.assertNotEqual(shown[photo], jpeg)  # stored without the EXIF data: not the uploaded bytes
		with patch.object(MockDARegistryClient, "upload_document", recording_upload):
			for name in (certificate, photo):
				self.assertEqual(self._decide(ADMIN1, name)["data"]["status"], "Applied", name)
		self.assertEqual(uploaded, [shown[certificate], shown[photo]])  # what the approvers saw

	def test_open_requests_from_before_fingerprints_are_pinned_once(self):
		from da_services.services import profile_lists

		with as_user(DA1):
			name = svc.submit_certificate(
				record_id="qual-000001-1", filename="a.pdf", content=PDF, title="A"
			)["name"]
		doc = frappe.get_doc(PCR, name)
		changes = frappe.parse_json(frappe.as_json(svc._changes(doc)))
		next(iter(changes.values()))["to"].pop("sha256")  # as such a request was stored before
		doc.db_set("changes", frappe.as_json(changes))
		with as_user(SUP), self.assertRaises(frappe.DoesNotExistError):
			svc.request_file(name)  # no fingerprint: nothing is shown, and nothing would be sent
		self.assertEqual(profile_lists.pin_open_requests(), 1)  # what migrate runs
		with as_user(SUP):
			self.assertEqual(svc.request_file(name)[1], PDF)
		self.assertEqual(profile_lists.pin_open_requests(), 0)

	def _an_older_request(self, **values):
		"""A request as rows were before steps were recorded on them: no ``decisions``."""
		frappe.set_user("Administrator")
		doc = frappe.get_doc(
			{
				"doctype": PCR,
				"da_id": "DA-000001",
				"user": DA1,
				"section": "Contact",
				"requested_by": DA1,
				"initiated_by_role": C.ROLE_DA,
				"changes": frappe.as_json({"telegram": {"from": None, "to": "+251911222333"}}),
				"region": "ET04",
				"zone": "ET04-Z01",
				"woreda": "ET04-W01",
			}
		).insert(ignore_permissions=True)
		doc.db_set({"owner": DA1, **values}, update_modified=False)
		frappe.db.commit()  # nosemgrep
		return doc.name

	def test_a_request_approved_before_steps_were_recorded_can_still_be_retried(self):
		name = self._an_older_request(
			status="Registry failed", l1_by=SUP, l2_by=ADMIN1, registry_sync_state="Unavailable"
		)
		self.assertEqual(self._act(ADMIN2, name, workflow.RETRY)["data"]["status"], "Applied")
		self.assertEqual([w[0] for w in self.writers], [ADMIN2])
		# and one that nobody else approved is not sent, by whatever way it came to be failed
		unapproved = self._an_older_request(status="Registry failed", registry_sync_state="Unavailable")
		self._error(self._act(ADMIN2, unapproved, workflow.RETRY), 403)
		self.assertEqual(len(self.writers), 1)

	def test_an_approved_request_that_never_got_an_answer_becomes_a_failed_one(self):
		from frappe.utils import add_to_date, now_datetime

		long_ago = add_to_date(now_datetime(), minutes=-30)
		died = self._an_older_request(status="Sent to registry", l2_by=ADMIN1, last_push_at=long_ago)
		in_flight = self._an_older_request(
			status="Sent to registry", l2_by=ADMIN1, last_push_at=now_datetime()
		)
		with as_user(DA1):
			self.assertEqual(
				svc.get_change_request(in_flight)["status"], "Sent to registry"
			)  # being sent now
		svc.fail_unanswered()
		self.assertEqual(
			frappe.db.get_value(PCR, died, ["status", "registry_sync_state"]),
			("Registry failed", "Unavailable"),
		)
		self.assertEqual(frappe.db.get_value(PCR, in_flight, "status"), "Sent to registry")
		with as_user(ADMIN2):
			self.assertEqual(
				svc.get_change_request(died)["allowed_actions"], [workflow.RETRY, workflow.CLOSE]
			)

	def test_a_supervisor_cannot_request_outside_their_team(self):
		frappe.set_user(SUP)
		self._error(self._submit({"mobile": "+251911999777"}, da_id="DA-000004"), 403)
		self._error(self._submit({"mobile": "+251911999777"}), 404)  # own record: this user has none

	# -- the registry's answer ---------------------------------------------------------------

	def test_the_same_step_on_several_requests(self):
		first, second = self._own_request(), self._own_request({"telegram": "+251911222333"})
		frappe.set_user(SUP)
		frappe.db.commit()  # nosemgrep
		out = api.act(request_ids=json_ids([first, second, "DA-PCR-0000-0000000"]), action=workflow.APPROVE)
		self.assertEqual((out["data"]["done"], out["data"]["failed"]), (2, 1), out)
		self.assertEqual(
			[(r["ok"], r["status"], r["code"]) for r in out["data"]["results"]],
			[(True, "Pending Administrator", None)] * 2 + [(False, None, "NOT_FOUND")],
		)
		self._error(api.act(action=workflow.APPROVE), 400)  # no request named
		self._error(self._act(SUP, first, "Escalate"), 400)  # not a step of the Workflow

	def test_the_action_route_is_not_taken_for_a_request_id(self):
		from da_services.api.router import ensure_routes_registered

		ensure_routes_registered()
		adapter = frappe.api.API_URL_MAP.bind("localhost")
		endpoint, _args = adapter.match("/api/v1/da-services/profile/change-requests/action", method="POST")
		self.assertIn("act", str(endpoint))

	def test_what_may_be_asked_for_own_record_and_for_a_da_below(self):
		frappe.set_user(DA1)
		own = api.editable_fields()["data"]
		self.assertEqual(own["da_id"], "DA-000001")
		self.assertEqual(own["locked_fields"], sorted(profile_fields.SUPERVISOR_ONLY_FIELDS))
		self.assertNotIn("fayda_id", own["editable_fields"] + own["locked_fields"])
		self.assertEqual(sorted(own["fields"]), own["editable_fields"])
		self.assertEqual(own["fields"]["mobile"]["current"], "+251911000001")
		self.assertIn("education_level", own["list_options"])
		frappe.set_user(SUP)
		for_da = api.editable_fields(da_id="DA-000001")["data"]
		self.assertEqual(for_da["locked_fields"], [])
		self.assertIn("position", for_da["editable_fields"])
		frappe.db.commit()  # nosemgrep
		self._error(api.editable_fields(da_id="DA-000004"), 403)  # not in this Supervisor's team

	def test_the_section_named_must_be_the_one_the_fields_are_in(self):
		frappe.set_user(DA1)
		frappe.db.commit()  # nosemgrep
		self._error(api.submit(section="Identity", values=dict(CONTACT)), 400)
		self._error(api.submit(section="Salary", values=dict(CONTACT)), 400)
		self._error(api.submit(values=dict(CONTACT)), 400)
		self._error(api.submit(section="Photo", action="add"), 400)  # no file
		self._error(api.submit(section="Certificates", action="update"), 400)

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
		self._error(self._submit(dict(CONTACT), reason="New SIM", da_id="DA-000001"), 403)
		frappe.set_user(ADMIN1)
		self.assertFalse(api.agent_profile("DA-000001")["data"]["can_request"])  # approves, never asks

	# -- the Desk's buttons and the log ------------------------------------------------------

	def _press(self, user, name, action):
		"""The Desk form's Workflow button: Frappe's method, as hooks.py routes it."""
		frappe.set_user(user)
		frappe.db.commit()  # nosemgrep: a refused call rolls back what is not committed
		return desk.apply_workflow(frappe.get_doc(PCR, name).as_json(), action)

	def test_a_desk_button_takes_the_whole_step(self):
		self.assertEqual(
			frappe.get_hooks("override_whitelisted_methods")["frappe.model.workflow.apply_workflow"],
			["da_services.workflows.desk.apply_workflow"],
		)
		name = self._own_request()
		for user in (DA1, ADMIN1):  # own request; level 2 does not skip level 1
			with self.assertRaises(frappe.PermissionError):
				self._press(user, name, workflow.APPROVE)
		self.assertEqual(self._press(SUP, name, workflow.APPROVE).status, "Pending Administrator")
		self.assertTrue(self._notified(ADMIN1, "profile_change_waiting"))
		self.assertEqual(self.writers, [])
		doc = self._press(ADMIN1, name, workflow.APPROVE)
		self.assertEqual((doc.status, doc.l2_by), ("Applied", ADMIN1))
		self.assertEqual([w[0] for w in self.writers], [ADMIN1])  # the registry was written to
		self.assertEqual(MockDARegistryClient.shared().get_da("DA-000001").phone, CONTACT["mobile"])
		self.assertEqual(
			self._history(name),
			[("Pending Administrator", SUP), ("Sent to registry", ADMIN1), ("Applied", "DA Registry")],
		)

	def test_a_session_the_registry_would_not_know_cannot_send_to_it(self):
		name = self._own_request()

		def no_sign_in(client):
			raise DARegistryAuthError("Sign in through the staff IAM to use the DA Registry")

		with patch.object(MockDARegistryClient, "check_sign_in", no_sign_in):
			self.assertEqual(self._press(SUP, name, workflow.APPROVE).status, "Pending Administrator")
			with self.assertRaises(frappe.PermissionError):  # the Desk: nothing moves, nothing is sent
				self._press(ADMIN1, name, workflow.APPROVE)
			self._error(self._decide(ADMIN1, name), 403)  # the API with a sign-in of the wrong kind
			self.assertEqual(frappe.db.get_value(PCR, name, "status"), "Pending Administrator")
			self.assertEqual(self.writers, [])
			doc = frappe.get_doc(PCR, name)
			doc.step_note = "Sent back from the Desk"
			doc.save()
			self.assertEqual(
				self._press(ADMIN1, name, workflow.REJECT).status, "Rejected"
			)  # no registry call

	def test_a_desk_step_takes_its_note_from_the_saved_form(self):
		name = self._own_request()
		with self.assertRaises(frappe.ValidationError):  # a rejection needs a note
			self._press(SUP, name, workflow.REJECT)
		frappe.set_user(DA1)
		doc = frappe.get_doc(PCR, name)
		doc.step_note = "Documents checked in person"
		doc.save()
		with self.assertRaises(frappe.ValidationError):  # a note the DA left is not the Supervisor's
			self._press(SUP, name, workflow.REJECT)
		frappe.set_user(SUP)
		doc = frappe.get_doc(PCR, name)
		doc.reason = "Not what the DA wrote"
		with self.assertRaises(frappe.ValidationError):  # the form saves the note and nothing else
			doc.save()
		doc = frappe.get_doc(PCR, name)
		doc.step_note = "The number is not the DA's"
		doc.save()
		form = frappe.get_doc(PCR, name).as_dict()
		form["step_note"] = "Typed in the form and never saved"
		frappe.db.commit()  # nosemgrep
		doc = desk.apply_workflow(frappe.as_json(form), workflow.REJECT)
		self.assertEqual((doc.status, doc.step_note), ("Rejected", None))
		self.assertEqual(workflow.steps(doc)[-1]["note"], "The number is not the DA's")

	def test_the_desk_list_takes_a_step_on_several_requests(self):
		first, second = self._own_request(), self._own_request({"telegram": "+251911222333"})
		frappe.set_user(SUP)
		frappe.db.commit()  # nosemgrep
		desk.bulk_workflow_approval(json_ids([first, second]), PCR, workflow.APPROVE)
		self.assertEqual(
			{frappe.db.get_value(PCR, n, "status") for n in (first, second)}, {"Pending Administrator"}
		)
		frappe.clear_messages()

	def test_other_doctypes_keep_frappes_own_workflow_calls(self):
		with (
			patch("frappe.model.workflow.apply_workflow", return_value="single") as single,
			patch("frappe.model.workflow.bulk_workflow_approval", return_value="bulk") as bulk,
		):
			self.assertEqual(desk.apply_workflow('{"doctype": "ToDo", "name": "x"}', "Approve"), "single")
			self.assertEqual(desk.bulk_workflow_approval('["x"]', "ToDo", "Approve"), "bulk")
		single.assert_called_once()
		bulk.assert_called_once()

	def test_the_log_lists_every_step_newest_first(self):
		name = self._own_request(reason="New SIM")
		self._decide(SUP, name)
		self._decide(ADMIN1, name, note="Checked")
		other = self._own_request({"telegram": "+251911222333"})
		self._decide(SUP, other, workflow.REJECT, "Not yours")

		frappe.set_user(ADMIN1)
		out = api.change_logs(da_id="DA-000001")
		self.assertEqual(out["status"], "success", out)
		self.assertEqual(out["meta"]["total"], 2)
		lines = out["data"]
		self.assertEqual(lines, sorted(lines, key=lambda line: line["at"], reverse=True))
		self.assertEqual(
			[(x["state"], x["action"], x["by"], x["note"]) for x in reversed(lines) if x["request"] == name],
			[
				("Pending Supervisor", None, DA1, "New SIM"),
				("Pending Administrator", workflow.APPROVE, SUP, None),
				("Sent to registry", workflow.APPROVE, ADMIN1, "Checked"),
				("Applied", None, None, None),
			],
		)
		applied = next(x for x in lines if x["request"] == name and x["state"] == "Applied")
		self.assertEqual((applied["by_name"], applied["section"]), ("DA Registry", "Contact"))
		self.assertEqual(applied["changes"]["mobile"]["to"], CONTACT["mobile"])
		rejected = next(x for x in lines if x["request"] == other and x["action"] == workflow.REJECT)
		self.assertEqual(
			(rejected["state"], rejected["by"], rejected["note"]), ("Rejected", SUP, "Not yours")
		)

		meta = api.change_logs(da_id="DA-000001", limit="1")["meta"]
		self.assertEqual((meta["total"], meta["limit"], meta["start"]), (2, 1, 0))
		self.assertEqual(
			{x["request"] for x in api.change_logs(da_id="DA-000001", limit="1")["data"]}, {other}
		)
		self.assertEqual(api.change_logs(section="Education")["data"], [])
		self._error(api.change_logs(limit="many"), 400)
		for user, sees in ((DA1, {name, other}), (SUP, {name, other}), (DA3, set()), (SUP_OTHER, set())):
			frappe.set_user(user)
			self.assertEqual({x["request"] for x in api.change_logs()["data"]}, sees, user)

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
