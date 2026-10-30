# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""DA Broadcast: the record of one broadcast to the DAs in a scope.

Written by services/notifications.broadcast() before the per-DA notification rows.
The unique `client_op_id` is the idempotency guard at the database, so two
concurrent replays of the same request cannot both pass a "does it exist yet"
check and notify everyone twice. Rows are history and are never deleted.
"""

import frappe
from frappe import _
from frappe.model.document import Document

SCOPES = ("all", "region", "zone", "woreda")


class DABroadcast(Document):
	def validate(self):
		self.client_op_id = (self.client_op_id or "").strip() or None  # NULL for the unique index
		if self.scope not in SCOPES:
			frappe.throw(_("Scope must be one of {0}.").format(", ".join(SCOPES)))
		if self.scope != "all" and not self.scope_value:
			frappe.throw(_("A {0} broadcast needs a scope value.").format(self.scope))
		if self.scope == "all":
			self.scope_value = None

	def on_trash(self):
		frappe.throw(_("Broadcasts are history; they cannot be deleted."))
