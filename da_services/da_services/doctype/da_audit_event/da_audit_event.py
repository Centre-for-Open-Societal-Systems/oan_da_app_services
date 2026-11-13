# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class DAAuditEvent(Document):
	"""Append-only. Rows are written by utils.audit, never by a user.

	FSD 7 (Security) and Appendix D: the audit log is immutable and even an
	administrator cannot delete an entry. Both edits and deletes are refused here so
	the rule holds for Desk, the REST resource API and code alike.
	"""

	def validate(self):
		if not self.is_new():
			frappe.throw(_("Audit events are immutable."), frappe.PermissionError)

	def on_trash(self):
		frappe.throw(_("Audit events cannot be deleted."), frappe.PermissionError)
