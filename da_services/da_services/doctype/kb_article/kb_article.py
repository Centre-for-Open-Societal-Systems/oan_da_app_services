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
	"""Shared by KB Article and KB Video: audience scope rows must be consistent.

	The fields link to DA Region / DA Zone / DA Woreda, so the level is guaranteed; a
	Zone needs its Region and must sit in it, and a Woreda needs its Zone and must sit in it.
	"""
	for row in doc.audience_scopes or []:
		if not (row.region or row.zone or row.woreda):
			frappe.throw(_("Audience scope row {0} is empty.").format(row.idx))
		if row.zone and not row.region:
			frappe.throw(_("Row {0}: a Zone needs its Region.").format(row.idx))
		if row.woreda and not row.zone:
			frappe.throw(_("Row {0}: a Woreda needs its Zone.").format(row.idx))
		if row.zone and frappe.db.get_value("DA Zone", row.zone, "region") != row.region:
			frappe.throw(_("Row {0}: Zone {1} is not in Region {2}.").format(row.idx, row.zone, row.region))
		if row.woreda and frappe.db.get_value("DA Woreda", row.woreda, "zone") != row.zone:
			frappe.throw(_("Row {0}: Woreda {1} is not in Zone {2}.").format(row.idx, row.woreda, row.zone))
