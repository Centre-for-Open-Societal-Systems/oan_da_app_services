# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class DAWoreda(Document):
	def before_insert(self):
		# Runs before naming: the name is the code, so normalise it first.
		self.woreda_code = (self.woreda_code or "").strip().upper()

	def validate(self):
		self.woreda_name = (self.woreda_name or "").strip()
