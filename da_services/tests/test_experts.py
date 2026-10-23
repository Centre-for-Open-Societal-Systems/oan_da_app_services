"""Experts / My Team list (T3): scope per role, filters, self excluded.

Uses the DA Registry mock fixtures:
  DA-000001 Abebe Kebede  Active     Crop production     ET04 / ET04-Z01 / ET04-W01
  DA-000002 Marta Tesfaye On-leave   Livestock           ET04 / ET04-Z01 / ET04-W01
  DA-000003 Yonas Alemu   Separated  Natural resources   ET03 / ET03-Z02 / ET03-W02
  DA-000004 Hana Girma    Active     Crop production     ET03 / ET03-Z02 / ET03-W02
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.api.v1 import experts as api
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import constants as C
from da_services.services import experts as svc
from da_services.utils.permissions import get_scope_context

DA_SELF = "ex-da-self@da.local"  # DA-000001, ET04-W01 -> only itself in the Woreda
DA_NEW = "ex-da-new@da.local"  # DA-000099 (not in registry), ET04-W01
SUP_W01 = "ex-sup-w01@da.local"
EXEC_Z02 = "ex-exec-z02@da.local"  # region ET03 + zone ET03-Z02
EXEC_ET04 = "ex-exec-et04@da.local"  # region only
COMMS = "ex-comms@da.local"
ALL_USERS = (DA_SELF, DA_NEW, SUP_W01, EXEC_Z02, EXEC_ET04, COMMS)


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


class TestExperts(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA RBAC Assignment", {"user": ("in", ALL_USERS)})
		MockDARegistryClient.reset()
		_user(DA_SELF, [C.ROLE_DA])
		_user(DA_NEW, [C.ROLE_DA])
		_user(SUP_W01, [C.ROLE_SUPERVISOR])
		_user(EXEC_Z02, [C.ROLE_EXECUTIVE])
		_user(EXEC_ET04, [C.ROLE_EXECUTIVE])
		_user(COMMS, [C.ROLE_COMMS])
		_grant(DA_SELF, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA_NEW, C.ROLE_DA, da_id="DA-000099", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP_W01, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(EXEC_Z02, C.ROLE_EXECUTIVE, region_scope="ET03", zone_scope="ET03-Z02")
		_grant(EXEC_ET04, C.ROLE_EXECUTIVE, region_scope="ET04")
		_grant(COMMS, C.ROLE_COMMS)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA RBAC Assignment", {"user": ("in", ALL_USERS)})
		MockDARegistryClient.reset()

	def _ids(self, user, **kw):
		rows, meta = svc.list_experts(get_scope_context(user), **kw)
		return [r["da_id"] for r in rows], meta

	# -- scope --------------------------------------------------------------

	def test_da_sees_active_specialists_in_own_woreda_not_self(self):
		ids, meta = self._ids(DA_NEW)
		self.assertEqual(ids, ["DA-000001"])  # DA-000002 is On-leave
		self.assertEqual(meta["by_specialisation"], {"Crop production": 1})

	def test_caller_is_excluded(self):
		ids, _ = self._ids(DA_SELF)
		self.assertEqual(ids, [])

	def test_supervisor_woreda(self):
		ids, _ = self._ids(SUP_W01)
		self.assertEqual(ids, ["DA-000001"])

	def test_executive_zone_does_not_widen_to_region(self):
		ids, _ = self._ids(EXEC_Z02)
		self.assertEqual(ids, ["DA-000004"])  # DA-000003 is Separated; nothing outside ET03-Z02

	def test_executive_region(self):
		ids, _ = self._ids(EXEC_ET04)
		self.assertEqual(ids, ["DA-000001"])

	def test_admin_sees_all_active_specialists(self):
		ids, meta = self._ids("Administrator")
		self.assertEqual(ids, ["DA-000001", "DA-000004"])
		self.assertEqual(meta["total"], 2)

	# -- filters ------------------------------------------------------------

	def test_filter_by_specialisation_case_insensitive(self):
		ids, _ = self._ids("Administrator", specialisation="crop PRODUCTION")
		self.assertEqual(ids, ["DA-000001", "DA-000004"])
		ids, _ = self._ids("Administrator", specialisation="Livestock")
		self.assertEqual(ids, [])  # only an On-leave DA has it

	def test_search(self):
		ids, _ = self._ids("Administrator", q="hana")
		self.assertEqual(ids, ["DA-000004"])
		ids, _ = self._ids("Administrator", q="DA-000001")
		self.assertEqual(ids, ["DA-000001"])

	def test_pagination(self):
		ids, meta = self._ids("Administrator", limit=1, start=1)
		self.assertEqual(ids, ["DA-000004"])
		self.assertEqual(meta["total"], 2)
		self.assertEqual(meta["start"], 1)

	def test_row_fields(self):
		rows, _ = svc.list_experts(get_scope_context("Administrator"))
		row = rows[0]
		for f in (
			"da_id",
			"full_name",
			"specialisation",
			"phone",
			"kebele",
			"woreda",
			"zone",
			"region",
			"active_status",
		):
			self.assertIn(f, row)
		self.assertEqual(row["active_status"], C.ACTIVE_STATUS_ACTIVE)

	# -- API ----------------------------------------------------------------

	def test_api_excludes_comms(self):
		frappe.set_user(COMMS)
		out = api.list_experts()
		self.assertEqual(out["status"], "error")
		self.assertEqual(frappe.response.get("http_status_code"), 403)

	def test_api_envelope(self):
		frappe.set_user(SUP_W01)
		out = api.list_experts(limit="10")
		self.assertEqual([r["da_id"] for r in out["data"]], ["DA-000001"])
		self.assertEqual(out["meta"]["total"], 1)

	def test_api_rejects_bad_limit(self):
		frappe.set_user(SUP_W01)
		out = api.list_experts(limit="0")
		self.assertEqual(out["status"], "error")
		self.assertIn("Limit", out.get("message", ""))
