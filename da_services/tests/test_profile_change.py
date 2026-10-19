"""DA self-service profile: change request → Supervisor decision → Registry (FSD 3.7)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.api.v1 import profile_self as api
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import constants as C
from da_services.services import profile as svc
from da_services.utils.permissions import get_scope_context

DA1 = "pcr-da1@da.local"  # DA-000001, ET04 / ET04-W01
DA3 = "pcr-da3@da.local"  # DA-000004, ET03 / ET03-W02
SUP = "pcr-sup@da.local"  # Supervisor ET04-W01
SUP_OTHER = "pcr-sup3@da.local"  # Supervisor ET03-W02
EXEC = "pcr-exec@da.local"  # Executive ET04
DOCTYPES = ("DA Notification", "DA Profile Change Request", "DA RBAC Assignment")


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


class TestProfileChangeRequest(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for dt in DOCTYPES:
			frappe.db.delete(dt)
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
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		frappe.local.request = None

	def tearDown(self):
		frappe.set_user("Administrator")
		for dt in DOCTYPES:
			frappe.db.delete(dt)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		MockDARegistryClient.reset()

	def _submit(self, user=DA1, **kw):
		frappe.set_user(user)
		body = {"changes": {"mobile": "+251911999888", "email": "abebe@moa.gov.et"}, "reason": "New SIM"}
		body.update(kw)
		return api.submit(**body)

	# -- view -----------------------------------------------------------------------------

	def test_my_profile_shows_registry_values_and_editable_fields(self):
		frappe.set_user(DA1)
		out = api.my_profile()
		self.assertEqual(out["status"], "success", out)
		d = out["data"]
		self.assertEqual((d["profile"]["da_id"], d["profile"]["mobile"]), ("DA-000001", "+251911000001"))
		self.assertIn("mobile", d["editable_fields"])
		self.assertIn("gender", d["locked_fields"])
		self.assertEqual(d["pending_requests"], [])

	# -- submit ---------------------------------------------------------------------------

	def test_submit_records_diff_scope_and_is_idempotent(self):
		out = self._submit(client_op_id="pcr-op-1")
		self.assertEqual(out["status"], "success", out)
		r = out["data"]
		self.assertEqual(
			(r["status"], r["section"], r["da_id"], r["woreda"]),
			("Pending", "Contact", "DA-000001", "ET04-W01"),
		)
		self.assertEqual(r["changes"]["mobile"], {"from": "+251911000001", "to": "+251911999888"})
		self.assertEqual(r["requested_by"], DA1)
		again = self._submit(client_op_id="pcr-op-1")
		self.assertEqual(again["data"]["name"], r["name"])
		self.assertEqual(frappe.db.count("DA Profile Change Request"), 1)
		# shows on the profile while pending; registry unchanged
		frappe.set_user(DA1)
		d = api.my_profile()["data"]
		self.assertEqual([p["name"] for p in d["pending_requests"]], [r["name"]])
		self.assertEqual(d["profile"]["mobile"], "+251911000001")

	def test_submit_validation(self):
		frappe.set_user(DA1)
		frappe.db.commit()  # nosemgrep: error calls below roll back uncommitted rows
		cases = [
			{"changes": {"gender": "F"}},  # locked (Fayda)
			{"changes": {"salary": "1"}},  # unknown
			{"changes": {}},  # empty
			{"changes": {"email": "not-an-email"}},
			{"changes": {"language": "fr"}},
			{"changes": {"education_tier": "PhD"}},
			{"changes": {"mobile": "+251911999888", "specialisation": "Livestock"}},  # two sections
			{"changes": {"mobile": "+251911000001"}},  # same as current
		]
		for body in cases:
			out = api.submit(**body)
			self.assertEqual(out["status"], "error", body)
			self.assertEqual(frappe.response.get("http_status_code"), 400, body)
		self.assertEqual(frappe.db.count("DA Profile Change Request"), 0)

	def test_one_open_request_per_section_and_cancel(self):
		first = self._submit()["data"]
		frappe.db.commit()  # nosemgrep
		self.assertEqual(self._submit(changes={"language": "am"})["status"], "error")  # Contact still open
		ok = self._submit(changes={"specialisation": "Livestock"})  # other section is fine
		self.assertEqual(ok["status"], "success", ok)
		frappe.set_user(DA1)
		out = api.cancel(first["name"])
		self.assertEqual(out["data"]["status"], "Cancelled")
		self.assertEqual(self._submit(changes={"language": "am"})["status"], "success")

	def test_only_a_da_can_submit(self):
		frappe.set_user(SUP)
		self.assertEqual(api.submit(changes={"mobile": "+251911999888"})["status"], "error")
		self.assertEqual(frappe.response.get("http_status_code"), 403)

	# -- decide ---------------------------------------------------------------------------

	def test_supervisor_approves_and_registry_is_updated(self):
		r = self._submit()["data"]
		frappe.set_user(SUP)
		queue = api.list_requests(status="Pending")["data"]
		self.assertEqual([q["name"] for q in queue], [r["name"]])
		out = api.decide(r["name"], decision="Approve")
		self.assertEqual(out["status"], "success", out)
		d = out["data"]
		self.assertEqual((d["status"], d["registry_sync_state"], d["approver"]), ("Applied", "Accepted", SUP))
		self.assertTrue(d["registry_reference"].startswith("MOCK-"))
		ref = MockDARegistryClient.shared().get_da("DA-000001")
		self.assertEqual((ref.phone, ref.email), ("+251911999888", "abebe@moa.gov.et"))
		self.assertEqual(
			frappe.db.count("DA Notification", {"user": DA1, "event": "profile_change_decided"}), 1
		)
		frappe.set_user(DA1)
		self.assertEqual(api.my_profile()["data"]["profile"]["mobile"], "+251911999888")

	def test_reject_and_request_changes_need_a_note(self):
		r = self._submit()["data"]
		frappe.set_user(SUP)
		frappe.db.commit()  # nosemgrep
		self.assertEqual(api.decide(r["name"], decision="Reject")["status"], "error")
		self.assertEqual(api.decide(r["name"], decision="Maybe", note="whatever")["status"], "error")
		out = api.decide(r["name"], decision="Request changes", note="Attach the new SIM receipt")
		self.assertEqual(out["data"]["status"], "Changes requested")
		self.assertEqual(MockDARegistryClient.shared().get_da("DA-000001").phone, "+251911000001")
		frappe.db.commit()  # nosemgrep
		self.assertEqual(api.decide(r["name"], decision="Approve")["status"], "error")  # already decided

	def test_scope_on_decide_and_read(self):
		r = self._submit()["data"]
		frappe.db.commit()  # nosemgrep
		frappe.set_user(SUP_OTHER)  # other Woreda
		self.assertEqual(api.decide(r["name"], decision="Approve")["status"], "error")
		self.assertEqual(frappe.response.get("http_status_code"), 403)
		self.assertEqual(api.list_requests()["data"], [])
		self.assertEqual(api.get_request(r["name"])["status"], "error")
		frappe.set_user(EXEC)  # Region read, no decide
		self.assertEqual(api.get_request(r["name"])["status"], "success")
		self.assertEqual(api.decide(r["name"], decision="Approve")["status"], "error")
		frappe.set_user(DA3)  # another DA
		self.assertEqual(api.get_request(r["name"])["status"], "error")
		self.assertEqual(api.cancel(r["name"])["status"], "error")

	def test_registry_failure_keeps_the_approval(self):
		from unittest.mock import patch

		from da_services.integrations.da_registry.client import DARegistryUnavailable

		r = self._submit()["data"]
		frappe.set_user(SUP)
		with patch.object(
			MockDARegistryClient, "push_profile_update", side_effect=DARegistryUnavailable("down")
		):
			out = api.decide(r["name"], decision="Approve")
		d = out["data"]
		self.assertEqual((d["status"], d["registry_sync_state"]), ("Approved", "Failed"))
		self.assertIn("DARegistryUnavailable", d["registry_error"])

	def test_rows_cannot_be_deleted_or_edited_through_desk(self):
		r = (
			svc.submit_change_request(changes={"language": "am"}, ctx=get_scope_context(DA1))
			if frappe.set_user(DA1) is None
			else None
		)
		doc = frappe.get_doc("DA Profile Change Request", r["name"])
		self.assertFalse(frappe.has_permission("DA Profile Change Request", "write", doc, user=SUP))
		self.assertTrue(frappe.has_permission("DA Profile Change Request", "read", doc, user=SUP))
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("DA Profile Change Request", r["name"], ignore_permissions=True)
