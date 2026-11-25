# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class DAFarmerReference(Document):
	"""Read-only cache of Farmer Registry data.

	Written by the farmer sync service, never by users.
	Enables fast profile screens without calling the registry per row.
	"""

	pass
