# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class KBSendLog(Document):
	def before_validate(self):
		self.sent_by = self.sent_by or frappe.session.user
		self.sent_on = self.sent_on or now_datetime()

	def after_insert(self):
		refresh_send_count(self.article)

	def on_trash(self):
		frappe.throw(_("Send log rows are history; they cannot be deleted."))


def refresh_send_count(article: str) -> None:
	"""Recompute KB Article.send_count from the log. Never set the counter by hand."""
	count = frappe.db.count("KB Send Log", {"article": article})
	frappe.db.set_value("KB Article", article, "send_count", count, update_modified=False)
