"""The Frappe Workflow of DA Profile Change Request.

    Pending Supervisor ──Approve──▶ Pending Administrator ──Approve──▶ Sent to registry ─▶ Applied
            │                          ▲         │                            │
            │      Submit for DA ──────┘         ├─Reject─▶ Rejected          └─▶ Registry failed
            ├─Reject─▶ Rejected                  └─Cancel─▶ Cancelled               ├─Retry─▶ Sent to registry
            ├─Request Changes─▶ Changes requested                                   └─Close─▶ Closed
            └─Cancel─▶ Cancelled

That is the Workflow this app creates on a site that has none. The document itself is the
site's: an administrator can change it in the Desk, with another role on a step, another
condition, fewer levels or more (a Workflow State, a state row, and the transitions into
and out of it). Nothing else in the app names a level; it asks this module, which reads
the site's Workflow:

- the **system's states** are Sent to registry, Applied and Registry failed, and keep
  their names: the app writes Applied and Registry failed itself, after the DA Registry
  answers, and no transition leads to them;
- the **requester's steps** are Cancel and Submit for DA: only the person who sent a
  request takes them;
- every other transition out of a state that is not the system's is a **decision**
  (Approve, Reject, Request Changes, or one an administrator added): a request **waits**
  in a state that a decision leaves, and the decision that leads to Sent to registry is
  the last approval, whose giver sends the change to the DA Registry with their own token.

Rules the app holds for every Workflow, in ``actions`` (so they do not depend on how a row
was ticked in the Desk):

- nobody decides a request they sent, or one about their own record;
- one person decides a request at one level only, read from the steps recorded on the
  request (``decisions``), never from something a user can edit;
- only the requester takes the requester's steps.

``validate_shape`` (a hook on Workflow) refuses to save a Workflow of this DocType that
would undo them or that the app could not run: it says what to change. The rule "a
Supervisor above the DA" is a condition on the transitions (``ABOVE_THE_DA``), because a
role alone does not express it; an administrator copies it to a level that should follow
DA Hierarchy.

A step is taken with ``apply``, which the service calls. A step also records who decided,
tells people and may send the change to the registry, so the Desk's Workflow buttons are
routed to the same service (``workflows/desk.py``) instead of to Frappe's own
``apply_workflow``, which knows none of that. The controller refuses every other change of
a sent request.

Who sees a request (``query_conditions`` and ``has_permission``, hooked in hooks.py): Frappe
lets a row through when its Link is empty and lets a user with no User Permission see every
row, so the rule is stated here instead of left to the ``user`` Link alone.
"""

from __future__ import annotations

from contextlib import contextmanager

import frappe
from frappe import _
from frappe.model.workflow import (
	apply_workflow,
	get_workflow_name,
	get_workflow_safe_globals,
	has_approval_access,
)
from frappe.utils import cint, now_datetime

from da_services.da_services.doctype.da_profile_change_request import da_profile_change_request as states
from da_services.services import constants as C

DOCTYPE = "DA Profile Change Request"
WORKFLOW = "DA Profile Change Request"
STATE_FIELD = "status"

APPROVE = "Approve"
REJECT = "Reject"
REQUEST_CHANGES = "Request Changes"
CANCEL = "Cancel"
SUBMIT_FOR_DA = "Submit for DA"
RETRY = "Retry"
CLOSE = "Close"
ACTIONS = (APPROVE, REJECT, REQUEST_CHANGES, CANCEL, SUBMIT_FOR_DA, RETRY, CLOSE)
REQUESTER_ACTIONS = (CANCEL, SUBMIT_FOR_DA)
SYSTEM_STATES = (states.SENT_TO_REGISTRY, states.APPLIED, states.REGISTRY_FAILED)
# Frappe's role of every signed-in user: for a step whose rule is not a role.
EVERYONE = "All"
# The name the DA Registry's own steps are recorded under.
REGISTRY = "DA Registry"

# The caller is above the DA in DA Hierarchy: an explicit User Permission on the DA's User,
# which is not their own. A request with no User (the DA has never signed in, or the row is
# from before requests named one) falls to a User Permission on the DA's Woreda.
ABOVE_THE_DA = (
	'(doc.user and doc.user != frappe.session.user and frappe.db.get_value("User Permission", '
	'{"user": frappe.session.user, "allow": "User", "for_value": doc.user}, "name")) '
	'or (not doc.user and doc.woreda and frappe.db.get_value("User Permission", '
	'{"user": frappe.session.user, "allow": "DA Woreda", "for_value": doc.woreda}, "name"))'
)
IS_THE_REQUESTER = "doc.owner == frappe.session.user"
A_SUPERVISORS_REQUEST = f'doc.initiated_by_role == "{C.ROLE_SUPERVISOR}" and {IS_THE_REQUESTER}'

