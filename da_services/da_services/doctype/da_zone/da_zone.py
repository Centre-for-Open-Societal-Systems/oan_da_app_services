# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class DAZone(Document):
	def before_insert(self):
		# Runs before naming: the name is the code, so normalise it first.
		self.zone_code = (self.zone_code or "").strip().upper()

	def validate(self):
		self.zone_name = (self.zone_name or "").strip()
