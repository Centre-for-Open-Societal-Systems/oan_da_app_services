# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class DARegion(Document):
	def before_insert(self):
		# Runs before naming: the name is the code, so normalise it first.
		self.region_code = (self.region_code or "").strip().upper()

	def validate(self):
		self.region_name = (self.region_name or "").strip()
