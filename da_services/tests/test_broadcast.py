"""Broadcast notifications: Communications Officer -> DAs in a scope (T2)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.api.v1 import notifications as api
from da_services.services import constants as C
from da_services.services import notifications as svc
from da_services.tests.fixtures import as_user

DA_W01 = "bc-da-w01@da.local"  # ET04 / ET04-W01
DA_W02 = "bc-da-w02@da.local"  # ET04 / ET04-W02
DA_ET03 = "bc-da-et03@da.local"  # ET03 / ET03-W01
COMMS_NATIONAL = "bc-comms@da.local"  # no scope on grant
COMMS_W01 = "bc-comms-w01@da.local"  # woreda ET04-W01 only
SUP = "bc-sup@da.local"
KEBELE_W01 = "BC-K01"  # in ET04-W01; DA_W01 is posted here
KEBELE_W02 = "BC-K02"  # in ET04-W02; DA_W02 is posted here

ALL_USERS = (DA_W01, DA_W02, DA_ET03, COMMS_NATIONAL, COMMS_W01, SUP)


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


def _kebele(code, woreda):
	if not frappe.db.exists("DA Kebele", code):
		doc = frappe.get_doc(
			{"doctype": "DA Kebele", "kebele_code": code, "kebele_name": f"Kebele {code}", "woreda": woreda}
		)
		doc.flags.ignore_validate = True
		doc.insert(ignore_permissions=True, ignore_links=True)


def _post(user, da_id, kebele, woreda, state="Effective"):
	frappe.get_doc(
		{
			"doctype": "DA Kebele Assignment",
			"user": user,
			"da_id": da_id,
			"kebele": kebele,
			"state": state,
			"mode": "Manual",
			"effective_date": today(),
			"woreda": woreda,
		}
	).insert(ignore_permissions=True, ignore_links=True)


SENDERS = (COMMS_NATIONAL, COMMS_W01, SUP, "Administrator", DA_W01, DA_W02, DA_ET03)


class TestBroadcast(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA Notification", {"user": ("in", ALL_USERS)})
		frappe.db.delete("DA Broadcast", {"sent_by": ("in", SENDERS)})
		frappe.db.delete("DA RBAC Assignment", {"user": ("in", ALL_USERS)})
		_user(DA_W01, [C.ROLE_DA])
		_user(DA_W02, [C.ROLE_DA])
		_user(DA_ET03, [C.ROLE_DA])
		_user(COMMS_NATIONAL, [C.ROLE_COMMS])
		_user(COMMS_W01, [C.ROLE_COMMS])
		_user(SUP, [C.ROLE_SUPERVISOR])
		_grant(DA_W01, C.ROLE_DA, da_id="DA-BC0001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA_W02, C.ROLE_DA, da_id="DA-BC0002", region_scope="ET04", woreda_scope="ET04-W02")
		_grant(DA_ET03, C.ROLE_DA, da_id="DA-BC0003", region_scope="ET03", woreda_scope="ET03-W01")
		_grant(COMMS_NATIONAL, C.ROLE_COMMS)
		_grant(COMMS_W01, C.ROLE_COMMS, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(SUP, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		frappe.db.delete("DA Kebele Assignment", {"kebele": ("in", (KEBELE_W01, KEBELE_W02))})
		_kebele(KEBELE_W01, "ET04-W01")
		_kebele(KEBELE_W02, "ET04-W02")
		_post(DA_W01, "DA-BC0001", KEBELE_W01, "ET04-W01")
		_post(DA_W02, "DA-BC0002", KEBELE_W02, "ET04-W02")
		# DA_ET03 has no Kebele assignment.

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA Notification", {"user": ("in", ALL_USERS)})
		frappe.db.delete("DA Broadcast", {"sent_by": ("in", SENDERS)})
		frappe.db.delete("DA RBAC Assignment", {"user": ("in", ALL_USERS)})
		frappe.db.delete("DA Kebele Assignment", {"kebele": ("in", (KEBELE_W01, KEBELE_W02))})

	def _rows(self, user):
		return frappe.get_all(
			"DA Notification", filters={"user": user, "kind": "Broadcast"}, fields=["title", "event", "read"]
		)

	# -- scope resolution ---------------------------------------------------

	def test_national_comms_broadcast_all(self):
		ctx = COMMS_NATIONAL
		with as_user(ctx):
			out = svc.broadcast(
				title="Fertiliser vouchers", body="Collect at the Woreda office.", scope="all"
			)
		self.assertEqual(out["recipients"], 3)
		self.assertFalse(out["duplicate"])
		for u in (DA_W01, DA_W02, DA_ET03):
			rows = self._rows(u)
			self.assertEqual(len(rows), 1)
			self.assertEqual(rows[0].title, "Fertiliser vouchers")
			self.assertEqual(rows[0].event, f"broadcast:{out['broadcast_id']}")
			self.assertFalse(rows[0].read)
		self.assertEqual(self._rows(SUP), [])  # only DAs receive broadcasts

	def test_woreda_scope_reaches_only_that_woreda(self):
		ctx = COMMS_NATIONAL
		with as_user(ctx):
			out = svc.broadcast(title="W01 meeting", scope="woreda", scope_value="ET04-W01")
		self.assertEqual(out["recipients"], 1)
		self.assertEqual(len(self._rows(DA_W01)), 1)
		self.assertEqual(self._rows(DA_W02), [])
		self.assertEqual(self._rows(DA_ET03), [])

	def test_region_scope(self):
		ctx = COMMS_NATIONAL
		with as_user(ctx):
			out = svc.broadcast(title="ET04 notice", scope="region", scope_value="ET04")
		self.assertEqual(out["recipients"], 2)
		self.assertEqual(self._rows(DA_ET03), [])

	def test_expired_grant_is_not_a_recipient(self):
		frappe.db.set_value(
			"DA RBAC Assignment",
			{"user": DA_W02, "role": C.ROLE_DA},
			"effective_to",
			"2020-01-01",
			update_modified=False,
		)
		ctx = COMMS_NATIONAL
		with as_user(ctx):
			out = svc.broadcast(title="Active only", scope="region", scope_value="ET04")
		self.assertEqual(out["recipients"], 1)
		self.assertEqual(self._rows(DA_W02), [])

	# -- sender scope rule (deny, never widen) -------------------------------

	def test_scoped_comms_cannot_broadcast_all(self):
		ctx = COMMS_W01
		with self.assertRaises(frappe.PermissionError):
			with as_user(ctx):
				svc.broadcast(title="x", scope="all")

	def test_scoped_comms_cannot_target_other_woreda(self):
		ctx = COMMS_W01
		with self.assertRaises(frappe.PermissionError):
			with as_user(ctx):
				svc.broadcast(title="x", scope="woreda", scope_value="ET04-W02")

	def test_scoped_comms_can_target_own_woreda(self):
		ctx = COMMS_W01
		with as_user(ctx):
			out = svc.broadcast(title="own woreda", scope="woreda", scope_value="ET04-W01")
		self.assertEqual(out["recipients"], 1)

	def test_kebele_scope_reaches_only_the_das_posted_there(self):
		ctx = COMMS_NATIONAL
		with as_user(ctx):
			out = svc.broadcast(title="K01 meeting", scope="kebele", scope_value=KEBELE_W01)
		self.assertEqual(out["recipients"], 1)
		self.assertEqual(len(self._rows(DA_W01)), 1)
		self.assertEqual(self._rows(DA_W02), [])
		self.assertEqual(self._rows(DA_ET03), [])

	def test_superseded_assignment_is_not_a_recipient(self):
		frappe.db.set_value(
			"DA Kebele Assignment", {"user": DA_W02, "kebele": KEBELE_W02}, "state", "Superseded"
		)
		ctx = COMMS_NATIONAL
		with self.assertRaises(frappe.ValidationError):
			with as_user(ctx):
				svc.broadcast(title="x", scope="kebele", scope_value=KEBELE_W02)

	def test_scoped_comms_kebele_follows_its_woreda(self):
		ctx = COMMS_W01
		with as_user(ctx):
			out = svc.broadcast(title="own kebele", scope="kebele", scope_value=KEBELE_W01)
		self.assertEqual(out["recipients"], 1)
		with self.assertRaises(frappe.PermissionError):
			with as_user(ctx):
				svc.broadcast(title="x", scope="kebele", scope_value=KEBELE_W02)

	def test_da_can_target_own_kebele(self):
		ctx = DA_W01
		with as_user(ctx):
			out = svc.broadcast(title="own kebele", scope="kebele", scope_value=KEBELE_W01)
		self.assertFalse(out["duplicate"])
		self.assertEqual(out["recipients"], 1)
		self.assertEqual(self._rows(DA_W02), [])

	def test_da_cannot_target_other_kebele(self):
		ctx = DA_W01
		with self.assertRaises(frappe.PermissionError):
			with as_user(ctx):
				svc.broadcast(title="x", scope="kebele", scope_value=KEBELE_W02)

	def test_da_cannot_broadcast_wider_than_their_grant(self):
		# A scoped grant (Region ET04 / Woreda ET04-W01) bounds where the DA may broadcast.
		ctx = DA_W01
		for scope, value in (("all", None), ("woreda", "ET04-W09"), ("region", "ET03")):
			with self.assertRaises(frappe.PermissionError):
				with as_user(ctx):
					svc.broadcast(title="x", scope=scope, scope_value=value)

	def test_da_without_a_kebele_cannot_broadcast(self):
		ctx = DA_ET03
		with self.assertRaises(frappe.PermissionError):
			with as_user(ctx):
				svc.broadcast(title="x", scope="kebele", scope_value=KEBELE_W01)

	def test_me_lists_the_das_kebeles(self):
		from da_services.api.v1.me import profile_for

		with as_user(DA_W01):
			data = profile_for()
		self.assertEqual(data["kebeles"], [{"code": KEBELE_W01, "name": f"Kebele {KEBELE_W01}"}])
		with as_user(DA_ET03):
			self.assertNotIn("kebeles", profile_for())  # posted nowhere
		with as_user(COMMS_NATIONAL):
			self.assertNotIn("kebeles", profile_for())

	def test_source_is_stored_on_the_notification(self):
		ctx = COMMS_NATIONAL
		with as_user(ctx):
			svc.broadcast(title="Heavy rain", scope="woreda", scope_value="ET04-W01", source="Weather")
		with as_user(DA_W01):
			rows, _meta = svc.list_notifications(limit=100)
		self.assertEqual([r["source"] for r in rows if r["title"] == "Heavy rain"], ["Weather"])

	def test_unknown_source_is_rejected(self):
		ctx = COMMS_NATIONAL
		with self.assertRaises(frappe.ValidationError):
			with as_user(ctx):
				svc.broadcast(title="x", scope="woreda", scope_value="ET04-W01", source="Random")

	def test_a_sender_without_area_grants_may_broadcast_to_all(self):
		ctx = "Administrator"
		with as_user(ctx):
			out = svc.broadcast(title="admin all", scope="all")
		self.assertEqual(out["recipients"], 3)

	# -- idempotency ----------------------------------------------------------

	def test_same_client_op_id_does_not_resend(self):
		ctx = COMMS_NATIONAL
		with as_user(ctx):
			first = svc.broadcast(title="once", scope="all", client_op_id="bc-op-0001")
		with as_user(ctx):
			second = svc.broadcast(title="once", scope="all", client_op_id="bc-op-0001")
		self.assertFalse(first["duplicate"])
		self.assertTrue(second["duplicate"])
		self.assertEqual(first["broadcast_id"], "bc-op-0001")
		self.assertEqual(second["recipients"], 3)
		self.assertEqual(len(self._rows(DA_W01)), 1)
		# One record, guarded by the unique client_op_id (the database-level check)
		self.assertEqual(frappe.db.count("DA Broadcast", {"client_op_id": "bc-op-0001"}), 1)

	def test_broadcast_record_written(self):
		ctx = COMMS_NATIONAL
		with as_user(ctx):
			out = svc.broadcast(
				title="recorded", body="b", href="/notifications", scope="region", scope_value="ET04"
			)
		row = frappe.db.get_value(
			"DA Broadcast",
			{"client_op_id": ("is", "not set"), "title": "recorded"},
			["name", "sent_by", "scope", "scope_value", "recipients", "href"],
			as_dict=True,
		)
		self.assertEqual(out["broadcast_id"], row.name)  # no client_op_id: the record name is the id
		self.assertEqual(row.sent_by, COMMS_NATIONAL)
		self.assertEqual(
			(row.scope, row.scope_value, row.recipients, row.href), ("region", "ET04", 2, "/notifications")
		)

	def test_failed_send_leaves_nothing_behind(self):
		"""Atomic: if sending stops half-way, neither the record nor partial notifications remain."""
		from unittest.mock import patch

		ctx = COMMS_NATIONAL
		real_notify = svc.notify
		calls = []

		def flaky(user, **kw):
			calls.append(user)
			if len(calls) == 2:
				raise RuntimeError("boom")
			return real_notify(user, **kw)

		with patch.object(svc, "notify", side_effect=flaky), self.assertRaises(RuntimeError):
			with as_user(ctx):
				svc.broadcast(title="partial", scope="all", client_op_id="bc-op-atomic")
		self.assertEqual(frappe.db.count("DA Broadcast", {"client_op_id": "bc-op-atomic"}), 0)
		for u in (DA_W01, DA_W02, DA_ET03):
			self.assertEqual(self._rows(u), [])

	# -- validation + audit ---------------------------------------------------

	def test_validation(self):
		ctx = COMMS_NATIONAL
		with self.assertRaises(frappe.ValidationError):
			with as_user(ctx):
				svc.broadcast(title="", scope="all")
		with self.assertRaises(frappe.ValidationError):
			with as_user(ctx):
				svc.broadcast(title="x", scope="woreda")  # scope_value missing
		with self.assertRaises(frappe.ValidationError):
			with as_user(ctx):
				svc.broadcast(title="x", scope="planet")

	def test_href_must_be_a_portal_path(self):
		ctx = COMMS_NATIONAL
		for bad in ("https://evil.example/x", "//evil.example", "notifications", "/a b"):
			with self.assertRaises(frappe.ValidationError, msg=bad):
				with as_user(ctx):
					svc.broadcast(title="x", scope="all", href=bad)
		with as_user(ctx):
			out = svc.broadcast(title="ok", scope="all", href=" /notifications ")
		self.assertEqual(out["recipients"], 3)

	def test_scope_with_no_active_da_is_rejected(self):
		"""A typo in scope_value must not silently send to nobody."""
		ctx = COMMS_NATIONAL
		with self.assertRaises(frappe.ValidationError):
			with as_user(ctx):
				svc.broadcast(title="x", scope="woreda", scope_value="ET99-W99")
		self.assertEqual(frappe.db.count("DA Broadcast", {"scope_value": "ET99-W99"}), 0)

	def test_audit_event_written(self):
		ctx = COMMS_NATIONAL
		with as_user(ctx):
			out = svc.broadcast(title="audited", scope="woreda", scope_value="ET04-W01")
		rows = frappe.get_all(
			"DA Audit Event",
			filters={"action": svc.BROADCAST_SENT, "entity_name": out["broadcast_id"]},
			fields=["user", "decision", "details"],
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].user, COMMS_NATIONAL)
		self.assertEqual(rows[0].decision, "allowed")
		# A JSON column reads back as a string on MariaDB and as a dict on PostgreSQL.
		self.assertEqual(frappe.parse_json(rows[0].details)["recipients"], 1)

	# -- API ------------------------------------------------------------------

	def test_api_requires_comms_or_admin(self):
		frappe.set_user(DA_W01)
		frappe.form_dict.update({"title": "nope", "scope": "all"})
		out = api.broadcast()
		self.assertEqual(out["status"], "error")
		self.assertEqual(frappe.response.get("http_status_code"), 403)

	def test_api_sends_and_shows_in_bell(self):
		frappe.set_user(COMMS_NATIONAL)
		frappe.form_dict.update({"title": "Bell check", "body": "hello", "scope": "all"})
		out = api.broadcast()
		self.assertEqual(out["data"]["recipients"], 3)

		frappe.set_user(DA_W01)
		frappe.form_dict.clear()
		listed = api.list_notifications(unread="1", limit=10)
		titles = [r["title"] for r in listed["data"]]
		self.assertIn("Bell check", titles)
		self.assertGreaterEqual(listed["meta"]["unread_count"], 1)