# (state, the role that may edit the form in the Desk)
STATES = (
	(states.PENDING_SUPERVISOR, C.ROLE_SUPERVISOR),
	(states.PENDING_ADMINISTRATOR, C.ROLE_ADMIN),
	(states.SENT_TO_REGISTRY, C.ROLE_ADMIN),
	(states.APPLIED, C.ROLE_ADMIN),
	(states.REGISTRY_FAILED, C.ROLE_ADMIN),
	(states.REJECTED, C.ROLE_ADMIN),
	(states.CHANGES_REQUESTED, C.ROLE_ADMIN),
	(states.CANCELLED, C.ROLE_ADMIN),
	(states.CLOSED, C.ROLE_ADMIN),
)

# (state, action, next state, role, allow self approval, condition)
TRANSITIONS = (
	(states.PENDING_SUPERVISOR, APPROVE, states.PENDING_ADMINISTRATOR, C.ROLE_SUPERVISOR, 0, ABOVE_THE_DA),
	(states.PENDING_SUPERVISOR, REJECT, states.REJECTED, C.ROLE_SUPERVISOR, 0, ABOVE_THE_DA),
	(
		states.PENDING_SUPERVISOR,
		REQUEST_CHANGES,
		states.CHANGES_REQUESTED,
		C.ROLE_SUPERVISOR,
		0,
		ABOVE_THE_DA,
	),
	(states.PENDING_SUPERVISOR, CANCEL, states.CANCELLED, EVERYONE, 1, IS_THE_REQUESTER),
	# A Supervisor's own request for a DA: the Supervisor is level 1, so it goes straight on.
	(
		states.PENDING_SUPERVISOR,
		SUBMIT_FOR_DA,
		states.PENDING_ADMINISTRATOR,
		C.ROLE_SUPERVISOR,
		1,
		A_SUPERVISORS_REQUEST,
	),
	(states.PENDING_ADMINISTRATOR, APPROVE, states.SENT_TO_REGISTRY, C.ROLE_ADMIN, 0, None),
	(states.PENDING_ADMINISTRATOR, REJECT, states.REJECTED, C.ROLE_ADMIN, 0, None),
	(states.PENDING_ADMINISTRATOR, CANCEL, states.CANCELLED, EVERYONE, 1, IS_THE_REQUESTER),
	(states.REGISTRY_FAILED, RETRY, states.SENT_TO_REGISTRY, C.ROLE_ADMIN, 1, None),
	(states.REGISTRY_FAILED, CLOSE, states.CLOSED, C.ROLE_ADMIN, 1, None),
)


def definition() -> dict:
	"""The Workflow document this app creates on a site that has none."""
	return {
		"doctype": "Workflow",
		"workflow_name": WORKFLOW,
		"document_type": DOCTYPE,
		"is_active": 1,
		"workflow_state_field": STATE_FIELD,
		"send_email_alert": 0,
		"states": [{"state": state, "doc_status": "0", "allow_edit": role} for state, role in STATES],
		"transitions": [
			{
				"state": state,
				"action": action,
				"next_state": next_state,
				"allowed": role,
				"allow_self_approval": self_approval,
				"condition": condition,
			}
			for state, action, next_state, role, self_approval, condition in TRANSITIONS
		],
	}


def ensure_workflow() -> str | None:
	"""Create the Workflow when the site has none for the DocType; return its name.

	Run on install and on every migrate. A site that already has a Workflow for the DocType,
	this one after a change in the Desk or another one, keeps it: nothing is overwritten.
	"""
	for state, _role in STATES:
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": state}).insert(
				ignore_permissions=True
			)
	for action in ACTIONS:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(
				ignore_permissions=True
			)
	existing = frappe.db.get_value("Workflow", {"document_type": DOCTYPE}, "name", order_by="is_active desc")
	if existing:
		return existing
	return frappe.get_doc(definition()).insert(ignore_permissions=True).name


# -- the shape of the site's Workflow -----------------------------------------------------


