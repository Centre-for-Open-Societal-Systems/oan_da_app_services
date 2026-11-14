# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from da_services.services.constants import NOT_ASSIGNED_KEBELE


class DAKebele(Document):
	def before_insert(self):
		# Runs before naming: the name is the code, so normalise it first.
		self.kebele_code = (self.kebele_code or "").strip()
		if self.kebele_code != NOT_ASSIGNED_KEBELE:  # the Not Assigned row keeps its spelling
			self.kebele_code = self.kebele_code.upper()

	def validate(self):
		self.kebele_name = (self.kebele_name or "").strip()
