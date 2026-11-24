# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

OPEN = "Open"
DONE = "Done"
CANCELLED = "Cancelled"
STATUSES = (OPEN, DONE, CANCELLED)


class DATask(Document):
	"""One unit of work for a DA (data-model 5.5).

	Row access is Role DocPerm + User Permissions on geo Links. ``user`` is the DA
	User Link; ``da_id`` is ``fetch_from: user.da_registry_id``. Geo stamps come from
	the client / masters / tests — not registry HTTP.
	"""

	def before_insert(self):
		self.created_by_user = self.created_by_user or frappe.session.user

	def validate(self):
		self._validate_status()
		self._validate_client_op_id()

	# -- rules -------------------------------------------------------------------

	def _validate_status(self):
		if self.status not in STATUSES:
			frappe.throw(_("Unknown task status {0}.").format(self.status))
		if self.status == DONE and not self.completed_at:
			self.completed_at = now_datetime()
		if self.status != DONE:
			self.completed_at = None
		before = self.get_doc_before_save() if not self.is_new() else None
		if before and before.status == CANCELLED and self.status != CANCELLED:
			frappe.throw(_("A cancelled task cannot be reopened; create a new one."))

	def _validate_client_op_id(self):
		if not self.client_op_id:
			return
		dup = frappe.db.exists(
			"DA Task", {"client_op_id": self.client_op_id, "name": ("!=", self.name or "")}
		)
		if dup:
			frappe.throw(
				_("A task with client_op_id {0} already exists ({1}).").format(self.client_op_id, dup),
				frappe.DuplicateEntryError,
			)

	# -- actions -----------------------------------------------------------------

	def mark_done(self):
		if self.status == CANCELLED:
			frappe.throw(_("A cancelled task cannot be marked done."))
		self.status = DONE
		self.completed_at = now_datetime()
		self.save()

	def cancel_task(self, reason: str | None = None):
		self.status = CANCELLED
		if reason:
			self.description = f"{self.description or ''}\nCancelled: {reason}".strip()
		self.save()
