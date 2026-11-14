# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class DACountry(Document):
	def before_insert(self):
		# Runs before naming: the name is the code, so normalise it first.
		self.country_code = (self.country_code or "").strip().upper()

	def validate(self):
		self.country_name = (self.country_name or "").strip()
