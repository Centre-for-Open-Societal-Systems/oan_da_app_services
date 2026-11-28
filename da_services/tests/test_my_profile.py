"""GET /profile/my_profile: the signed-in DA's own profile, found by User.da_registry_id.

The caller is never asked which DA they are: the id comes from their User (IAM fills it
from the ``da_id`` claim), so these tests give a user an id and check what they can reach.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from da_services.api.v1 import profile_self as api
from da_services.integrations.da_registry.client import DARegistryNotConfigured
from da_services.integrations.da_registry.mapper import mock_record_id
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import constants as C
from da_services.services import profile as svc
from da_services.tests.fixtures import as_user, ensure_doctype_schema, set_da_registry_id

DA = "myprofile-da@da.local"  # DA-000001, found by the registry's record id
DA_BY_DA_ID = "myprofile-da4@da.local"  # DA-000004, found by DA-ID
NOT_A_DA = "myprofile-admin@da.local"  # no da_registry_id
UNKNOWN = "myprofile-unknown@da.local"  # an id the registry does not know
USERS = (DA, DA_BY_DA_ID, NOT_A_DA, UNKNOWN)


def _user(email: str, role: str) -> None:
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = email.split("@")[0]
		u.send_welcome_email = 0
		u.insert(ignore_permissions=True, ignore_links=True)
	if role not in frappe.get_roles(email):
		frappe.get_doc("User", email).add_roles(role)


class TestMyProfile(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		ensure_doctype_schema("DA Profile Change Request")
		MockDARegistryClient.reset()
		for email in (DA, DA_BY_DA_ID, UNKNOWN):
			_user(email, C.ROLE_DA)
		_user(NOT_A_DA, C.ROLE_ADMIN)
		set_da_registry_id(DA, mock_record_id("DA-000001"))
		set_da_registry_id(DA_BY_DA_ID, "DA-000004")
		set_da_registry_id(UNKNOWN, "00000000-0000-0000-0000-000000000000")
		frappe.db.set_value("User", NOT_A_DA, "da_registry_id", None, update_modified=False)

	def tearDown(self):
		frappe.set_user("Administrator")
		for email in USERS:
			frappe.db.set_value("User", email, "da_registry_id", None, update_modified=False)

	def test_own_profile_by_record_id(self):
		with as_user(DA):
			d = api.my_profile()["data"]
		p = d["profile"]
		self.assertEqual(p["da_id"], "DA-000001")
		self.assertEqual(p["internal_record_id"], mock_record_id("DA-000001"))
		self.assertEqual(
			d["validation"], {"record_found": True, "record_status": "ACTIVE", "is_active": True}
		)
		# the record, the open requests and the validation: what a DA may edit is not here
		self.assertEqual(set(d), {"profile", "pending_requests", "validation"})

	def test_own_profile_by_da_id(self):
		"""A User whose id was set by hand to the DA-ID is found by it."""
		with as_user(DA_BY_DA_ID):
			self.assertEqual(api.my_profile()["data"]["profile"]["da_id"], "DA-000004")

	def test_family_members_and_the_earlier_name(self):
		with as_user(DA):
			p = api.my_profile()["data"]["profile"]
		self.assertTrue(p["family_members"])
		self.assertEqual(
			set(p["family_members"][0]), {"id", "name", "relationship", "relationship_label", "date_of_birth"}
		)
		self.assertEqual(p["dependents"], p["family_members"])

	def test_certificates_carry_a_name_and_a_document_id_but_no_link(self):
		"""The link is asked for when the DA opens one (GET /profile/documents/<id>)."""
		with as_user(DA):
			p = api.my_profile()["data"]["profile"]
		certificates = [f for q in p["qualifications"] for f in q["certificates"]]
		self.assertTrue(certificates)
		for f in certificates:
			self.assertTrue(f["document_id"])
			self.assertTrue(f["name"])
			self.assertNotIn("url", f)

	def test_the_registry_is_asked_once(self):
		"""One read of the record by its id: no search, no document links, no second record
		(the person the DA reports to is their DA-ID only)."""
		with (
			patch.object(
				MockDARegistryClient,
				"get_da_by_record_id",
				autospec=True,
				side_effect=MockDARegistryClient.get_da_by_record_id,
			) as by_id,
			patch.object(MockDARegistryClient, "get_da") as by_da_id,
			patch.object(MockDARegistryClient, "document_links") as links,
			patch.object(MockDARegistryClient, "change_request_status") as status,
			as_user(DA),
		):
			p = api.my_profile()["data"]["profile"]
		self.assertEqual(by_id.call_count, 1)
		by_da_id.assert_not_called()
		links.assert_not_called()
		status.assert_not_called()
		self.assertNotIn("reports_to_name", p)
		self.assertIn("reports_to", p)

	def test_a_user_who_is_not_a_da_gets_404(self):
		with as_user(NOT_A_DA), self.assertRaises(frappe.DoesNotExistError) as ctx:
			svc.my_profile()
		self.assertIn("not linked", str(ctx.exception))

	def test_an_id_the_registry_does_not_know_gets_404(self):
		with as_user(UNKNOWN), self.assertRaises(frappe.DoesNotExistError) as ctx:
			svc.my_profile()
		self.assertIn("not found in the DA Registry", str(ctx.exception))

	def test_a_record_out_of_service_is_shown_and_flagged(self):
		MockDARegistryClient.shared()._records["DA-000001"]["active_status"] = C.ACTIVE_STATUS_SEPARATED
		with as_user(DA):
			d = api.my_profile()["data"]
		self.assertEqual(d["profile"]["da_id"], "DA-000001")
		self.assertFalse(d["validation"]["is_active"])
		self.assertNotEqual(d["validation"]["record_status"], "ACTIVE")

	def test_nothing_the_caller_sends_changes_whose_profile_it_is(self):
		"""The route takes no DA: a da_id in the request is not read."""
		frappe.local.form_dict["da_id"] = "DA-000004"
		try:
			with as_user(DA):
				self.assertEqual(api.my_profile()["data"]["profile"]["da_id"], "DA-000001")
		finally:
			frappe.local.form_dict.pop("da_id", None)

	def test_missing_registry_settings_are_named(self):
		"""An empty DA Services Settings field is said so, not "An unexpected error occurred"."""
		for route, args in ((api.my_profile, ()), (api.document, ("doc-1",))):
			# the refusal rolls the request back, as the shared handler does: set the id again
			set_da_registry_id(DA, mock_record_id("DA-000001"))
			frappe.local.response = frappe._dict({"docs": []})
			with (
				patch.object(svc, "get_client", side_effect=DARegistryNotConfigured(["DA Registry Tab ID"])),
				as_user(DA),
			):
				out = route(*args)
			self.assertEqual(frappe.local.response.get("http_status_code"), 500)
			self.assertEqual(out["status"], "error")
			self.assertEqual(out["code"], "DA_REGISTRY_NOT_CONFIGURED")
			self.assertEqual(out["message"], "DA Registry is not configured.")
			self.assertEqual(out["details"], {"missing_settings": ["DA Registry Tab ID"]})

	def test_own_document_needs_no_da_id(self):
		with as_user(DA):
			p = api.my_profile()["data"]["profile"]
			document_id = p["qualifications"][0]["certificates"][0]["document_id"]
			out = svc.document_link(document_id)
		self.assertEqual(out["da_id"], "DA-000001")
		self.assertTrue(out["url"])

	def test_a_document_of_another_da_does_not_open_as_own(self):
		other = MockDARegistryClient.shared().get_da("DA-000004")
		foreign = other.photo_document_id or "doc-of-nobody"
		with as_user(DA), self.assertRaises(frappe.DoesNotExistError):
			svc.document_link(foreign)
