"""apply_geo_user_permissions: geo codes of a registry filter_by, from the user's User
Permissions, sent as names."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from da_services.services import constants as C
from da_services.services.utils import apply_geo_user_permissions

PERMS = "da_services.services.utils.frappe.permissions.get_user_permissions"
NAMES = "da_services.services.utils.geo.display_names"


def _perms(**by_doctype):
	return {doctype: [{"doc": v} for v in values] for doctype, values in by_doctype.items()}


def _names(doctype, codes):
	return {c: f"{c} name" for c in codes if c != "NOPE"}


class TestApplyGeoUserPermissions(IntegrationTestCase):
	USER = "geo-filter-da@da.local"

	def _apply(self, filter_by, user=None, **perms):
		with patch(PERMS, return_value=_perms(**perms)), patch(NAMES, side_effect=_names):
			return apply_geo_user_permissions(filter_by, user=user or self.USER)

	def test_an_empty_level_is_filled_with_the_permitted_name(self):
		out = self._apply({"first_name": {"eq": "test"}, "woreda_code": ""}, **{"DA Woreda": ["ET04-W01"]})
		self.assertEqual(out, {"first_name": {"eq": "test"}, "woreda_name": {"eq": "ET04-W01 name"}})

	def test_every_level(self):
		out = self._apply(
			{},
			**{
				"DA Region": ["ET04"],
				"DA Zone": ["ET04-Z01"],
				"DA Woreda": ["ET04-W01"],
				"DA Kebele": ["K1"],
			},
		)
		self.assertEqual(
			out,
			{
				"region_name": {"eq": "ET04 name"},
				"zone_name": {"eq": "ET04-Z01 name"},
				"woreda_name": {"eq": "ET04-W01 name"},
				"kebele_name": {"eq": "K1 name"},
			},
		)

	def test_several_permissions_become_in(self):
		out = self._apply({}, **{"DA Woreda": ["ET04-W02", "ET04-W01"]})
		self.assertEqual(out, {"woreda_name": {"in": ["ET04-W01 name", "ET04-W02 name"]}})

	def test_a_permitted_code_is_sent_as_its_name(self):
		for asked in ("ET04-W01", {"eq": "ET04-W01"}, {"in": ["ET04-W01"]}):
			out = self._apply({"woreda_code": asked}, **{"DA Woreda": ["ET04-W01", "ET04-W02"]})
			self.assertEqual(out, {"woreda_name": {"eq": "ET04-W01 name"}}, asked)

	def test_a_code_outside_the_permissions_is_refused(self):
		for asked in ("ET03-W02", {"eq": "ET03-W02"}, {"in": ["ET04-W01", "ET03-W02"]}):
			with self.assertRaises(frappe.PermissionError):
				self._apply({"woreda_code": asked}, **{"DA Woreda": ["ET04-W01"]})

	def test_a_level_without_permissions_is_not_restricted(self):
		out = self._apply({"zone_code": {"eq": "ANY"}}, **{"DA Woreda": ["ET04-W01"]})
		self.assertEqual(out, {"zone_name": {"eq": "ANY name"}, "woreda_name": {"eq": "ET04-W01 name"}})
		self.assertEqual(self._apply({"other": 1}), {"other": 1})

	def test_an_unknown_code_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._apply({"woreda_code": "NOPE"})

	def test_the_not_assigned_kebele_is_not_a_filter(self):
		self.assertEqual(self._apply({}, **{"DA Kebele": [C.NOT_ASSIGNED_KEBELE]}), {})

	def test_administrator_is_not_restricted_but_gets_names(self):
		out = self._apply({"woreda_code": "X"}, user="Administrator", **{"DA Woreda": ["ET04-W01"]})
		self.assertEqual(out, {"woreda_name": {"eq": "X name"}})

	def test_real_user_permission_and_name(self):
		woreda = frappe.get_all("DA Woreda", fields=["name", "woreda_name"], limit=1)
		if not woreda:
			self.skipTest("no DA Woreda on this site")
		if not frappe.db.exists("User", self.USER):
			frappe.get_doc(
				{"doctype": "User", "email": self.USER, "first_name": "Geo", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": self.USER,
				"allow": "DA Woreda",
				"for_value": woreda[0].name,
			}
		).insert(ignore_permissions=True)
		frappe.cache.hdel("user_permissions", self.USER)
		self.assertEqual(
			apply_geo_user_permissions({}, user=self.USER), {"woreda_name": {"eq": woreda[0].woreda_name}}
		)