def _workflow():
	"""The site's active Workflow of the DocType, or None when it has none."""
	name = get_workflow_name(DOCTYPE)
	return frappe.get_cached_doc("Workflow", name) if name else None


def _transitions() -> list:
	workflow = _workflow()
	return list(workflow.transitions) if workflow else []


def _is_decision(transition) -> bool:
	return transition.state not in SYSTEM_STATES and transition.action not in REQUESTER_ACTIONS


def state_names() -> list[str]:
	"""Every state a request can be in, in the Workflow's order."""
	workflow = _workflow()
	return [s.state for s in workflow.states] if workflow else [state for state, _role in STATES]


def first_state() -> str:
	"""Where every request starts."""
	workflow = _workflow()
	return workflow.states[0].state if workflow and workflow.states else states.PENDING_SUPERVISOR


def waiting_states() -> list[str]:
	"""The states in which a request waits for a person's decision, in the Workflow's order."""
	if not _workflow():
		return list(states.PENDING_STATES)
	left_by_a_decision = {t.state for t in _transitions() if _is_decision(t)}
	return [state for state in state_names() if state in left_by_a_decision]


def open_states() -> list[str]:
	"""The states of a request that is not finished. A DA has at most one such request per
	field: the registry also takes one pending change per DA and section, and a failed push
	is retried or closed before the next one."""
	return [*waiting_states(), states.SENT_TO_REGISTRY, states.REGISTRY_FAILED]


def deciding_roles(state: str | None = None) -> set[str]:
	"""The roles that can decide a request waiting in ``state`` (in any state, without one)."""
	return {t.allowed for t in _transitions() if _is_decision(t) and state in (None, t.state)}


def roles_for(state: str, action: str | None = None) -> set[str]:
	"""The roles the Workflow lets act on a request in ``state`` (with ``action``, or any)."""
	return {t.allowed for t in _transitions() if t.state == state and action in (None, t.action)}


def leads_to(state: str, action: str) -> set[str]:
	"""The states ``action`` moves a request to from ``state``."""
	return {t.next_state for t in _transitions() if t.state == state and t.action == action}


def bound_to_hierarchy(state: str) -> bool:
	"""Whether the decisions of ``state`` carry the hierarchy condition (a best reading of
	the condition's text, used only to choose whom to tell that a request waits)."""
	conditions = [t.condition or "" for t in _transitions() if t.state == state and _is_decision(t)]
	return bool(conditions) and all("User Permission" in c for c in conditions)


