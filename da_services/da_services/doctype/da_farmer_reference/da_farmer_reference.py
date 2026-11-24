# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class DAFarmerReference(Document):
	"""Read-only cache of Farmer Registry data.

	Written by the farmer sync service, never by users.
	Enables fast profile screens without calling the registry per row.
	"""

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		# The place is the registry's. A level the geo masters do not have stays empty: it
		# is never filled with the saving user's own Region / Zone / Woreda / Kebele (the
		# default Frappe takes from a single User Permission).
		self.dont_update_if_missing = ["region", "zone", "woreda", "kebele"]
