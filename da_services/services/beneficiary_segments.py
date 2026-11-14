"""Beneficiary Segment service layer (DA-XX).

Business logic for creating, listing, retrieving, updating, and deactivating
beneficiary segments. Segments are managed by OAN Administrator only.

Segments group farmers for subsidy, credit, or response programs:
- Input subsidy - teff & wheat
- Credit-eligible smallholders
- Drought-response kebeles
- Women-headed households
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.naming import make_autoname

from da_services.utils.permissions import ScopeContext

WRITE_ROLES = ("OAN Administrator",)


def create_segment(
	*,
	segment_name: str,
	description: str | None = None,
	client_op_id: str | None = None,
	ctx: ScopeContext | None = None,
) -> dict:
 """Create a new beneficiary segment.

  Only available to OAN Administrator.

  Args:
   segment_name: Name of the segment (mandatory, trimmed, unique).
   description: Optional description of the segment.
   client_op_id: Optional idempotency key.
   ctx: Scope context for role checking.

   Returns:
    Created segment details.

   Raises:
    ValidationError: If segment name is empty or duplicate.
    PermissionError: If user does not have Administrator role.
   """
 if not segment_name or not segment_name.strip():
  frappe.throw(_("Segment name is required."), frappe.ValidationError)

 segment_name = segment_name.strip()

 # Check for duplicate segment name (case-insensitive)
 existing = frappe.db.exists(
  "DA Beneficiary Segment",
  {"segment_name": ("=", segment_name)},
 )
 if existing:
  frappe.throw(
   _(
    "A segment with name '{0}' already exists. Segment names must be unique."
   ).format(segment_name),
   frappe.ValidationError,
 )

 # Role check: only OAN Administrator can create segments
 if ctx and not ctx.unrestricted and not ctx.has_any_role("OAN Administrator"):
  frappe.throw(
   _(
    "Only OAN Administrator can create beneficiary segments."
   ),
   frappe.PermissionError,
 )

 # Create the segment doc
 doc = frappe.get_doc(
  {
   "doctype": "DA Beneficiary Segment",
   "segment_name": segment_name,
   "description": description,
   "is_active": 1,
  }
 )
 if client_op_id:
  doc.client_op_id = client_op_id
 doc.insert(ignore_permissions=True)

 return {"name": doc.name, "segment_name": doc.segment_name, "description": doc.description, "is_active": doc.is_active}


def list_segments(
	*,
	active_only: bool = True,
	limit: int = 50,
	start: int = 0,
) -> dict:
 """List beneficiary segments.

  Args:
   active_only: If True, only return active segments (default: True).
   limit: Maximum number of rows to return.
   start: Offset for pagination.

  Returns:
   Dict with segments list, total count, and pagination info.
  """
 filters: dict = {}
 if active_only:
  filters["is_active"] = 1

 segments = frappe.get_all(
  "DA Beneficiary Segment",
  filters=filters,
  fields=["name", "segment_name", "description", "is_active", "assigned_by", "assigned_at"],
  order_by="segment_name asc",
  limit_page_length=limit,
  limit_start=start,
 )

 total = frappe.db.count("DA Beneficiary Segment", filters=filters)

 return {
  "segments": segments,
  "total": total,
  "limit": limit,
  "start": start,
  "page": start // limit + 1 if limit else 1,
  "number_of_pages": (total + limit - 1) // limit if limit else 1,
 }


def get_segment(segment_name: str, ctx: ScopeContext | None = None) -> dict:
 """Get a single beneficiary segment by name or doc name.

   Args:
    segment_name: The segment document name (e.g., "DA-BS-2026-00001")
      or the segment_name field value.

   Returns:
    Segment details dict.

   Raises:
    DoesNotExistError: If segment not found.
   """
 if not segment_name:
  frappe.throw(_("Segment name/ID is required."), frappe.ValidationError)

 # Try fetching by doc name first, then by segment_name field
 try:
  doc = frappe.get_doc("DA Beneficiary Segment", segment_name)
 except frappe.DoesNotExistError:
  # Fall back to searching by segment_name field
  doc = frappe.get_all(
   "DA Beneficiary Segment",
   filters={"segment_name": segment_name},
   fields=["name", "segment_name", "description", "is_active", "assigned_by", "assigned_at"],
  )
  if not doc:
   frappe.throw(_("Segment {0} not found.").format(segment_name), frappe.DoesNotExistError)
  doc = doc[0]

 return {
  "name": doc.name,
  "segment_name": doc.segment_name,
  "description": doc.description,
  "is_active": doc.is_active,
  "assigned_by": doc.assigned_by,
  "assigned_at": doc.assigned_at,
 }


def update_segment(
	*,
	segment_name: str,
	new_segment_name: str | None = None,
	new_description: str | None = None,
	ctx: ScopeContext | None = None,
) -> dict:
 """Update a beneficiary segment.

  Only OAN Administrator can update segments.

  Args:
   segment_name: The segment document name or segment_name field value.
   new_segment_name: New segment name (optional, must be unique).
   new_description: New description (optional).
   ctx: Scope context for role checking.

  Returns:
   Updated segment details.

  Raises:
   DoesNotExistError: If segment not found.
   ValidationError: If new segment name already exists.
   PermissionError: If user does not have Administrator role.
  """
 doc = frappe.get_doc("DA Beneficiary Segment", segment_name)

 # Role check: only OAN Administrator can update segments
 if ctx and not ctx.unrestricted and not ctx.has_any_role("OAN Administrator"):
  frappe.throw(
   _(
    "Only OAN Administrator can update beneficiary segments."
   ),
   frappe.PermissionError,
 )

 if new_segment_name is not None:
  new_segment_name = new_segment_name.strip()
  if not new_segment_name:
   frappe.throw(_("Segment name is required."), frappe.ValidationError)

  # Check uniqueness excluding current doc
  existing = frappe.db.exists(
   "DA Beneficiary Segment",
   {"segment_name": ("=", new_segment_name), "name": ("!=", doc.name)},
  )
  if existing:
   frappe.throw(
    _(
     "A segment with name '{0}' already exists. Segment names must be unique."
    ).format(new_segment_name),
    frappe.ValidationError,
  )

  # Use db_set to ensure the field is persisted
  doc.db_set("segment_name", new_segment_name)

 if new_description is not None:
  doc.db_set("description", new_description)

 doc.save()
 return {
  "name": doc.name,
  "segment_name": doc.segment_name,
  "description": doc.description,
  "is_active": doc.is_active,
 }


def set_segment_status(
	*,
	segment_name: str,
	is_active: bool,
) -> dict:
 """Set the active/inactive status of a segment.

  Admins can deactivate a segment to hide it from selection without
  deleting it. Reactivation is done by setting is_active=True.

  Args:
   segment_name: The segment document name or segment_name field value.
   is_active: True to activate, False to deactivate.

  Returns:
   Updated segment status.

  Raises:
   DoesNotExistError: If segment not found.
  """
 doc = frappe.get_doc("DA Beneficiary Segment", segment_name)
 doc.is_active = 1 if is_active else 0
 doc.save()
 return {
  "name": doc.name,
  "segment_name": doc.segment_name,
  "is_active": doc.is_active,
 }
