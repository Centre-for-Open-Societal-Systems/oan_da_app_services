"""My Team (GET /my-team): the signed-in DA first, then the DAs of the same Kebele.

Uses the DA Registry mock fixtures:
  DA-000001 Abebe Kebede  Active     ET04-W01 / ET04-W01-K03
  DA-000002 Marta Tesfaye On-leave   ET04-W01 / ET04-W01-K07
  DA-000003 Yonas Alemu   Separated  ET03-W02 / no Kebele
  DA-000004 Hana Girma    Active     ET03-W02 / ET03-W02-K01
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.api.v1 import my_team as api
from da_services.integrations.da_registry.client import get_client
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import constants as C
from da_services.services import my_team as svc
from da_services.utils.permissions import get_scope_context

DA_SELF = "team-da-self@da.local"  # DA-000001
DA_GONE = "team-da-gone@da.local"  # DA-000099, not in the registry
SUP = "team-sup@da.local"
ALL_USERS = (DA_SELF, DA_GONE, SUP)
MY_KEBELE = "ET04-W01-K03"


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


class TestMyTeam(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA RBAC Assignment", {"user": ("in", ALL_USERS)})
		MockDARegistryClient.reset()
		_user(DA_SELF, [C.ROLE_DA])
		_user(DA_GONE, [C.ROLE_DA])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_grant(DA_SELF, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA_GONE, C.ROLE_DA, da_id="DA-000099", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		self.registry = get_client()._records

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA RBAC Assignment", {"user": ("in", ALL_USERS)})
		MockDARegistryClient.reset()

	def _ids(self, user=DA_SELF):
		return [row["da_id"] for row in svc.list_my_team(get_scope_context(user))]

	def test_only_me_when_nobody_else_is_posted_in_my_kebele(self):
		self.assertEqual(self._ids(), ["DA-000001"])  # DA-000002 is in another Kebele of the Woreda

	def test_same_kebele_das_follow_me_by_name_whatever_their_specialisation(self):
		self.registry["DA-000002"]["assigned_kebele"] = MY_KEBELE  # On-leave, Livestock
		self.registry["DA-000005"] = {
			**self.registry["DA-000002"],
			"da_id": "DA-000005",
			"full_name": "Aster Mulugeta",
			"active_status": C.ACTIVE_STATUS_ACTIVE,
			"specialisation": "Natural resources",
		}
		self.assertEqual(self._ids(), ["DA-000001", "DA-000005", "DA-000002"])

	def test_separated_das_are_left_out(self):
		self.registry["DA-000002"]["assigned_kebele"] = MY_KEBELE
		self.registry["DA-000002"]["active_status"] = C.ACTIVE_STATUS_SEPARATED
		self.assertEqual(self._ids(), ["DA-000001"])

	def test_without_a_kebele_on_my_record_the_team_is_my_woreda(self):
		self.registry["DA-000001"]["assigned_kebele"] = None
		self.assertEqual(self._ids(), ["DA-000001", "DA-000002"])

	def test_row_fields(self):
		(row,) = svc.list_my_team(get_scope_context(DA_SELF))
		self.assertEqual(set(row), set(svc.FIELDS))
		self.assertEqual(
			(row["full_name"], row["kebele"], row["woreda"], row["active_status"], row["joined_at"]),
			("Abebe Kebede", MY_KEBELE, "ET04-W01", C.ACTIVE_STATUS_ACTIVE, "2019-09-01"),
		)

	# -- API ----------------------------------------------------------------

	def test_api_envelope(self):
		frappe.set_user(DA_SELF)
		out = api.get_my_team()
		self.assertEqual(out["status"], "success", out)
		self.assertEqual([row["da_id"] for row in out["data"]], ["DA-000001"])
		self.assertEqual(out["meta"]["total"], 1)

	def test_api_is_for_development_agents_only(self):
		frappe.set_user(SUP)
		out = api.get_my_team()
		self.assertEqual(out["status"], "error")
		self.assertEqual(frappe.response.get("http_status_code"), 403)

	def test_api_da_missing_from_the_registry(self):
		frappe.set_user(DA_GONE)
		out = api.get_my_team()
		self.assertEqual(out["status"], "error")
		self.assertEqual(frappe.response.get("http_status_code"), 404)
