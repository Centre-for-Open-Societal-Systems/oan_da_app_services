# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""DA Profile Change Request: a change to a DA's profile, approved at two levels.

States:

    Pending Supervisor ──approve──▶ Pending Administrator ──approve──▶ (sent to the registry)
            │                               │                              │
            ├─reject─▶ Rejected             ├─reject─▶ Rejected            ├─▶ Sent to registry ─▶ Applied
            ├─request changes─▶ Changes requested                          ├─▶ Applied
            └─cancel─▶ Cancelled            └─cancel─▶ Cancelled           └─▶ Registry failed
                                                                                  │  retry ─▶ Sent / Applied / failed
                                                                                  └─ close ─▶ Closed

A Supervisor's own request starts at Pending Administrator (the Supervisor is level 1).
"Sent to registry" waits for the registry's answer; it can still end in Registry failed
when the registry rejects the change later. Writes go through services/profile.py, which
checks scope; rows are history and are never deleted.
"""

import frappe
from frappe import _
from frappe.model.document import Document

PENDING_SUPERVISOR = "Pending Supervisor"
PENDING_ADMINISTRATOR = "Pending Administrator"
SENT_TO_REGISTRY = "Sent to registry"
APPLIED = "Applied"
REGISTRY_FAILED = "Registry failed"
REJECTED = "Rejected"
CHANGES_REQUESTED = "Changes requested"
CANCELLED = "Cancelled"
CLOSED = "Closed"

PENDING_STATES = (PENDING_SUPERVISOR, PENDING_ADMINISTRATOR)
# A DA has at most one of these per section: the registry also takes one pending change
# per DA and section, and a failed push must be retried or closed before the next one.
OPEN_STATES = (PENDING_SUPERVISOR, PENDING_ADMINISTRATOR, SENT_TO_REGISTRY, REGISTRY_FAILED)
_AFTER_PUSH = {SENT_TO_REGISTRY, APPLIED, REGISTRY_FAILED}
TRANSITIONS = {
	PENDING_SUPERVISOR: {PENDING_ADMINISTRATOR, REJECTED, CHANGES_REQUESTED, CANCELLED},
	PENDING_ADMINISTRATOR: {REJECTED, CANCELLED} | _AFTER_PUSH,
	SENT_TO_REGISTRY: {APPLIED, REGISTRY_FAILED},
	REGISTRY_FAILED: _AFTER_PUSH | {CLOSED},
	APPLIED: set(),
	REJECTED: set(),
	CHANGES_REQUESTED: set(),
	CANCELLED: set(),
	CLOSED: set(),
}


class DAProfileChangeRequest(Document):
	def validate(self):
		self.client_op_id = (self.client_op_id or "").strip() or None  # NULL for the unique index
		if self.is_new():
			if self.status not in PENDING_STATES:
				frappe.throw(_("A new change request must start waiting for a decision."))
			return
		before = self.get_doc_before_save()
		if before and before.status != self.status and self.status not in TRANSITIONS.get(before.status, ()):
			frappe.throw(
				_("Change request {0} cannot move from {1} to {2}.").format(
					self.name, before.status, self.status
				)
			)

	def on_trash(self):
		frappe.throw(_("Change requests are history; cancel or reject them instead of deleting."))
