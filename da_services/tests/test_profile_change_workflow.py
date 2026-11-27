"""The Frappe Workflow of DA Profile Change Request: who may take which step.

These tests drive the Workflow the app creates (``workflows/profile_change.py``): what
``workflow.actions`` offers each person, and steps taken with ``workflow.apply``,
with a small DA Hierarchy:

    root ─ Supervisor A ─ the DA
         └ Supervisor B              (another team)
    Supervisor N has no node         (so no User Permission at all)
"""

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase

from da_services.da_services.doctype.da_hierarchy.da_hierarchy import ensure_root
from da_services.da_services.doctype.da_profile_change_request import da_profile_change_request as states
from da_services.services import constants as C
from da_services.tests.fixtures import as_user
from da_services.workflows import profile_change as wf

PCR = wf.DOCTYPE
HIERARCHY = "DA Hierarchy"
# Frappe refuses a step with a validation error (not a valid action, no self approval) or a
# permission error (the caller cannot read or save the request).
REFUSED = (frappe.ValidationError, frappe.PermissionError)
DA = "pcw-da@da.local"
DA_OTHER = "pcw-da2@da.local"  # a DA of Supervisor B
DA_NO_NODE = "pcw-da3@da.local"  # a DA with no place in the hierarchy, so no User Permission
SUP_A = "pcw-supa@da.local"
SUP_B = "pcw-supb@da.local"
SUP_N = "pcw-supn@da.local"
ADMIN = "pcw-admin@da.local"
SUP_ADMIN = "pcw-supadmin@da.local"  # above the DA and also an OAN Administrator
TEST_USERS = (DA, DA_OTHER, DA_NO_NODE, SUP_A, SUP_B, SUP_N, ADMIN, SUP_ADMIN)
DA_ID = "DA-PCW-0001"


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


def _node(user, parent=None, group=0):
	if frappe.db.exists(HIERARCHY, user):
		return
	frappe.get_doc(
		{"doctype": HIERARCHY, "user": user, "is_group": group, "parent_da_hierarchy": parent}
	).insert(ignore_permissions=True)


def _clean():
	"""Remove only what these tests create."""
	names = frappe.get_all(PCR, filters={"da_id": DA_ID}, pluck="name")
	if names:
		frappe.db.delete("Comment", {"reference_doctype": PCR, "reference_name": ("in", names)})
		frappe.db.delete("Version", {"ref_doctype": PCR, "docname": ("in", names)})
	frappe.db.delete(PCR, {"da_id": DA_ID})
	for user in (ADMIN, DA, DA_OTHER, SUP_B, SUP_A, SUP_ADMIN):  # leaves before their groups
		if frappe.db.exists(HIERARCHY, user):
			frappe.delete_doc(HIERARCHY, user, ignore_permissions=True, force=True)
	frappe.db.delete("User Permission", {"user": ("in", TEST_USERS), "allow": "User"})


def _request(role=C.ROLE_DA, section="Contact", user=DA, status=None):
	"""A request about the DA, sent by the signed-in user."""
	doc = frappe.new_doc(PCR)
	doc.update(
		{
			"da_id": DA_ID,
			"da_name": "Workflow Test DA",
			"user": user,
			"section": section,
			"requested_by": frappe.session.user,
			"initiated_by_role": role,
			"reason": "A new phone number",
			"changes": frappe.as_json({"mobile": {"from": "+251911000000", "to": "+251911999888"}}),
		}
	)
	if status:
		doc.status = status
	with wf.sending():  # as the service inserts a request
		doc.insert()
	return doc


def _actions(name):
	"""What the signed-in user is offered on a request they may read."""
	doc = frappe.get_doc(PCR, name)
	doc.check_permission("read")
	return sorted(wf.actions(doc))


def _act(name, action):
	"""Take a step as the service does: the Workflow's step, then who gave level 1."""
	doc = frappe.get_doc(PCR, name)
	level_1 = doc.status == states.PENDING_SUPERVISOR and action != wf.CANCEL
	doc = wf.apply(doc, action)
	if level_1:
		doc.db_set("l1_by", frappe.session.user, update_modified=False)
	return doc


