# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""An MoA subject-matter expert in the advisor directory (Add Advisor form).

A reference list DAs read for specialist advisory support; the Administrator maintains it.
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import validate_email_address

EXPERTISE_AREAS = (
	"Crop protection",
	"Soil fertility",
	"Livestock health",
	"Irrigation",
	"Horticulture",
	"Agronomy",
	"Natural resources",
	"Agri-finance",
)
STATUSES = ("Active", "On leave", "Inactive")
# Ethiopian mobile number as the form asks for it, e.g. +251 91 234 5678.
MOBILE_PATTERN = re.compile(r"^\+?251\s?9\d(\s?\d){7}$")


class DAAdvisor(Document):
	def validate(self):
		for field in ("full_name", "moa_unit", "region", "woreda", "mobile_number", "email", "availability"):
			self.set(field, (self.get(field) or "").strip() or None)
		if len(self.full_name or "") < 3:
			frappe.throw(_("Enter the advisor's full name (at least 3 characters)."))
		if self.expertise_area not in EXPERTISE_AREAS:
			frappe.throw(_("Expertise area must be one of {0}.").format(", ".join(EXPERTISE_AREAS)))
		if self.status not in STATUSES:
			frappe.throw(_("Status must be one of {0}.").format(", ".join(STATUSES)))
		if not MOBILE_PATTERN.match(self.mobile_number or ""):
			frappe.throw(_("Use an Ethiopian mobile number, e.g. +251 91 234 5678."))
		if not validate_email_address(self.email or ""):
			frappe.throw(_("Enter a valid email address."))
