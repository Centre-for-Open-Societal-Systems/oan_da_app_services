# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.caching import request_cache

LEVELS = ("Region", "Zone", "Woreda", "Kebele")


class DAAdministrativeArea(Document):
	def validate(self):
		self.code = (self.code or "").strip().upper()
		if self.level not in LEVELS:
			frappe.throw(_("Unknown level {0}.").format(self.level))
		if self.level == "Region":
			if self.parent_area:
				frappe.throw(_("A Region has no parent area."))
			return
		if not self.parent_area:
			frappe.throw(_("A {0} needs a parent area.").format(self.level))
		parent_level = frappe.db.get_value("DA Administrative Area", self.parent_area, "level")
		expected = LEVELS[LEVELS.index(self.level) - 1]
		if parent_level != expected:
			frappe.throw(
				_("Parent of a {0} must be a {1}, not a {2}.").format(self.level, expected, parent_level)
			)

	def ancestors(self) -> dict:
		"""{'region': code, 'zone': code, 'woreda': code, 'kebele': code} for this area."""
		return ancestors_of(self.code)


@request_cache
def ancestors_of(code: str) -> dict:
	"""Walk up the tree (at most four small lookups); memoised for the rest of the request."""
	out = {"region": None, "zone": None, "woreda": None, "kebele": None}
	current = code
	for _i in range(4):
		row = frappe.db.get_value("DA Administrative Area", current, ["level", "parent_area"], as_dict=True)
		if not row:
			break
		out[row.level.lower()] = current
		if not row.parent_area:
			break
		current = row.parent_area
	return out
