# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""DA Kebele Assignment: the FR-05 decision record (data-model 5.13).

States follow the portal: Proposed (drafted) → Validated (geofence in-boundary) →
Effective (applied), or Flagged (geofence out-of-bounds, needs an override), and
Superseded when a newer decision for the same DA replaces it. Only an Effective row is
a posting in force; a Flagged row is a pending decision that leaves the posting in force
untouched until a Supervisor overrides it. The DA Registry keeps the posting of record;
`registry_sync_state` says whether it has accepted this decision.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from da_services.services.kebele_user_permissions import sync_user_permissions

LIVE_STATES = ("Proposed", "Validated", "Flagged", "Effective")
PENDING_STATES = ("Proposed", "Validated", "Flagged")


class DAKebeleAssignment(Document):
	def validate(self):
		kebele = frappe.db.get_value("DA Kebele", self.kebele, ["woreda", "is_active"], as_dict=True)
		if not kebele:
			frappe.throw(_("Kebele {0} is not in the DA Kebele master.").format(self.kebele))
		if not kebele.is_active and self.is_new():
			frappe.throw(_("Kebele {0} is inactive.").format(self.kebele))
		# Scope from the geo masters: Kebele -> Woreda -> Zone -> Region.
		self.woreda = kebele.woreda
		self.zone = frappe.db.get_value("DA Woreda", self.woreda, "zone") if self.woreda else None
		self.region = frappe.db.get_value("DA Zone", self.zone, "region") if self.zone else None
		self.client_op_id = (self.client_op_id or "").strip() or None  # NULL, so the unique index ignores it
		if self.is_new() and self.client_op_id:
			# Fast path with a clear message; the unique index on client_op_id is the guarantee.
			dup = frappe.db.get_value("DA Kebele Assignment", {"client_op_id": self.client_op_id}, "name")
			if dup:
				frappe.throw(
					_("Assignment {0} already exists for client_op_id {1}.").format(dup, self.client_op_id),
					frappe.DuplicateEntryError,
				)

	def after_insert(self):
		"""Supersede what this decision replaces.

		An Effective decision replaces every live row of the DA. A pending one (Flagged /
		Proposed / Validated) replaces only earlier pending rows, so the posting in force
		stays Effective until a Supervisor overrides the flag. The rows being replaced are
		locked (SELECT ... FOR UPDATE) for the rest of the transaction, so two decisions
		for the same DA at the same moment are applied one after the other.
		"""
		prior = self._supersede_prior()
		# User Permission on the Kebele; the superseded rows fire no hooks, so their users too.
		users = {self.user}
		if prior:
			users |= set(
				frappe.get_all("DA Kebele Assignment", filters={"name": ("in", prior)}, pluck="user")
			)
		for user in users:
			sync_user_permissions(user)

	def on_update(self):
		if self.flags.in_insert:
			return  # after_insert has synced, after superseding
		sync_user_permissions(self.user)
		before = self.get_doc_before_save()
		if before and before.user and before.user != self.user:
			sync_user_permissions(before.user)

	def _supersede_prior(self) -> list[str]:
		"""Mark the rows this decision replaces Superseded; returns their names."""
		if self.state not in LIVE_STATES:
			return []
		replace = LIVE_STATES if self.state == "Effective" else PENDING_STATES
		prior = frappe.db.sql(
			"""select name from `tabDA Kebele Assignment`
			where da_id = %s and state in %s and name != %s
			order by creation for update""",
			(self.da_id, tuple(replace), self.name),
			pluck=True,
		)
		for name in prior:
			frappe.db.set_value(
				"DA Kebele Assignment",
				name,
				{"state": "Superseded", "superseded_by": self.name},
				update_modified=True,
			)
		if prior:
			self.db_set("supersedes", prior[-1], update_modified=False)
		return prior

	def on_trash(self):
		frappe.throw(_("Assignments are history; supersede them instead of deleting."))
