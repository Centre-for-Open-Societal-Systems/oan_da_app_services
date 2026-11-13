"""DA Notification doctype, service and API (DA-196)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from da_services.api.v1 import notifications as api
from da_services.services import constants as C
from da_services.services import notifications as svc
from da_services.services import visits
from da_services.utils.permissions import get_scope_context

DA1 = "ntf-da1@da.local"  # DA-000001
DA2 = "ntf-da2@da.local"  # DA-000002
COMMS = "ntf-comms@da.local"


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


class TestDANotification(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for dt in ("DA Notification", "DA Visit", "DA Task", "DA RBAC Assignment"):
			frappe.db.delete(dt)
		_user(DA1, [C.ROLE_DA])
		_user(DA2, [C.ROLE_DA])
		_user(COMMS, [C.ROLE_COMMS])
		_grant(DA1, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA2, C.ROLE_DA, da_id="DA-000002", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(COMMS, C.ROLE_COMMS)
		self.n1 = svc.notify(
			DA1,
			kind="Task",
			title="Follow-up task created",
			body="Lelise",
			href="/tasks/x",
			event="task_created",
		)
		self.n2 = svc.notify(DA1, kind="Sync", title="Needs you: resolve", event="sync_conflict")
		self.n3 = svc.notify(DA2, kind="Visit", title="Visit rescheduled", event="visit_rescheduled")
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	def tearDown(self):
		frappe.set_user("Administrator")
		for dt in ("DA Notification", "DA Visit", "DA Task", "DA RBAC Assignment"):
			frappe.db.delete(dt)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	# -- doctype --------------------------------------------------------------------

	def test_notify_creates_unread_row_with_shape(self):
		doc = frappe.get_doc("DA Notification", self.n1)
		self.assertTrue(doc.name.startswith("DA-NTF-"))
		self.assertEqual(
			(doc.user, doc.kind, doc.read, doc.channel, doc.delivery_status),
			(DA1, "Task", 0, "App", "Not sent"),
		)
		with self.assertRaises(frappe.ValidationError):
			svc.notify(DA1, kind="Nope", title="x")

	def test_recipient_can_only_mark_read_and_cannot_delete(self):
		frappe.set_user(DA1)
		doc = frappe.get_doc("DA Notification", self.n1)
		doc.title = "edited"
		with self.assertRaises(frappe.ValidationError):
			doc.save()
		doc.reload()
		doc.read = 1
		doc.save()
		self.assertIsNotNone(doc.read_at)
		# No role holds delete on the doctype, so Frappe refuses before on_trash runs.
		with self.assertRaises(frappe.PermissionError):
			doc.delete()
		# Even an administrator is stopped by on_trash: notifications are never deleted.
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc("DA Notification", self.n1).delete()

	def test_user_for_da_resolves_through_active_assignment(self):
		self.assertEqual(svc.user_for_da("DA-000001"), DA1)
		self.assertIsNone(svc.user_for_da("DA-999999"))
		self.assertIsNone(svc.notify_da("DA-999999", kind="Task", title="nobody"))

	# -- service / API ----------------------------------------------------------------

	def test_list_is_own_rows_only_with_unread_count(self):
		rows, meta = svc.list_notifications(get_scope_context(DA1))
		self.assertEqual({r["id"] for r in rows}, {self.n1, self.n2})
		self.assertEqual(meta["unread_count"], 2)
		self.assertEqual(rows[0]["id"], self.n2)  # newest first
		self.assertEqual(
			set(rows[0]),
			{
				"id",
				"kind",
				"title",
				"body",
				"href",
				"read",
				"read_at",
				"event",
				"reference_doctype",
				"reference_name",
				"at",
			},
		)
		rows, meta = svc.list_notifications(get_scope_context(DA2))
		self.assertEqual([r["id"] for r in rows], [self.n3])

	def test_mark_read_and_read_all(self):
		ctx1 = get_scope_context(DA1)
		out = svc.mark_read(self.n1, ctx1)
		self.assertTrue(out["read"])
		self.assertIsNotNone(out["read_at"])
		with self.assertRaises(frappe.PermissionError):
			svc.mark_read(self.n3, ctx1)  # someone else's
		self.assertEqual(svc.mark_all_read(ctx1), {"updated": 1})
		_rows, meta = svc.list_notifications(ctx1, unread=True)
		self.assertEqual(meta["unread_count"], 0)

	def test_api_endpoints(self):
		frappe.set_user(DA1)
		out = api.list_notifications(unread="1", limit=10)
		self.assertEqual(out["status"], "success")
		self.assertEqual(len(out["data"]), 2)
		self.assertEqual(out["meta"]["unread_count"], 2)
		out = api.read_one(self.n2)
		self.assertTrue(out["data"]["read"])
		out = api.read_all()
		self.assertEqual(out["data"]["updated"], 1)
		frappe.set_user(COMMS)
		out = api.list_notifications()
		self.assertEqual(out["status"], "success")  # Comms gets notifications too, just their own
		self.assertEqual(out["data"], [])

	def test_api_denies_guest(self):
		frappe.set_user("Guest")
		out = api.list_notifications()
		self.assertEqual(out["status"], "error")

	# -- wired event: visit outcome -> follow-up task -> notification ---------------------

	def test_visit_follow_up_notifies_the_da(self):
		ctx = get_scope_context(DA1)
		v = visits.plan_visit(farmer_id="FR-88466", scheduled_at=f"{today()} 08:30:00", ctx=ctx)
		visits.submit_outcome(
			v["name"],
			observed="ok",
			next_action={"type": "Task", "note": "Bring seed", "date": add_days(today(), 3)},
			ctx=ctx,
		)
		rows, _meta = svc.list_notifications(ctx, unread=True)
		row = next((r for r in rows if r["event"] == "task_created"), None)
		self.assertIsNotNone(row, [r["title"] for r in rows])
		self.assertEqual(row["kind"], "Task")
		self.assertTrue(row["href"].startswith("/tasks/"))
		self.assertEqual(row["reference_doctype"], "DA Task")
