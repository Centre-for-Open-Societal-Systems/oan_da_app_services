# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""DA Profile Change Request: a DA's own profile edit awaiting Supervisor approval.

States: Pending → Approved → Applied (pushed to the DA Registry), or Rejected /
Changes requested (the DA submits a new request) / Cancelled (by the DA while Pending).
Writes go through services/profile.py, which checks scope; rows are history and are
never deleted.
"""

import frappe
from frappe import _
from frappe.model.document import Document

OPEN_STATES = ("Pending", "Approved")
TRANSITIONS = {
	"Pending": {"Approved", "Rejected", "Changes requested", "Cancelled"},
	"Approved": {"Applied"},
	"Rejected": set(),
	"Changes requested": set(),
	"Applied": set(),
	"Cancelled": set(),
}


class DAProfileChangeRequest(Document):
	def validate(self):
		self.client_op_id = (self.client_op_id or "").strip() or None  # NULL for the unique index
		if self.is_new():
			if self.status != "Pending":
				frappe.throw(_("A new change request must start as Pending."))
			return
		before = self.get_doc_before_save()
		if before and before.status != self.status and self.status not in TRANSITIONS[before.status]:
			frappe.throw(
				_("Change request {0} cannot move from {1} to {2}.").format(
					self.name, before.status, self.status
				)
			)

	def on_trash(self):
		frappe.throw(_("Change requests are history; cancel or reject them instead of deleting."))
