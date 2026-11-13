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

from da_services.da_services.doctype.da_administrative_area.da_administrative_area import ancestors_of

LIVE_STATES = ("Proposed", "Validated", "Flagged", "Effective")
PENDING_STATES = ("Proposed", "Validated", "Flagged")


class DAKebeleAssignment(Document):
	def validate(self):
		area = frappe.db.get_value("DA Administrative Area", self.kebele, ["level", "active"], as_dict=True)
		if not area:
			frappe.throw(_("Kebele {0} is not in the administrative area master.").format(self.kebele))
		if area.level != "Kebele":
			frappe.throw(_("{0} is a {1}, not a Kebele.").format(self.kebele, area.level))
		if not area.active and self.is_new():
			frappe.throw(_("Kebele {0} is inactive.").format(self.kebele))
		tree = ancestors_of(self.kebele)
		self.region, self.zone, self.woreda = tree["region"], tree["zone"], tree["woreda"]
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
		if self.state not in LIVE_STATES:
			return
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

	def on_trash(self):
		frappe.throw(_("Assignments are history; supersede them instead of deleting."))
