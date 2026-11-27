"""The Frappe Workflow of DA Profile Change Request.

    Pending Supervisor ──Approve──▶ Pending Administrator ──Approve──▶ Sent to registry ─▶ Applied
            │                          ▲         │                            │
            │      Submit for DA ──────┘         ├─Reject─▶ Rejected          └─▶ Registry failed
            ├─Reject─▶ Rejected                  └─Cancel─▶ Cancelled               ├─Retry─▶ Sent to registry
            ├─Request Changes─▶ Changes requested                                   └─Close─▶ Closed
            └─Cancel─▶ Cancelled

That is the Workflow this app creates on a site that has none; after that the document is
the site's, to change in the Desk. Who may take a step is Frappe's: the transition's role,
its condition and its "Allow Self Approval" tick. Who sees a request is Frappe's too: role
and User Permissions (DA Hierarchy gives each person one on themselves and on everyone
below them, and the request names the DA's User).

What Frappe has no setting for is written as a condition on the transitions, where an
administrator can read and change it: a Supervisor decides for the people below them
(``ABOVE_THE_DA``), and whoever gave level 1 does not give level 2 (``NOT_LEVEL_1``).

Applied and Registry failed have no transition: the app writes them after the DA Registry
answers. A step is taken with ``apply``, which the service calls; the controller refuses
every other change of a sent request.
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
from frappe.utils import now_datetime

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
# Frappe's role of every signed-in user: for a step whose rule is its condition, not a role.
EVERYONE = "All"
# The name the DA Registry's own steps are recorded under.
REGISTRY = "DA Registry"

# The caller is above the DA in DA Hierarchy: they hold a User Permission on the DA's User,
# which is not their own.
ABOVE_THE_DA = (
	'doc.user != frappe.session.user and frappe.db.get_value("User Permission", '
	'{"user": frappe.session.user, "allow": "User", "for_value": doc.user}, "name")'
)
# Level 2 is not given by whoever gave level 1, nor by the person the request is about.
NOT_LEVEL_1 = "doc.l1_by != frappe.session.user and doc.user != frappe.session.user"
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
	(states.PENDING_ADMINISTRATOR, APPROVE, states.SENT_TO_REGISTRY, C.ROLE_ADMIN, 0, NOT_LEVEL_1),
	(states.PENDING_ADMINISTRATOR, REJECT, states.REJECTED, C.ROLE_ADMIN, 0, NOT_LEVEL_1),
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


def roles_for(state: str, action: str | None = None) -> set[str]:
	"""The roles the Workflow lets act on a request in ``state`` (with ``action``, or any)."""
	return {t.allowed for t in _transitions() if t.state == state and action in (None, t.action)}


def leads_to(state: str, action: str) -> set[str]:
	"""The states ``action`` moves a request to from ``state``."""
	return {t.next_state for t in _transitions() if t.state == state and t.action == action}


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
	# the service writes (no save can).
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


def actions(row) -> list[str]:
	"""The Workflow actions the session user may take on a request now, in the Workflow's order.

	``row`` is the document or a row with ``status``, ``owner`` and the fields the conditions
	read (``user``, ``l1_by``, ``initiated_by_role``). The rules are Frappe's: the transition's
	role, its "Allow Self Approval" tick and its condition. None while the site has no
	active Workflow.
	"""
	workflow = _workflow()
	if not workflow:
		return []
	doc = frappe._dict(row.as_dict() if hasattr(row, "as_dict") else row)
	user, roles = frappe.session.user, frappe.get_roles()
	met: dict[str, bool] = {}  # a condition shared by several transitions is evaluated once
	out = []
	for t in workflow.transitions:
		if t.state != doc.get(STATE_FIELD) or t.allowed not in roles or t.action in out:
			continue
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

	Frappe's ``apply_workflow`` checks the step (role, condition, self approval), sets the
	state, saves and writes its history line. The flag tells the controller that this save
	is a step. The step is then recorded on the request with its note, for the history.
	"""
	if not _workflow():
		frappe.throw(_("{0} has no active Workflow. Activate one in the Desk.").format(DOCTYPE))
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
	return doc


def record_system_state(doc, state: str) -> None:
	"""Keep a step the system took (the DA Registry's answer) with the request's steps, and
	in the timeline Frappe shows in the Desk, where the steps taken through a transition
	already have a line."""
	_record(doc, {"from": None, "action": None, "to": state, "by": None, "system": REGISTRY, "note": None})
	doc.add_comment("Workflow", state, comment_by=REGISTRY)