class TestProfileChangeWorkflow(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		wf.ensure_workflow()
		# a run that died while a test had the Workflow switched off or half saved
		frappe.db.set_value("Workflow", {"document_type": PCR}, "is_active", 1)
		frappe.cache.hdel("workflow", PCR)
		_clean()
		_user(DA, [C.ROLE_DA])
		_user(DA_OTHER, [C.ROLE_DA])
		_user(DA_NO_NODE, [C.ROLE_DA])
		_user(SUP_A, [C.ROLE_SUPERVISOR])
		_user(SUP_B, [C.ROLE_SUPERVISOR])
		_user(SUP_N, [C.ROLE_SUPERVISOR])
		_user(ADMIN, [C.ROLE_ADMIN])
		_user(SUP_ADMIN, [C.ROLE_SUPERVISOR, C.ROLE_ADMIN])
		ensure_root()
		_node(SUP_ADMIN, group=1)
		_node(SUP_A, parent=SUP_ADMIN, group=1)
		_node(DA, parent=SUP_A)
		_node(SUP_B, group=1)
		_node(DA_OTHER, parent=SUP_B)
		self.addCleanup(self._cleanup)

	def _cleanup(self):
		frappe.set_user("Administrator")
		_clean()

	def _sent_by_the_da(self, section="Contact"):
		with as_user(DA):
			return _request(section=section).name

	def _at_level_2(self):
		name = self._sent_by_the_da()
		with as_user(SUP_A):
			_act(name, wf.APPROVE)
		return name

	# --- the Workflow document

	def test_the_app_creates_the_workflow_with_the_states_of_the_status_field(self):
		doc = frappe.get_doc("Workflow", {"document_type": PCR, "is_active": 1})
		self.assertEqual(doc.workflow_state_field, "status")
		# the state field is a Link to Workflow State: a state added in the Desk needs no schema change
		field = frappe.get_meta(PCR).get_field("status")
		self.assertEqual((field.fieldtype, field.options), ("Link", "Workflow State"))
		self.assertEqual([s.state for s in doc.states], [state for state, _role in wf.STATES])
		self.assertEqual(doc.states[0].state, states.PENDING_SUPERVISOR)  # where a new request starts
		self.assertEqual({s.doc_status for s in doc.states}, {"0"})
		# the system writes these two; no button leads to them
		reachable = {t.next_state for t in doc.transitions}
		self.assertNotIn(states.APPLIED, reachable)
		self.assertNotIn(states.REGISTRY_FAILED, reachable)

	def test_a_workflow_changed_in_the_desk_survives_the_next_migrate(self):
		doc = frappe.get_doc("Workflow", {"document_type": PCR, "is_active": 1})
		row = next(s for s in doc.states if s.state == states.CLOSED)
		before = row.allow_edit
		self.addCleanup(lambda: frappe.db.set_value(row.doctype, row.name, "allow_edit", before))
		frappe.db.set_value(row.doctype, row.name, "allow_edit", C.ROLE_EXECUTIVE)
		frappe.clear_document_cache("Workflow", doc.name)

		self.assertEqual(wf.ensure_workflow(), doc.name)
		self.assertEqual(frappe.db.count("Workflow", {"document_type": PCR}), 1)
		self.assertEqual(frappe.db.get_value(row.doctype, row.name, "allow_edit"), C.ROLE_EXECUTIVE)

	# --- sending

	def test_a_request_starts_at_pending_supervisor_and_nowhere_else(self):
		with as_user(DA):
			self.assertEqual(_request().status, states.PENDING_SUPERVISOR)
			with self.assertRaises(frappe.ValidationError):
				_request(section="Identity", status=states.PENDING_ADMINISTRATOR)
			with self.assertRaises(frappe.ValidationError):
				_request(section="Location", status=states.APPLIED)

	def test_a_supervisors_request_goes_straight_to_the_administrator(self):
		with as_user(SUP_A):
			name = _request(role=C.ROLE_SUPERVISOR).name
			doc = _act(name, wf.SUBMIT_FOR_DA)
		self.assertEqual(doc.status, states.PENDING_ADMINISTRATOR)
		self.assertEqual(  # recorded on the request as the Supervisor's step
			[(step["action"], step["to"], step["by"]) for step in wf.steps(doc)],
			[(wf.SUBMIT_FOR_DA, states.PENDING_ADMINISTRATOR, SUP_A)],
		)
		with as_user(DA):  # the DA's own request cannot take that shortcut
			own = _request(section="Identity").name
			self.assertNotIn(wf.SUBMIT_FOR_DA, _actions(own))

	# --- level 1

	def test_level_1_is_for_a_supervisor_above_the_da(self):
		name = self._sent_by_the_da()
		with as_user(SUP_A):
			self.assertEqual(_actions(name), [wf.APPROVE, wf.REJECT, wf.REQUEST_CHANGES])
		with as_user(SUP_ADMIN):  # two levels above the DA
			self.assertIn(wf.APPROVE, _actions(name))
		with as_user(SUP_B), self.assertRaises(frappe.PermissionError):  # another team: not even visible
			_actions(name)
		with as_user(SUP_N):  # the role alone, no place in the hierarchy
			self.assertEqual(_actions(name), [])
		with as_user(ADMIN):  # level 2 does not skip level 1
			self.assertEqual(_actions(name), [])
		with as_user(DA):
			self.assertEqual(_actions(name), [wf.CANCEL])
			with self.assertRaises(REFUSED):
				_act(name, wf.APPROVE)

	def test_level_1_approve_reject_and_request_changes(self):
		for section, action, state in (
			("Contact", wf.APPROVE, states.PENDING_ADMINISTRATOR),
			("Identity", wf.REJECT, states.REJECTED),
			("Location", wf.REQUEST_CHANGES, states.CHANGES_REQUESTED),
		):
			name = self._sent_by_the_da(section)
			with as_user(SUP_A):
				doc = _act(name, action)
			self.assertEqual(doc.status, state)
		approved = frappe.get_doc(PCR, {"da_id": DA_ID, "section": "Contact"})
		self.assertEqual(
			[(step["from"], step["by"]) for step in wf.steps(approved)], [(states.PENDING_SUPERVISOR, SUP_A)]
		)

	def test_nobody_decides_their_own_request(self):
		with as_user(SUP_A):
			# sent by the Supervisor for a DA below them and left at level 1
			sent = _request(role=C.ROLE_SUPERVISOR).name
			self.assertEqual(_actions(sent), [wf.CANCEL, wf.SUBMIT_FOR_DA])
			with self.assertRaises(REFUSED):
				_act(sent, wf.APPROVE)
			# about the Supervisor's own record: "above the DA" never means oneself
			own = _request(section="Identity", user=SUP_A).name
			self.assertEqual(_actions(own), [wf.CANCEL])
		with as_user(SUP_ADMIN):  # the person above them gives level 1
			self.assertIn(wf.APPROVE, _actions(own))

	def test_nobody_decides_a_request_about_their_own_record(self):
		# sent by someone else, and waiting at a level that is the subject's own to give
		_node(ADMIN, parent=SUP_A)  # an Administrator who is below this Supervisor in the hierarchy
		with as_user(SUP_A):
			name = _request(role=C.ROLE_SUPERVISOR, user=ADMIN).name
			_act(name, wf.SUBMIT_FOR_DA)
		with as_user(ADMIN):
			self.assertEqual(_actions(name), [])
			with self.assertRaises(REFUSED):
				_act(name, wf.APPROVE)
		with as_user(SUP_ADMIN):  # another Administrator decides it
			self.assertEqual(_actions(name), [wf.APPROVE, wf.REJECT])
		# and should such an approval ever be recorded, it is not the second person's
		doc = frappe.get_doc(PCR, name)
		doc.db_set("status", states.SENT_TO_REGISTRY)
		doc.db_set(
			"decisions",
			frappe.as_json(
				{
					"steps": [
						{
							"from": states.PENDING_ADMINISTRATOR,
							"action": wf.APPROVE,
							"to": states.SENT_TO_REGISTRY,
							"by": ADMIN,
						}
					]
				}
			),
		)
		self.assertFalse(wf.approved_by_another(frappe.get_doc(PCR, name)))

	# --- level 2

	def test_level_2_is_for_an_administrator_who_did_not_give_level_1(self):
		name = self._sent_by_the_da()
		with as_user(SUP_ADMIN):
			_act(name, wf.APPROVE)  # level 1, as a Supervisor above the DA
			self.assertEqual(_actions(name), [])  # and so no level 2 for the same person
			with self.assertRaises(REFUSED):
				_act(name, wf.APPROVE)
		with as_user(SUP_A):
			self.assertEqual(_actions(name), [])
		with as_user(ADMIN):
			self.assertEqual(_actions(name), [wf.APPROVE, wf.REJECT])
			doc = _act(name, wf.APPROVE)
		self.assertEqual(doc.status, states.SENT_TO_REGISTRY)

	def test_level_2_can_reject(self):
		name = self._at_level_2()
		with as_user(ADMIN):
			self.assertEqual(_act(name, wf.REJECT).status, states.REJECTED)
			self.assertEqual(_actions(name), [])

	# --- the registry's answer

	def test_applied_and_registry_failed_are_written_by_the_system_only(self):
		name = self._at_level_2()
		with as_user(ADMIN):
			_act(name, wf.APPROVE)
			doc = frappe.get_doc(PCR, name)
			doc.status = states.APPLIED
			with self.assertRaises(frappe.ValidationError):
				doc.save()  # not even an Administrator, by hand
		frappe.get_doc(PCR, name).db_set("status", states.APPLIED)
		with as_user(ADMIN):
			self.assertEqual(_actions(name), [])
			frappe.get_doc(PCR, name).save()  # and the row stays a valid one

	def test_a_failed_request_can_be_retried_or_closed_by_an_administrator(self):
		name = self._at_level_2()
		with as_user(ADMIN):
			_act(name, wf.APPROVE)
		frappe.get_doc(PCR, name).db_set("status", states.REGISTRY_FAILED)
		with as_user(SUP_A):
			self.assertEqual(_actions(name), [])
		with as_user(ADMIN):
			self.assertEqual(_actions(name), [wf.CLOSE, wf.RETRY])
			self.assertEqual(_act(name, wf.RETRY).status, states.SENT_TO_REGISTRY)
		frappe.get_doc(PCR, name).db_set("status", states.REGISTRY_FAILED)
		with as_user(ADMIN):
			self.assertEqual(_act(name, wf.CLOSE).status, states.CLOSED)

	# --- cancel

	def test_only_the_requester_cancels_and_only_while_it_waits(self):
		at_level_1 = self._sent_by_the_da()
		at_level_2 = self._sent_by_the_da("Identity")
		with as_user(SUP_A):
			self.assertNotIn(wf.CANCEL, _actions(at_level_1))
			_act(at_level_2, wf.APPROVE)
		with as_user(DA):
			self.assertEqual(_act(at_level_1, wf.CANCEL).status, states.CANCELLED)
			self.assertEqual(_act(at_level_2, wf.CANCEL).status, states.CANCELLED)
			self.assertEqual(_actions(at_level_1), [])

		with as_user(SUP_A):
			by_supervisor = _request(role=C.ROLE_SUPERVISOR, section="Location").name
			_act(by_supervisor, wf.SUBMIT_FOR_DA)
		with as_user(SUP_ADMIN):  # another Supervisor above the DA is not the requester
			self.assertNotIn(wf.CANCEL, _actions(by_supervisor))
		with as_user(SUP_A):
			self.assertEqual(_act(by_supervisor, wf.CANCEL).status, states.CANCELLED)

		sent = self._at_level_2()
		with as_user(ADMIN):
			_act(sent, wf.APPROVE)
		with as_user(DA):
			self.assertEqual(_actions(sent), [])  # it has left for the registry

	# --- what was asked stays what was asked

	def test_a_sent_request_cannot_be_rewritten(self):
		name = self._sent_by_the_da()
		for user in (DA, SUP_A, ADMIN):
			for field, value in (
				("reason", "something else"),
				("changes", frappe.as_json({"mobile": {"from": "+251911000000", "to": "+251900000000"}})),
				("section", "Identity"),
				("user", SUP_A),
			):
				with as_user(user):
					doc = frappe.get_doc(PCR, name)
					doc.set(field, value)
					# refused by the lock, or before it by the caller's own User Permissions
					with self.assertRaises(
						(frappe.ValidationError, frappe.PermissionError), msg=f"{user} {field}"
					):
						doc.save()
		self.assertEqual(frappe.db.get_value(PCR, name, "reason"), "A new phone number")

	# --- who sees a request

	def test_a_request_is_seen_by_the_da_and_by_those_above(self):
		# Frappe's own rule: the request names the DA's User, and DA Hierarchy gives each person
		# a User Permission on themselves and on everyone below them.
		name = self._at_level_2()
		with as_user(ADMIN):
			_act(name, wf.APPROVE)
		for user, sees in ((DA, True), (SUP_A, True), (SUP_ADMIN, True), (ADMIN, True)):
			with as_user(user):
				self.assertEqual(name in frappe.get_list(PCR, pluck="name", limit=0), sees, user)
		for user in (DA_OTHER, SUP_B):  # another team
			with as_user(user):
				self.assertNotIn(name, frappe.get_list(PCR, pluck="name", limit=0), user)
				self.assertFalse(frappe.has_permission(PCR, "read", frappe.get_doc(PCR, name)), user)

	# --- a step is taken through the service, not by a save or the Desk's buttons

	def test_frappes_own_workflow_button_is_refused(self):
		name = self._sent_by_the_da()
		with as_user(SUP_A), self.assertRaises(frappe.ValidationError) as ctx:
			apply_workflow(frappe.get_doc(PCR, name).as_dict(), wf.APPROVE)
		self.assertIn("through its steps only", str(ctx.exception))
		self.assertEqual(frappe.db.get_value(PCR, name, "status"), states.PENDING_SUPERVISOR)

	def test_a_plain_save_cannot_stand_in_for_a_step(self):
		with as_user(SUP_A):  # the Supervisor's own record: a save would be a self approval
			own = _request(role=C.ROLE_DA, user=SUP_A).name
			doc = frappe.get_doc(PCR, own)
			doc.status = states.PENDING_ADMINISTRATOR
			with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
				doc.save()
		name = self._sent_by_the_da()
		with as_user(SUP_ADMIN):
			_act(name, wf.APPROVE)
			for field, value in (
				("l1_by", None),  # the Workflow reads it: who gave level 1 does not give level 2
				("uploaded_document_id", "doc-1"),
				("l2_note", "x"),
				("owner", SUP_ADMIN),  # the Workflow takes the owner for the requester
			):
				doc = frappe.get_doc(PCR, name)
				doc.set(field, value)
				with self.assertRaises(frappe.ValidationError, msg=field):
					doc.save()
			self.assertEqual(_actions(name), [])
		self.assertEqual([step["by"] for step in wf.steps(frappe.get_doc(PCR, name))], [SUP_ADMIN])

	def test_a_request_is_not_created_outside_the_service(self):
		# whom it is about, who sent it and its steps are never what a form or an API call carried
		with as_user(DA):  # everything here is the DA's own, so only the controller can refuse it
			doc = frappe.new_doc(PCR)
			doc.update(
				{
					"da_id": DA_ID,
					"user": DA,
					"section": "Contact",
					"requested_by": DA,
					"initiated_by_role": C.ROLE_DA,
					"changes": "{}",
					"decisions": frappe.as_json({"steps": [{"to": states.SENT_TO_REGISTRY, "by": SUP_A}]}),
				}
			)
			with self.assertRaises(frappe.PermissionError) as ctx:
				doc.insert()
		self.assertIn("sent from the DA portal", str(ctx.exception))

	def test_nothing_is_sent_to_the_registry_without_a_second_persons_approval(self):
		from da_services.services import profile as svc

		with as_user(SUP_ADMIN):  # can retry, and sent this request
			name = _request(role=C.ROLE_SUPERVISOR).name
		# however the row got to Sent to registry (a Workflow changed in the Desk, say)
		frappe.get_doc(PCR, name).db_set("status", states.SENT_TO_REGISTRY)
		with as_user(SUP_ADMIN), self.assertRaises(frappe.PermissionError) as ctx:
			svc._send_to_registry(frappe.get_doc(PCR, name))
		self.assertIn("second person", str(ctx.exception))
		self.assertFalse(wf.approved_by_another(frappe.get_doc(PCR, name)))
		# and the same row once someone else has approved it
		frappe.get_doc(PCR, name).db_set("status", states.PENDING_ADMINISTRATOR)
		with as_user(ADMIN):
			_act(name, wf.APPROVE)
		self.assertTrue(wf.approved_by_another(frappe.get_doc(PCR, name)))
		# a level-2 name on a request whose recorded decisions hold no last approval does not count
		with as_user(DA):
			rejected = _request(section="Identity").name
		with as_user(SUP_A):
			_act(rejected, wf.REJECT)
		frappe.get_doc(PCR, rejected).db_set("l2_by", ADMIN)
		self.assertFalse(wf.approved_by_another(frappe.get_doc(PCR, rejected)))

	def test_without_an_active_workflow_reading_goes_on_and_steps_are_refused(self):
		name = self._sent_by_the_da()
		workflow = frappe.db.get_value("Workflow", {"document_type": PCR, "is_active": 1}, "name")

		def switch(active):
			frappe.db.set_value("Workflow", workflow, "is_active", active)
			frappe.cache.hdel("workflow", PCR)

		self.addCleanup(switch, 1)
		switch(0)
		with as_user(SUP_A):
			self.assertEqual(wf.actions(frappe.get_doc(PCR, name)), [])
			self.assertIn(name, frappe.get_list(PCR, pluck="name", limit=0))
			with self.assertRaises(frappe.ValidationError):
				_act(name, wf.APPROVE)

	def test_every_step_is_in_the_history_frappe_keeps(self):
		name = self._at_level_2()
		with as_user(ADMIN):
			_act(name, wf.APPROVE)
		steps = frappe.get_all(
			"Comment",
			filters={"reference_doctype": PCR, "reference_name": name, "comment_type": "Workflow"},
			fields=["content", "comment_email"],
			order_by="creation asc, name asc",
		)
		self.assertEqual(  # two lines; their order is the timeline's, not asserted here
			{(s.content, s.comment_email) for s in steps},
			{(states.PENDING_ADMINISTRATOR, SUP_A), (states.SENT_TO_REGISTRY, ADMIN)},
		)
