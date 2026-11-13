# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class DAFarmerLink(Document):
	"""Links a farmer to a DA (Automatic or Manual).

	Used to:
	1. Determine which farmers a DA can edit (DA-41)
	2. Scope farmer lists to "My Farmers"
	3. Track manual links DAs create
	"""

	def before_insert(self):
		"""Set linked_at and linked_by on creation."""
		if not self.linked_at:
			self.linked_at = frappe.utils.now_datetime()
		if not self.linked_by:
			self.linked_by = frappe.session.user

	def validate(self):
		"""Fetch and cache farmer info from DA Farmer Reference."""
		if self.farmer_id and not self.farmer_name:
			ref = frappe.db.get_value(
				"DA Farmer Reference",
				self.farmer_id,
				["farmer_name", "region", "zone", "woreda", "kebele"],
				as_dict=True,
			)
			if ref:
				self.farmer_name = ref.farmer_name
				self.region = ref.region
				self.zone = ref.zone
				self.woreda = ref.woreda
				self.kebele = ref.kebele
