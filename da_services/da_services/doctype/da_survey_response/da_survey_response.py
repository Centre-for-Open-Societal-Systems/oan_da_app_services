# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""One farmer response to a published survey. Writes go through services/surveys.py."""

import frappe
from frappe import _
from frappe.model.document import Document

SYNC_STATES = ("Draft", "Queued", "Synced", "Rejected", "Conflict", "Declined")
CONSENTS = ("granted", "declined")


class DASurveyResponse(Document):
	def validate(self):
		self.client_op_id = (self.client_op_id or "").strip() or None
		self.duplicate_guard_key = (self.duplicate_guard_key or "").strip() or None
		if self.sync_state not in SYNC_STATES:
			frappe.throw(_("sync_state must be one of {0}.").format(", ".join(SYNC_STATES)))
		if self.consent and self.consent not in CONSENTS:
			frappe.throw(_("consent must be granted or declined."))
		if not self.survey or not self.farmer_id or not self.da_id:
			frappe.throw(_("survey, farmer_id and collector DA-ID are required."))

	def on_trash(self):
		frappe.throw(_("Survey responses are history and cannot be deleted."))
