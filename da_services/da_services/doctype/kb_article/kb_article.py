# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today


class KBArticle(Document):
	def validate(self):
		if self.status == "Published" and not self.published_on:
			self.published_on = today()
		if not self.languages:
			frappe.throw(_("Add at least one language version (EN or አማ)."))
		seen = set()
		for row in self.languages:
			if row.language in seen:
				frappe.throw(_("Language {0} is listed twice.").format(row.language))
			seen.add(row.language)
		_check_audience(self)

	def on_trash(self):
		if frappe.db.exists("KB Send Log", {"article": self.name}):
			frappe.throw(_("This article has been sent to recipients; archive it instead of deleting."))


def _check_audience(doc):
	"""Shared by KB Article and KB Video: audience scope rows must be consistent."""
	for row in doc.audience_scopes or []:
		if not (row.region or row.zone or row.woreda):
			frappe.throw(_("Audience scope row {0} is empty.").format(row.idx))
		for field, level in (("region", "Region"), ("zone", "Zone"), ("woreda", "Woreda")):
			value = row.get(field)
			if value and frappe.db.get_value("DA Administrative Area", value, "level") != level:
				frappe.throw(_("Row {0}: {1} must be a {2}.").format(row.idx, value, level))
