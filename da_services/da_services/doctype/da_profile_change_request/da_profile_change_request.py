# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""DA Profile Change Request: a change to a DA's profile, approved at two levels.

The states and who may move a request between them are the Frappe Workflow of this DocType
(see ``da_services/workflows/profile_change.py`` for the first version and the diagram);
``status`` is its state field. Applied and Registry failed have no transition: the system
writes them after the DA Registry answers. A step is taken through ``services/profile.py``
(the portal's API), never by a plain save: see ``_refuse_changes_outside_a_step``. Who sees
a request is role and User Permissions. Rows are history and are never deleted.
"""

import frappe
from frappe import _
from frappe.model import no_value_fields
from frappe.model.document import Document
from frappe.model.workflow import get_workflow_name

PENDING_SUPERVISOR = "Pending Supervisor"
PENDING_ADMINISTRATOR = "Pending Administrator"
SENT_TO_REGISTRY = "Sent to registry"
APPLIED = "Applied"
REGISTRY_FAILED = "Registry failed"
REJECTED = "Rejected"
CHANGES_REQUESTED = "Changes requested"
CANCELLED = "Cancelled"
CLOSED = "Closed"

# The states of the Workflow this app creates. A site's Workflow may hold more (an approval
# level added in the Desk): which states wait for a decision and which are still open is
# asked of the Workflow (``workflows/profile_change.py``); these two are what holds on a
# site with no active Workflow.
PENDING_STATES = (PENDING_SUPERVISOR, PENDING_ADMINISTRATOR)
OPEN_STATES = (PENDING_SUPERVISOR, PENDING_ADMINISTRATOR, SENT_TO_REGISTRY, REGISTRY_FAILED)
# Set (to the request's name) by ``workflows.profile_change.apply`` while it takes a step.
STEP_FLAG = "da_profile_change_step"
# Set by ``workflows.profile_change.sending`` while the service inserts a new request.
SEND_FLAG = "da_profile_change_send"


class DAProfileChangeRequest(Document):
	def validate(self):
		self.client_op_id = (self.client_op_id or "").strip() or None  # NULL for the unique index
		if self.is_new():
			if not frappe.flags.get(SEND_FLAG) and frappe.session.user != "Administrator":
				# Whom a request is about, who sent it and its steps are what the service found
				# and checked, never what a form or an API call carried.
				frappe.throw(
					_("A change request is sent from the DA portal, not created here."),
					frappe.PermissionError,
				)
			start = self._first_state()
			if self.status != start:
				frappe.throw(_("A new change request starts at {0}.").format(start))
			return
		self._refuse_changes_outside_a_step()

	def _first_state(self) -> str:
		"""Where the site's Workflow starts a request (Frappe checks this too while a Workflow
		is active; this also holds on a site whose Workflow was switched off)."""
		name = get_workflow_name(self.doctype)
		states = frappe.get_cached_doc("Workflow", name).states if name else []
		return states[0].state if states else PENDING_SUPERVISOR

	def _refuse_changes_outside_a_step(self):
		"""A sent request changes through its steps only.

		A step is more than a new state: it records who decided and, at the last approval,
		sends the change to the DA Registry with the approver's own sign-in. So a step is
		taken by the service (``STEP_FLAG``) and its save changes the state only; no other
		save changes a request. Everything else on it the service writes directly.
		"""
		if not self.get_doc_before_save():
			return
		changed = [
			df.fieldname
			for df in self.meta.fields
			if df.fieldtype not in no_value_fields and self.has_value_changed(df.fieldname)
		]
		if self.has_value_changed("owner"):
			changed.append("owner")  # who sent the request: the Workflow reads it
		taking_a_step = frappe.flags.get(STEP_FLAG) == self.name
		beyond = [f for f in changed if not (taking_a_step and f == "status")]
		if not beyond:
			return
		labels = ", ".join(self.meta.get_label(f) or f for f in beyond)
		if taking_a_step:
			frappe.throw(_("A step cannot change {0} of change request {1}.").format(labels, self.name))
		frappe.throw(
			_(
				"Change request {0} changes through its steps only (Approve, Reject, Cancel ...), "
				"taken in the DA portal. Not saved: {1}."
			).format(self.name, labels)
		)

	def on_trash(self):
		frappe.throw(_("Change requests are history; cancel or reject them instead of deleting."))
