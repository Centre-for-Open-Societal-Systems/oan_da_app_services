"""Surveys nav visibility (FR-09d Stage 4). Pure: no DB rows are needed."""

from __future__ import annotations

import unittest

from da_services.api.v1.me import menu_for
from da_services.services import constants as C
from da_services.utils.permissions import ScopeContext


def _ctx(*roles: str, unrestricted: bool = False) -> ScopeContext:
	return ScopeContext(user="menu-test@example.com", roles=frozenset(roles), unrestricted=unrestricted)


class TestSurveyMenu(unittest.TestCase):
	def test_executive_sees_surveys(self):
		self.assertIn("/surveys", menu_for(_ctx(C.ROLE_EXECUTIVE)))

	def test_da_supervisor_and_admin_keep_surveys(self):
		for role in (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_ADMIN):
			self.assertIn("/surveys", menu_for(_ctx(role)), role)

	def test_unrestricted_admin_sees_surveys(self):
		self.assertIn("/surveys", menu_for(_ctx(unrestricted=True)))

	def test_communications_officer_does_not_see_surveys(self):
		self.assertNotIn("/surveys", menu_for(_ctx(C.ROLE_COMMS)))
