"""Surveys nav visibility (FR-09d Stage 4). Pure: no DB rows are needed."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from da_services.api.v1.me import menu_for
from da_services.services import constants as C


def _menu(role: str) -> list[str]:
	"""The nav for a user holding just `role` (menu_for reads the session user's roles)."""
	with patch("da_services.api.v1.me.frappe.get_roles", return_value=[role]):
		return menu_for()


class TestSurveyMenu(unittest.TestCase):
	def test_executive_sees_surveys(self):
		self.assertIn("/surveys", _menu(C.ROLE_EXECUTIVE))

	def test_da_supervisor_and_admin_keep_surveys(self):
		for role in (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_ADMIN):
			self.assertIn("/surveys", _menu(role), role)

	def test_communications_officer_does_not_see_surveys(self):
		self.assertNotIn("/surveys", _menu(C.ROLE_COMMS))
