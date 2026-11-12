# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class DABeneficiarySegment(Document):
 """Beneficiary Segment: a grouping of farmers for subsidy, credit, or response programs.

  Segments are managed by OAN Administrator and used for manual farmer assignment.
  Examples: "Input subsidy – teff & wheat", "Credit-eligible smallholders",
  "Drought-response kebeles", "Women-headed households".
 """

 def before_insert(self):
  """Set assigned_at and assigned_by on creation."""
  if not self.assigned_at:
   self.assigned_at = frappe.utils.now_datetime()
  if not self.assigned_by:
   self.assigned_by = frappe.session.user

 def validate(self):
  """Validate segment name and prevent duplicates."""

  self.segment_name = (self.segment_name or "").strip()

  if not self.segment_name:
   frappe.throw(_("Segment name is required."), frappe.ValidationError)

  # Check for duplicate segment name (case-insensitive), ignoring this doc if updating
  existing = frappe.db.exists(
   "DA Beneficiary Segment",
   {
    "segment_name": ("like", self.segment_name),
    "name": ("!=", self.name),
   },
  )
  if existing:
   frappe.throw(
    _(
     "A segment with name '{0}' already exists. Segment names must be unique."
    ).format(self.segment_name),
    frappe.ValidationError,
   )

  # Normalize client_op_id: NULL when absent, so the format unique index ignores it
  self.client_op_id = (self.client_op_id or "").strip() or None