def validate_shape(doc, method=None) -> None:
	"""``Workflow.validate``: refuse a Workflow of this DocType the app could not run safely.

	Each refusal names the row and what to change. The Desk ticks "Allow Self Approval" on a
	new transition row by default, so the commonest mistake is caught here, at save.
	"""
	if doc.document_type != DOCTYPE or not cint(doc.is_active):
		return
	problems = []
	names = [row.state for row in doc.states]
	if doc.workflow_state_field != STATE_FIELD:
		problems.append(_("Workflow State Field must be {0}.").format(STATE_FIELD))
	if doc.get("send_email_alert"):
		# Frappe's emails carry links that take the step outside the app's service, which the
		# request refuses: the app tells the people concerned itself.
		problems.append(_("Clear Send Email Alert: the app tells the people concerned itself."))
	for state in SYSTEM_STATES:
		if state not in names:
			problems.append(_("The state {0} is the system's and must stay.").format(frappe.bold(state)))
	for row in doc.states:
		if row.update_field:
			# who decided, what was sent to the registry and where: every field of a request is
			# written by the app, and a state row that set one would write over that
			problems.append(
				_(
					"State row {0} ({1}): clear Update Field ({2}). The app records what a step changes; "
					"a state cannot set a field of a change request."
				).format(row.idx, row.state, row.update_field)
			)
	if names and names[0] in SYSTEM_STATES:
		problems.append(_("The first state is where a request starts: it cannot be {0}.").format(names[0]))
	decisions_from = {t.state for t in doc.transitions if _is_decision(t)}
	leads: dict[tuple, set] = {}
	for t in doc.transitions:
		leads.setdefault((t.state, t.action), set()).add(t.next_state)
	bound = {
		t.allowed for t in doc.transitions if _is_decision(t) and "User Permission" in (t.condition or "")
	}
	for t in doc.transitions:
		row = _("Transition row {0} ({1}: {2})").format(t.idx, t.state, t.action)
		if len(leads[(t.state, t.action)]) > 1:
			problems.append(
				_("{0}: {1} from {2} leads to more than one state; give the steps different actions.").format(
					row, t.action, t.state
				)
			)
		if t.allowed == EVERYONE and t.action not in REQUESTER_ACTIONS:
			problems.append(
				_("{0}: the role All is for the requester's own steps (Cancel, Submit for DA) only.").format(
					row
				)
			)
		if t.state == states.REGISTRY_FAILED and t.allowed in bound:
			problems.append(
				_(
					"{0}: {1} decides for the people below them in DA Hierarchy. Whoever retries or closes "
					"what the registry did not take sees every request, so give this step to another role."
				).format(row, t.allowed)
			)
		if t.action in REQUESTER_ACTIONS and t.next_state in SYSTEM_STATES:
			problems.append(_("{0}: a requester's own step cannot lead to {1}.").format(row, t.next_state))
		if t.get("transition_tasks"):
			problems.append(
				_(
					"{0}: clear Transition Tasks. A task runs inside the step and could write over what the "
					"app records on a change request."
				).format(row)
			)
		if t.next_state in (states.APPLIED, states.REGISTRY_FAILED):
			problems.append(
				_(
					"{0}: {1} is written by the system after the DA Registry answers; no step leads to it."
				).format(row, t.next_state)
			)
		if t.state in (states.SENT_TO_REGISTRY, states.APPLIED):
			problems.append(_("{0}: nothing leaves {1}.").format(row, t.state))
		if t.action in REQUESTER_ACTIONS and t.state not in SYSTEM_STATES:
			if not cint(t.allow_self_approval):
				problems.append(
					_("{0}: tick Allow Self Approval. This is the requester's own step.").format(row)
				)
			if t.action == SUBMIT_FOR_DA and t.next_state not in decisions_from:
				problems.append(
					_(
						"{0}: it must lead to a state where the request waits for someone's decision, "
						"so that no request reaches the DA Registry unseen by a second person."
					).format(row)
				)
		elif _is_decision(t) and cint(t.allow_self_approval):
			problems.append(
				_(
					"{0}: untick Allow Self Approval. A decision is never for the person who sent the request."
				).format(row)
			)
	last_approvals = [
		t for t in doc.transitions if t.next_state == states.SENT_TO_REGISTRY and _is_decision(t)
	]
	if not last_approvals:
		problems.append(
			_("No decision leads to {0}: a request could never be approved.").format(states.SENT_TO_REGISTRY)
		)
	held = frappe.get_all(
		DOCTYPE,
		filters=[[STATE_FIELD, "not in", names], [STATE_FIELD, "is", "set"]],
		fields=[STATE_FIELD, {"COUNT": "*", "as": "requests"}],
		group_by=STATE_FIELD,
		order_by=STATE_FIELD,
	)
	for row in held:
		problems.append(
			_("{0} change requests are at {1}: finish or move them before the state is removed.").format(
				row.requests, frappe.bold(row.get(STATE_FIELD))
			)
		)
	if problems:
		frappe.throw(
			_("This Workflow of {0} cannot be saved:").format(DOCTYPE) + "<br>" + "<br>".join(problems),
			title=_("Workflow not valid for profile change requests"),
		)


# -- the steps of a request -----------------------------------------------------------------


def steps(row) -> list[dict]:
	"""The steps recorded on a request, oldest first: {from, action, to, by, at, note}; a step
	of the system has no ``by`` and names the system in ``system``.

	``decisions`` holds ``{"steps": [...]}``: an object, because PostgreSQL hands a JSON
	column back parsed and Frappe refuses a list as the value of a field.
	"""
	recorded = row.get("decisions")
	if isinstance(recorded, str):
		recorded = frappe.parse_json(recorded) if recorded else None
	if isinstance(recorded, dict):
		recorded = recorded.get("steps")
	return list(recorded or [])


def is_decision(state: str, action: str) -> bool:
	"""Is ``action`` from ``state`` a person's decision (not the requester's own step, not a
	step on what the registry did not take)?"""
	return state not in SYSTEM_STATES and action not in REQUESTER_ACTIONS


