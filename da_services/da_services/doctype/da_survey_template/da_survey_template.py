# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""Published survey template assigned to DAs in a Woreda (FR-09d).

Writes go through services/surveys.py. Rows are history; close them instead of deleting.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

SURVEY_TYPES = ("Satisfaction", "Service quality", "Needs assessment")
RESPONSE_TYPES = ("consent", "auto", "lookup", "rating", "single", "multi", "text")
CUSTOM_QUESTION_TYPES = ("rating", "single", "multi", "text")
STATUSES = ("Open", "Closed")


class DASurveyTemplate(Document):
	def validate(self):
		self.client_op_id = (self.client_op_id or "").strip() or None
		if self.survey_type not in SURVEY_TYPES:
			frappe.throw(_("Survey type must be one of {0}.").format(", ".join(SURVEY_TYPES)))
		if self.status not in STATUSES:
			frappe.throw(_("Status must be Open or Closed."))
		if not self.window_open or not self.window_close:
			frappe.throw(_("Collection window dates are required."))
		if getdate(self.window_close) < getdate(self.window_open):
			frappe.throw(_("Window must close on or after it opens."))
		if not self.parameters:
			frappe.throw(_("At least one parameter is required."))
		for row in self.parameters:
			if row.response_type not in RESPONSE_TYPES:
				frappe.throw(_("Unknown response type {0}.").format(row.response_type))

	def on_trash(self):
		frappe.throw(_("Survey templates are history; close them instead of deleting."))
