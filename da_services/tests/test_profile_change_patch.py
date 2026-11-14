"""Migration of DA Profile Change Requests to the two-level states (patches/profile_change_two_level)."""

import frappe
from frappe.tests import IntegrationTestCase

from da_services.patches import profile_change_two_level as patch_module
from da_services.tests.fixtures import ensure_doctype_schema

DOCTYPE = "DA Profile Change Request"
PREFIX = "PCR-PATCH-"


def _old_row(suffix, status, **values):
	"""A row as the one-level code left it (written with SQL: the old states are no longer valid)."""
	name = PREFIX + suffix
	row = {
		"name": name,
		"da_id": "DA-PATCH-1",
		"section": "Contact",
		"status": status,
		"changes": "{}",
		"registry_sync_state": "Not sent",
		"creation": frappe.utils.now(),
		"modified": frappe.utils.now(),
		"owner": "Administrator",
		"modified_by": "Administrator",
		"docstatus": 0,
	}
	row.update(values)
	cols = ", ".join(f"`{c}`" for c in row)
	marks = ", ".join(["%s"] * len(row))
	frappe.db.sql(f"insert into `tab{DOCTYPE}` ({cols}) values ({marks})", tuple(row.values()))
	return name


class TestProfileChangeTwoLevelPatch(IntegrationTestCase):
	def setUp(self):
		ensure_doctype_schema(DOCTYPE)
		frappe.db.delete(DOCTYPE, {"name": ("like", PREFIX + "%")})

	def tearDown(self):
		frappe.db.delete(DOCTYPE, {"name": ("like", PREFIX + "%")})

	def _get(self, name, *fields):
		return frappe.db.get_value(DOCTYPE, name, list(fields), as_dict=True)

	def test_old_states_move_to_the_two_level_states(self):
		pending = _old_row("1", "Pending")
		failed_down = _old_row(
			"2",
			"Approved",
			approver="sup@da.local",
			decided_at="2026-11-05 10:00:00",
			registry_sync_state="Failed",
			registry_error="DARegistryUnavailable: down",
		)
		failed_refused = _old_row(
			"3", "Approved", registry_sync_state="Failed", registry_error="DARegistryError: refused"
		)
		waiting = _old_row(
			"4",
			"Applied",
			registry_sync_state="Accepted",
			registry_approval="Pending",
			applied_at="2026-11-06 09:00:00",
		)
		done = _old_row(
			"5",
			"Applied",
			registry_sync_state="Accepted",
			registry_approval="Approved",
			applied_at="2026-11-06 09:00:00",
		)
		rejected_there = _old_row(
			"6", "Applied", registry_sync_state="Accepted", registry_approval="Rejected"
		)
		rejected_here = _old_row("7", "Rejected", approver="sup@da.local", decision_note="Wrong number")

		patch_module.execute()
		patch_module.execute()  # idempotent

		self.assertEqual(self._get(pending, "status").status, "Pending Supervisor")
		row = self._get(failed_down, "status", "registry_sync_state", "l1_by", "l1_at")
		self.assertEqual(
			(row.status, row.registry_sync_state, row.l1_by),
			("Registry failed", "Unavailable", "sup@da.local"),
		)
		self.assertIsNotNone(row.l1_at)
		row = self._get(failed_refused, "status", "registry_sync_state")
		self.assertEqual((row.status, row.registry_sync_state), ("Registry failed", "Refused"))
		row = self._get(waiting, "status", "applied_at", "last_push_at", "push_attempts")
		self.assertEqual((row.status, row.applied_at, row.push_attempts), ("Sent to registry", None, 1))
		self.assertIsNotNone(row.last_push_at)
		row = self._get(done, "status", "applied_at")
		self.assertEqual(row.status, "Applied")
		self.assertIsNotNone(row.applied_at)
		row = self._get(rejected_there, "status", "registry_sync_state", "registry_error")
		self.assertEqual((row.status, row.registry_sync_state), ("Registry failed", "Refused"))
		self.assertTrue(row.registry_error)
		row = self._get(rejected_here, "status", "l1_note", "initiated_by_role")
		self.assertEqual(
			(row.status, row.l1_note, row.initiated_by_role),
			("Rejected", "Wrong number", "Development Agent"),
		)
