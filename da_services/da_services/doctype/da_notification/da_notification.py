# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

# The only fields a recipient may change on their own notification.
RECIPIENT_MUTABLE = {"read", "read_at"}
_IGNORED = {"modified", "modified_by", "idx", "docstatus"}


class DANotification(Document):
	"""One notification for one user (data-model 5.10).

	Content is written by the system when the row is created; afterwards the recipient
	may only mark it read. Delivery fields are updated by the SMS / Telegram jobs.
	"""

	def validate(self):
		if self.read and not self.read_at:
			self.read_at = now_datetime()
		if not self.read:
			self.read_at = None
		self._recipient_may_only_mark_read()

	def _recipient_may_only_mark_read(self):
		if self.is_new() or self.flags.ignore_permissions or frappe.session.user == "Administrator":
			return
		before = self.get_doc_before_save()
		if not before:
			return
		changed = {f for f in self.meta.get_valid_columns() if before.get(f) != self.get(f)} - _IGNORED
		if changed - RECIPIENT_MUTABLE:
			frappe.throw(_("A notification can only be marked read; its content cannot be edited."))

	def on_trash(self):
		frappe.throw(_("Notifications are not deleted; they are marked read."))