def approved_by_another(row) -> bool:
	"""Has someone other than the person who sent the request, and other than the person it
	is about, given the approval that leads to the registry? The one thing checked again
	right before anything is sent there.

	A request from before steps were recorded has ``l2_by``.
	"""
	senders = {row.get("owner"), row.get("requested_by"), row.get("user")} - {None, ""}
	decisions = [
		step for step in steps(row) if step.get("by") and is_decision(step.get("from"), step.get("action"))
	]
	if decisions:
		return any(
			step.get("to") == states.SENT_TO_REGISTRY and step.get("by") not in senders for step in decisions
		)
	# No decision recorded: a request from before steps were. Its level-2 approver, which only
	# the service writes (no save can, and ``validate_shape`` lets no state row set a field).
	return bool(row.get("l2_by")) and row.get("l2_by") not in senders


@contextmanager
def sending():
	"""Around the insert of a new request by the service: the controller refuses any other
	insert, so what a request says (whom it is about, who sent it, its steps) is never
	what a caller typed into a form or an API."""
	frappe.flags[states.SEND_FLAG] = True
	try:
		yield
	finally:
		frappe.flags[states.SEND_FLAG] = None


def _decided_elsewhere(row, user: str) -> bool:
	"""Has this person decided the request at another level than the one it waits at?

	A request that came back to a level (a "send back" an administrator added) may be
	decided there again by the same person; another level may not. A request from before
	steps were recorded has only ``l1_by``.
	"""
	recorded = steps(row)
	if not recorded:
		return bool(row.get("l1_by")) and row.get("l1_by") == user
	return any(
		step.get("by") == user
		and step.get("from") != row.get(STATE_FIELD)
		and step.get("action") not in REQUESTER_ACTIONS
		and step.get("from") not in SYSTEM_STATES
		for step in recorded
	)


def decided_elsewhere(row) -> set[str]:
	"""Everyone who may not decide the request where it waits now because they decided it at
	another level."""
	recorded = steps(row)
	people = {step.get("by") for step in recorded if step.get("by")} | {row.get("l1_by")}
	return {user for user in people if user and _decided_elsewhere(row, user)}


def actions(row) -> list[str]:
	"""The Workflow actions the session user may take on a request now, in the Workflow's order.

	``row`` is the document or a row with ``status``, ``owner``, ``requested_by``,
	``decisions`` and the fields the conditions read (``user``, ``woreda``,
	``initiated_by_role``). Frappe's own rules (the transition's role, its condition, self
	approval) and the app's three (module docstring). None while the site has no active
	Workflow.
	"""
	workflow = _workflow()
	if not workflow:
		return []
	doc = frappe._dict(row.as_dict() if hasattr(row, "as_dict") else row)
	user, roles = frappe.session.user, frappe.get_roles()
	requester = user in (doc.get("owner"), doc.get("requested_by"))
	about_them = bool(doc.get("user")) and doc.get("user") == user
	met: dict[str, bool] = {}  # a condition shared by several transitions is evaluated once
	out = []
	for t in workflow.transitions:
		if t.state != doc.get(STATE_FIELD) or t.allowed not in roles or t.action in out:
			continue
		if _is_decision(t):
			if requester or about_them or _decided_elsewhere(doc, user):
				continue
		elif t.action in REQUESTER_ACTIONS and (not requester or t.next_state in SYSTEM_STATES):
			continue  # the requester's own step, and never into a state of the system
		if not has_approval_access(user, doc, t):
			continue
		if t.condition:
			if t.condition not in met:
				met[t.condition] = bool(
					frappe.safe_eval(t.condition, get_workflow_safe_globals(), {"doc": doc})
				)
			if not met[t.condition]:
				continue
		out.append(t.action)
	return out


def _record(doc, step: dict) -> None:
	"""Add a step to the request. Written directly: the controller lets no save change
	``decisions``, so what is here is what the service recorded."""
	recorded = [*steps(doc), {**step, "at": str(now_datetime())}]
	doc.db_set("decisions", frappe.as_json({"steps": recorded}), update_modified=False)


