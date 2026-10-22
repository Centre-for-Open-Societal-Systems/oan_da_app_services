# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""DA Leave Request: a DA's leave awaiting Woreda Supervisor approval (lifecycle, slice 1).

States: Pending → Approved → Active (leave started; DA is On-leave in the registry)
→ Completed (leave ended; DA reactivated), or Rejected / Cancelled (by the DA while
Pending or Approved-not-yet-started). Writes go through services/leave.py, which
checks scope and talks to the DA Registry; rows are history and are never deleted.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, getdate

LEAVE_TYPES = ("Annual", "Sick", "Maternity", "Study", "Other")
OPEN_STATES = ("Pending", "Approved", "Active")
TRANSITIONS = {
	"Pending": {"Approved", "Rejected", "Cancelled"},
	"Approved": {"Active", "Cancelled"},
	"Active": {"Completed"},
	"Completed": set(),
	"Rejected": set(),
	"Cancelled": set(),
}


class DALeaveRequest(Document):
	def validate(self):
		self.client_op_id = (self.client_op_id or "").strip() or None  # NULL for the unique index
		if self.leave_type not in LEAVE_TYPES:
			frappe.throw(_("Leave type must be one of {0}.").format(", ".join(LEAVE_TYPES)))
		if not self.from_date or not self.to_date:
			frappe.throw(_("From and To dates are required."))
		if getdate(self.to_date) < getdate(self.from_date):
			frappe.throw(_("To date cannot be before From date."))
		self.days = date_diff(self.to_date, self.from_date) + 1

		if self.is_new():
			if self.status != "Pending":
				frappe.throw(_("A new leave request must start as Pending."))
			return
		before = self.get_doc_before_save()
		if before and before.status != self.status and self.status not in TRANSITIONS[before.status]:
			frappe.throw(
				_("Leave request {0} cannot move from {1} to {2}.").format(
					self.name, before.status, self.status
				)
			)

	def on_trash(self):
		frappe.throw(_("Leave requests are history; cancel or reject them instead of deleting."))
