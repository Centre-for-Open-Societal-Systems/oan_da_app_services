"""Portal sidebar from DA Portal Menu Item: seeding and GET /me menu."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from da_services.api.v1 import me as me_api
from da_services.services import constants as C
from da_services.setup.install import PORTAL_MENU, seed_portal_menu

DOCTYPE = "DA Portal Menu Item"
TEST_ROUTE = "/test-portal-menu"


def _menu(*roles):
	with patch.object(me_api.frappe, "get_roles", return_value=list(roles)):
		return me_api.menu_for()


class TestPortalMenu(IntegrationTestCase):
	def setUp(self):
		seed_portal_menu()
		frappe.db.delete(DOCTYPE, {"route": TEST_ROUTE})
		frappe.cache.delete_value("da_portal_menu")

	def tearDown(self):
		frappe.db.rollback()
		frappe.cache.delete_value("da_portal_menu")

	def test_seed_creates_every_route_once_and_is_idempotent(self):
		seed_portal_menu()
		routes = frappe.get_all(DOCTYPE, pluck="route")
		for route, _label, _roles in PORTAL_MENU:
			self.assertEqual(routes.count(route), 1, route)

	def test_seed_leaves_an_edited_item_alone(self):
		name = frappe.db.get_value(DOCTYPE, {"route": "/visits"})
		frappe.db.set_value(DOCTYPE, name, "label", "Field Visits")
		seed_portal_menu()
		self.assertEqual(frappe.db.get_value(DOCTYPE, name, "label"), "Field Visits")

	def test_menu_follows_the_seeded_roles_in_sequence_order(self):
		self.assertEqual(_menu(C.ROLE_COMMS), ["/dashboard", "/knowledge", "/broadcast", "/alerts"])
		da = _menu(C.ROLE_DA)
		self.assertIn("/my-teams", da)
		self.assertNotIn("/approvals", da)
		self.assertEqual(_menu("Guest"), [])

	def test_a_disabled_item_is_not_returned(self):
		doc = frappe.get_doc(DOCTYPE, frappe.db.get_value(DOCTYPE, {"route": "/broadcast"}))
		doc.enabled = 0
		doc.save(ignore_permissions=True)
		self.assertNotIn("/broadcast", _menu(C.ROLE_COMMS))

	def test_a_new_item_and_role_show_without_a_restart(self):
		self.assertNotIn(TEST_ROUTE, _menu(C.ROLE_COMMS))  # warms the cache
		frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"route": TEST_ROUTE,
				"label": "Test",
				"sequence": 1,
				"roles": [{"role": C.ROLE_COMMS}],
			}
		).insert(ignore_permissions=True)
		self.assertEqual(_menu(C.ROLE_COMMS)[0], TEST_ROUTE)
		self.assertNotIn(TEST_ROUTE, _menu(C.ROLE_DA))

	def test_route_must_start_with_a_slash(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{"doctype": DOCTYPE, "route": "dashboard-x", "roles": [{"role": C.ROLE_DA}]}
			).insert(ignore_permissions=True)

	def test_a_duplicate_route_is_refused(self):
		with self.assertRaises(frappe.UniqueValidationError):
			frappe.get_doc(
				{"doctype": DOCTYPE, "route": "/dashboard", "roles": [{"role": C.ROLE_DA}]}
			).insert(ignore_permissions=True)