def apply(doc, action: str, note: str | None = None):
	"""Take a Workflow step as the session user; returns the saved document.

	Frappe's ``apply_workflow`` checks the step again (role, condition, self approval), sets
	the state, saves and writes its history line. The flag tells the controller that this
	save is a step. The step is then recorded on the request with its note, which is what
	"one person, one level" and the history read.
	"""
	if not _workflow():
		frappe.throw(_("{0} has no active Workflow. Activate one in the Desk.").format(DOCTYPE))
	if action not in actions(doc):
		# the app's own rules (module docstring), which Frappe's check below does not know
		frappe.throw(
			_("{0} is not a step you can take on change request {1} now.").format(action, doc.name),
			frappe.PermissionError,
		)
	before = doc.get(STATE_FIELD)
	frappe.flags[states.STEP_FLAG] = doc.name
	try:
		doc = apply_workflow(doc.as_dict(), action)
	finally:
		frappe.flags[states.STEP_FLAG] = None
	_record(
		doc,
		{
			"from": before,
			"action": action,
			"to": doc.get(STATE_FIELD),
			"by": frappe.session.user,
			"note": note or None,
		},
	)
	if doc.get("step_note"):
		doc.db_set("step_note", None, update_modified=False)  # the note written in the Desk is used up
	if note:
		# Frappe's own line for the step (in the form's timeline) says the state; this one adds why
		doc.add_comment("Info", _("{0}: {1}").format(action, note))
	return doc


def record_system_state(doc, state: str) -> None:
	"""Keep a step the system took (the DA Registry's answer) with the request's steps, and
	in the timeline Frappe shows in the Desk, where the steps taken through a transition
	already have a line."""
	_record(doc, {"from": None, "action": None, "to": state, "by": None, "system": REGISTRY, "note": None})
	doc.add_comment("Workflow", state, comment_by=REGISTRY)


# -- who sees a request -------------------------------------------------------------------


def _permitted(user: str, doctype: str) -> set[str]:
	"""The values of ``doctype`` this user holds an explicit User Permission on."""
	return {row.get("doc") for row in frappe.permissions.get_user_permissions(user).get(doctype, [])}


def roles_seen_by_frappe_alone() -> set[str]:
	"""The roles this module adds no rule for: those who look after what the registry did
	not take (retry, close), and the roles that read requests but do not send them."""
	readers = {p.role for p in frappe.get_meta(DOCTYPE).permissions if p.read and not p.create}
	return roles_for(states.REGISTRY_FAILED) | readers


def _sees_by_frappe_alone(user: str) -> bool:
	"""No rule of this module for this user: role and User Permissions decide, as Frappe applies them.

	True for those who look after what the registry did not take (they see every request
	their own permissions allow) and for a role that only reads (it keeps the geography
	rules it had). Giving an approval, even the last, does not make it true: an approver
	sees what the rule below gives them. While the site has no active Workflow nobody is
	known to look after failures, so everyone who sends requests sees their own and those
	of the people below them, no more.
	"""
	if user == "Administrator":
		return True
	if roles_for(states.REGISTRY_FAILED) & set(frappe.get_roles(user)):
		return True
	return not frappe.has_permission(DOCTYPE, "create", user=user)


def _decides(user: str) -> bool:
	return bool(deciding_roles() & set(frappe.get_roles(user)))


def query_conditions(user: str | None = None, doctype: str | None = None) -> str:
	"""``permission_query_conditions``: the requests a person who sends or decides them sees.

	Their own (about them, or sent by them), those about a User they hold a User Permission
	on (DA Hierarchy: everyone below them), and, for someone with a deciding role, those
	with no User in a Woreda they hold a User Permission on.
	"""
	user = user or frappe.session.user
	if _sees_by_frappe_alone(user):
		return ""
	table, me = f"`tab{DOCTYPE}`", frappe.db.escape(user)
	permitted = "select `for_value` from `tabUser Permission` where `user` = {me} and `allow` = {allow}"
	rules = [
		f"{table}.`owner` = {me}",
		f"{table}.`user` = {me}",
		f"{table}.`user` in ({permitted.format(me=me, allow=frappe.db.escape('User'))})",
	]
	if _decides(user):
		rules.append(
			f"(({table}.`user` is null or {table}.`user` = '') and {table}.`woreda` in "
			f"({permitted.format(me=me, allow=frappe.db.escape('DA Woreda'))}))"
		)
	return "(" + " or ".join(rules) + ")"


def has_permission(doc, ptype: str | None = None, user: str | None = None, **kwargs) -> bool:
	"""``has_permission``: the same rule as ``query_conditions`` for one request.

	A hook can only take away what role and User Permissions gave; it returns True to leave
	Frappe's answer as it is.
	"""
	user = user or frappe.session.user
	if ptype == "create" or doc.is_new() or _sees_by_frappe_alone(user):
		return True
	if user in (doc.owner, doc.get("user")):
		return True
	if doc.get("user"):
		return doc.user in _permitted(user, "User")
	return bool(doc.get("woreda")) and _decides(user) and doc.woreda in _permitted(user, "DA Woreda")
