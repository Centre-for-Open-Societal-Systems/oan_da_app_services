"""DA Leave Request: doctype, service and API (lifecycle slice 1).

Registry mock fixtures: DA-000001 (ET04 / ET04-W01, Active), DA-000004 (ET03 / ET03-W02, Active).
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from da_services.api.v1 import leave as api
from da_services.integrations.da_registry.client import get_client
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import constants as C
from da_services.services import leave as svc
from da_services.utils.permissions import get_scope_context

DA1 = "lvr-da1@da.local"  # DA-000001, ET04-W01
DA3 = "lvr-da3@da.local"  # DA-000004, ET03-W02
SUP = "lvr-sup@da.local"  # Supervisor ET04-W01
SUP_OTHER = "lvr-sup3@da.local"  # Supervisor ET03-W02
EXEC = "lvr-exec@da.local"  # Executive ET04
ALL_USERS = (DA1, DA3, SUP, SUP_OTHER, EXEC)
DOCTYPES = ("DA Notification", "DA Leave Request", "DA RBAC Assignment")


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


def _ctx(user):
	return get_scope_context(user)


class TestLeaveRequests(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self._clean()
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

	def tearDown(self):
		frappe.set_user("Administrator")
		self._clean()
		MockDARegistryClient.reset()

	def _clean(self):
		frappe.db.delete("DA Notification", {"user": ("in", ALL_USERS)})
		frappe.db.delete("DA Leave Request", {"da_id": ("in", ("DA-000001", "DA-000004"))})
		frappe.db.delete("DA RBAC Assignment", {"user": ("in", ALL_USERS)})

	def _submit(self, user=DA1, **over):
		data = {
			"leave_type": "Annual",
			"from_date": add_days(today(), 7),
			"to_date": add_days(today(), 9),
			"reason": "Family event in Addis",
		}
		data.update(over)
		return svc.submit_leave_request(ctx=_ctx(user), **data)

	# -- submit ---------------------------------------------------------------

	def test_submit_creates_pending_with_scope_and_days(self):
		out = self._submit()
		self.assertEqual(out["status"], "Pending")
		self.assertEqual(out["da_id"], "DA-000001")
		self.assertEqual((out["region"], out["woreda"]), ("ET04", "ET04-W01"))
		self.assertEqual(out["days"], 3)
		self.assertEqual(out["requested_by"], DA1)

	def test_submit_validation(self):
		with self.assertRaises(frappe.ValidationError):
			self._submit(leave_type="Holiday")
		with self.assertRaises(frappe.ValidationError):
			self._submit(to_date=add_days(today(), 3))  # before from_date
		with self.assertRaises(frappe.ValidationError):
			self._submit(from_date=add_days(today(), -1), to_date=today())  # past
		with self.assertRaises(frappe.ValidationError):
			self._submit(reason="no")
		with self.assertRaises(frappe.ValidationError):
			self._submit(to_date=add_days(today(), 400))

	def test_overlapping_open_request_rejected(self):
		self._submit()
		with self.assertRaises(frappe.ValidationError):
			self._submit(from_date=add_days(today(), 8), to_date=add_days(today(), 12))

	def test_client_op_id_is_idempotent(self):
		first = self._submit(client_op_id="lvr-op-1")
		second = self._submit(client_op_id="lvr-op-1")
		self.assertEqual(first["id"], second["id"])
		self.assertEqual(frappe.db.count("DA Leave Request", {"da_id": "DA-000001"}), 1)

	def test_only_da_can_submit(self):
		with self.assertRaises(frappe.PermissionError):
			svc.submit_leave_request(
				ctx=_ctx(SUP), leave_type="Annual", from_date=today(), to_date=today(), reason="not a DA"
			)

	# -- cancel ---------------------------------------------------------------

	def test_da_cancels_own_pending(self):
		out = self._submit()
		cancelled = svc.cancel_leave_request(out["id"], _ctx(DA1))
		self.assertEqual(cancelled["status"], "Cancelled")

	def test_other_da_cannot_cancel(self):
		out = self._submit()
		with self.assertRaises(frappe.PermissionError):
			svc.cancel_leave_request(out["id"], _ctx(DA3))

	# -- list / get scope -------------------------------------------------------

	def test_list_scope_per_role(self):
		mine = self._submit()
		theirs = self._submit(user=DA3)

		def ids(user):
			# The list goes through frappe.get_list, whose scope hook reads the session user.
			frappe.set_user(user)
			try:
				return [r["id"] for r in svc.list_leave_requests(_ctx(user))[0]]
			finally:
				frappe.set_user("Administrator")

		self.assertEqual(ids(DA1), [mine["id"]])
		self.assertEqual(ids(SUP), [mine["id"]])
		self.assertEqual(ids(SUP_OTHER), [theirs["id"]])
		self.assertEqual(ids(EXEC), [mine["id"]])
		self.assertEqual(sorted(ids("Administrator")), sorted([mine["id"], theirs["id"]]))

	def test_get_outside_scope_is_denied(self):
		theirs = self._submit(user=DA3)
		with self.assertRaises(frappe.PermissionError):
			svc.get_leave_request(theirs["id"], _ctx(SUP))
		self.assertEqual(svc.get_leave_request(theirs["id"], _ctx(SUP_OTHER))["id"], theirs["id"])

	# -- decide ---------------------------------------------------------------

	def test_supervisor_approves_future_leave(self):
		out = self._submit()
		decided = svc.decide(out["id"], decision="Approve", ctx=_ctx(SUP))
		self.assertEqual(decided["status"], "Approved")
		self.assertEqual(decided["approver"], SUP)
		self.assertEqual(decided["registry_sync_state"], "Not sent")  # starts later
		self.assertEqual(get_client().get_da("DA-000001").active_status, C.ACTIVE_STATUS_ACTIVE)
		rows = frappe.get_all("DA Notification", filters={"user": DA1, "kind": "Approval"}, fields=["title"])
		self.assertEqual(len(rows), 1)
		self.assertIn("approved", rows[0].title)

	def test_approving_leave_starting_today_activates_and_sets_on_leave(self):
		out = self._submit(from_date=today(), to_date=add_days(today(), 2))
		decided = svc.decide(out["id"], decision="Approve", ctx=_ctx(SUP))
		self.assertEqual(decided["status"], "Active")
		self.assertEqual(decided["registry_sync_state"], "Accepted")
		self.assertTrue(decided["registry_reference"].startswith("MOCK-"))
		self.assertEqual(get_client().get_da("DA-000001").active_status, C.ACTIVE_STATUS_ON_LEAVE)

	def test_reject_requires_note(self):
		out = self._submit()
		with self.assertRaises(frappe.ValidationError):
			svc.decide(out["id"], decision="Reject", ctx=_ctx(SUP))
		decided = svc.decide(out["id"], decision="Reject", note="Coverage gap that week", ctx=_ctx(SUP))
		self.assertEqual(decided["status"], "Rejected")
		self.assertEqual(decided["decision_note"], "Coverage gap that week")

	def test_other_woreda_supervisor_cannot_decide(self):
		out = self._submit()
		with self.assertRaises(frappe.PermissionError):
			svc.decide(out["id"], decision="Approve", ctx=_ctx(SUP_OTHER))

	def test_executive_cannot_decide(self):
		out = self._submit()
		with self.assertRaises(frappe.PermissionError):
			svc.decide(out["id"], decision="Approve", ctx=_ctx(EXEC))

	def test_cannot_decide_twice(self):
		out = self._submit()
		svc.decide(out["id"], decision="Approve", ctx=_ctx(SUP))
		with self.assertRaises(frappe.ValidationError):
			svc.decide(out["id"], decision="Reject", note="changed my mind", ctx=_ctx(SUP))

	# -- schedule: start / end --------------------------------------------------

	def test_scheduler_starts_and_ends_leave(self):
		out = self._submit(from_date=add_days(today(), 1), to_date=add_days(today(), 2))
		svc.decide(out["id"], decision="Approve", ctx=_ctx(SUP))
		# Not yet started
		stats = svc.process_leave_schedule()
		self.assertEqual((stats["started"], stats["ended"], stats["failed"]), (0, 0, 0))

		# Pretend the first day has come
		frappe.db.set_value(
			"DA Leave Request", out["id"], {"from_date": today(), "to_date": today()}, update_modified=False
		)
		self.assertEqual(svc.process_leave_schedule()["started"], 1)
		self.assertEqual(frappe.db.get_value("DA Leave Request", out["id"], "status"), "Active")
		self.assertEqual(get_client().get_da("DA-000001").active_status, C.ACTIVE_STATUS_ON_LEAVE)

		# Pretend the last day has passed (keep from <= to so the row stays valid)
		frappe.db.set_value(
			"DA Leave Request",
			out["id"],
			{"from_date": add_days(today(), -2), "to_date": add_days(today(), -1)},
			update_modified=False,
		)
		self.assertEqual(svc.process_leave_schedule()["ended"], 1)
		doc = frappe.get_doc("DA Leave Request", out["id"])
		self.assertEqual(doc.status, "Completed")
		self.assertIsNotNone(doc.completed_at)
		self.assertEqual(get_client().get_da("DA-000001").active_status, C.ACTIVE_STATUS_ACTIVE)

	def test_scheduler_retries_failed_registry_push(self):
		"""A registry outage at approval time is recorded, not fatal; the next run converges."""
		from unittest.mock import patch

		from da_services.integrations.da_registry.client import DARegistryUnavailable

		out = self._submit(from_date=today(), to_date=add_days(today(), 1))
		with patch.object(
			MockDARegistryClient, "push_lifecycle_outcome", side_effect=DARegistryUnavailable("registry down")
		):
			decided = svc.decide(out["id"], decision="Approve", ctx=_ctx(SUP))
		self.assertEqual(decided["status"], "Active")  # local state stands
		self.assertEqual(decided["registry_sync_state"], "Failed")
		self.assertIn("registry down", decided["registry_error"])
		self.assertEqual(get_client().get_da("DA-000001").active_status, C.ACTIVE_STATUS_ACTIVE)
		self.assertTrue(
			frappe.db.exists(
				"DA Audit Event", {"action": "leave_registry_sync_failed", "entity_name": out["id"]}
			)
		)

		stats = svc.process_leave_schedule()
		self.assertEqual(stats["retried"], 1)
		doc = frappe.get_doc("DA Leave Request", out["id"])
		self.assertEqual(doc.registry_sync_state, "Accepted")
		self.assertEqual(get_client().get_da("DA-000001").active_status, C.ACTIVE_STATUS_ON_LEAVE)
		# Nothing left to retry; a second run is a no-op
		self.assertEqual(svc.process_leave_schedule()["retried"], 0)

	def test_reason_length_is_bounded(self):
		with self.assertRaises(frappe.ValidationError):
			self._submit(reason="x" * (svc.MAX_REASON + 1))
		out = self._submit(reason="y" * svc.MAX_REASON)
		self.assertEqual(len(out["reason"]), svc.MAX_REASON)

	def test_active_leave_cannot_be_cancelled(self):
		out = self._submit(from_date=today(), to_date=add_days(today(), 1))
		svc.decide(out["id"], decision="Approve", ctx=_ctx(SUP))
		with self.assertRaises(frappe.ValidationError):
			svc.cancel_leave_request(out["id"], _ctx(DA1))

	# -- doctype guards ---------------------------------------------------------

	def test_rows_are_history(self):
		out = self._submit()
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("DA Leave Request", out["id"], ignore_permissions=True)

	def test_invalid_transition_blocked(self):
		out = self._submit()
		doc = frappe.get_doc("DA Leave Request", out["id"])
		doc.status = "Completed"
		doc.flags.ignore_permissions = True
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	# -- API --------------------------------------------------------------------

	def test_api_submit_and_decide(self):
		frappe.set_user(DA1)
		out = api.submit(
			leave_type="Sick",
			from_date=add_days(today(), 3),
			to_date=add_days(today(), 4),
			reason="Doctor appointment",
		)
		self.assertEqual(out["status"], "success")
		rid = out["data"]["id"]

		frappe.set_user(SUP)
		listed = api.list_requests(status="Pending")
		self.assertEqual([r["id"] for r in listed["data"]], [rid])
		decided = api.decide(rid, decision="Approve")
		self.assertEqual(decided["data"]["status"], "Approved")

	def test_api_supervisor_cannot_submit(self):
		frappe.set_user(SUP)
		out = api.submit(leave_type="Annual", from_date=today(), to_date=today(), reason="not allowed")
		self.assertEqual(out["status"], "error")
		self.assertEqual(frappe.response.get("http_status_code"), 403)
