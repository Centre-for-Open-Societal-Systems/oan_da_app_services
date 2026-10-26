# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from da_services.integrations.da_registry.client import DARegistryError, get_client

OPEN = "Open"
DONE = "Done"
CANCELLED = "Cancelled"
STATUSES = (OPEN, DONE, CANCELLED)


class DATask(Document):
	"""One unit of work for a DA (data-model 5.5).

	Scope codes (`region`, `woreda`) are copied from the DA's registry posting when the
	row is created so that Supervisor / Executive list filters work without a registry
	call per row (see permissions.da_task_query_conditions). The DA ↔ task relation is
	`da_id`; `client_op_id` makes device replays idempotent.
	"""

	def before_insert(self):
		self.created_by_user = self.created_by_user or frappe.session.user
		self._fill_scope_from_registry()

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

	def _fill_scope_from_registry(self):
		if self.region and self.zone and self.woreda:
			return
		try:
			ref = get_client().get_da(self.da_id)
		except DARegistryError as exc:
			# Unknown DA or registry down: leave the codes empty. Officers will not see the
			# row until a sync fills them; the DA still sees their own rows by da_id. Logged
			# (not raised) so an outage is visible without blocking the DA's work.
			frappe.logger("da_services").warning(
				"DA Task %s: scope codes not filled, registry lookup of %s failed: %s",
				self.name or "(new)",
				self.da_id,
				exc,
			)
			return
		self.region = self.region or ref.region
		self.zone = self.zone or ref.zone
		self.woreda = self.woreda or ref.woreda

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
