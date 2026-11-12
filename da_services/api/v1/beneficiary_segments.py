"""v1: Beneficiary Segment API (DA-XX).

Admin-only endpoints for creating, listing, retrieving, and updating
beneficiary segments. Segments group farmers for subsidy, credit, or
response programs.

Examples:
- Input subsidy – teff & wheat
- Credit-eligible smallholders
- Drought-response kebeles
- Women-headed households
"""

from __future__ import annotations

import frappe
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.services import constants as C
from da_services.services.beneficiary_segments import (
	create_segment,
	list_segments,
	get_segment,
	update_segment,
	set_segment_status,
)


@route("/beneficiary-segments", methods=("POST",), status=201, summary="Create beneficiary segment (Admin only)")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def create_beneficiary_segment(**kwargs):
 """Create a new beneficiary segment.

  Only OAN Administrator can create segments.

  Request body (JSON):
   {
    "segment_name": "Input subsidy – teff & wheat",
    "description": "Input subsidy for teff and wheat farmers",
    "client_op_id": "unique-operation-id"  // optional
   }

  Response (201):
   {
    "success": true,
    "data": {
     "name": "DA-BS-2026-00001",
     "segment_name": "Input subsidy – teff & wheat",
     "description": "Input subsidy for teff and wheat farmers",
     "is_active": true,
     "assigned_by": "admin@test.com",
     "assigned_at": "2026-11-12T10:00:00"
    },
    "message": "Segment created successfully"
   }
  """
 ctx = require_roles(C.ROLE_ADMIN)

 segment_name = kwargs.get("segment_name") or kwargs.get("segment_name") or ""
 if not segment_name and frappe.form_dict:
  segment_name = frappe.form_dict.get("segment_name") or ""

 description = kwargs.get("description") or ""

 client_op_id = kwargs.get("client_op_id") or None

 result = create_segment(
  segment_name=segment_name,
  description=description,
  client_op_id=client_op_id,
 )

 return success_response(
  message=_("Segment created successfully"),
  data={
   "name": result["name"],
   "segment_name": result["segment_name"],
   "description": result.get("description", ""),
   "is_active": result["is_active"],
   "assigned_by": result["assigned_by"],
   "assigned_at": result["assigned_at"],
  },
 )


@route("/beneficiary-segments", methods=("GET",), summary="List beneficiary segments")
@frappe.whitelist()
@handle_api_errors
def list_beneficiary_segments(**kwargs):
 """List beneficiary segments.

  Query parameters:
   - active_only: bool (default true) – return only active segments
   - limit: page size (default 50)
   - start: offset (default 0)

  Response:
   {
    "success": true,
    "data": {
     "segments": [...],
     "total": 4,
     "limit": 50,
     "start": 0,
     "page": 1,
     "number_of_pages": 1
    }
   }
  """
 active_only = kwargs.get("active_only") in ("1", "true", True, 1) if kwargs.get("active_only") else True
 limit = int(kwargs.get("limit") or 50)
 start = int(kwargs.get("start") or 0)

 result = list_segments(
  active_only=active_only,
  limit=limit,
  start=start,
 )

 return success_response(
  data={
   "segments": result["segments"],
   "total": result["total"],
   "limit": result["limit"],
   "start": result["start"],
   "page": result["page"],
   "number_of_pages": result["number_of_pages"],
  },
 )


@route("/beneficiary-segments/<segment_name>", methods=("GET",), summary="Retrieve beneficiary segment")
@frappe.whitelist()
@handle_api_errors
def get_beneficiary_segment(segment_name: str):
 """Retrieve a single beneficiary segment.

  Args:
   segment_name: The segment document name (e.g., "DA-BS-2026-00001")
     or the segment_name field value.

  Response:
   {
    "success": true,
    "data": {
     "name": "DA-BS-2026-00001",
     "segment_name": "Input subsidy – teff & wheat",
     "description": "Input subsidy for teff and wheat farmers",
     "is_active": true,
     "assigned_by": "admin@test.com",
     "assigned_at": "2026-11-12T10:00:00"
    }
   }
  """
 result = get_segment(segment_name=segment_name)

 return success_response(
  data={
   "name": result["name"],
   "segment_name": result["segment_name"],
   "description": result["description"],
   "is_active": result["is_active"],
   "assigned_by": result["assigned_by"],
   "assigned_at": result["assigned_at"],
  },
 )


@route("/beneficiary-segments/<segment_name>", methods=("PATCH",), summary="Update beneficiary segment (Admin only)")
@frappe.whitelist(methods=["PATCH"])
@handle_api_errors
def update_beneficiary_segment(segment_name: str, **kwargs):
 """Update a beneficiary segment.

  Only OAN Administrator can update segments.

  Request body (JSON, optional fields):
   {
    "segment_name": "Input subsidy – teff & wheat",
    "description": "Updated description"
   }

  Response:
   {
    "success": true,
    "data": {
     "name": "DA-BS-2026-00001",
     "segment_name": "Input subsidy – teff & wheat",
     "description": "Updated description",
     "is_active": true
    }
   }
  """
 ctx = require_roles(C.ROLE_ADMIN)

 new_segment_name = kwargs.get("segment_name") or None
 new_description = kwargs.get("description") or None

 result = update_segment(
  segment_name=segment_name,
  new_segment_name=new_segment_name,
  new_description=new_description,
 )

 return success_response(
  data={
   "name": result["name"],
   "segment_name": result["segment_name"],
   "description": result["description"],
   "is_active": result["is_active"],
  },
 )


@route("/beneficiary-segments/<segment_name>/deactivate", methods=("POST",), summary="Deactivate beneficiary segment (Admin only)")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def deactivate_beneficiary_segment(segment_name: str):
 """Deactivate a beneficiary segment.

  Admins can deactivate a segment to hide it from selection without
  deleting it. The segment and its assignment history are preserved.

  Response:
   {
    "success": true,
    "data": {
     "name": "DA-BS-2026-00001",
     "segment_name": "Input subsidy – teff & wheat",
     "is_active": false
    }
   }
  """
 ctx = require_roles(C.ROLE_ADMIN)

 result = set_segment_status(
  segment_name=segment_name,
  is_active=False,
 )

 return success_response(
  data={
   "name": result["name"],
   "segment_name": result["segment_name"],
   "is_active": result["is_active"],
  }
 )


@route("/beneficiary-segments/<segment_name>/activate", methods=("POST",), summary="Activate beneficiary segment (Admin only)")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def activate_beneficiary_segment(segment_name: str):
 """Activate a deactivated beneficiary segment.

  Admins can reactivate a deactivated segment to make it available
  for farmer assignment again.

  Response:
   {
    "success": true,
    "data": {
     "name": "DA-BS-2026-00001",
     "segment_name": "Input subsidy – teff & wheat",
     "is_active": true
    }
   }
  """
 ctx = require_roles(C.ROLE_ADMIN)

 result = set_segment_status(
  segment_name=segment_name,
  is_active=True,
 )

 return success_response(
  data={
   "name": result["name"],
   "segment_name": result["segment_name"],
   "is_active": result["is_active"],
  }
 )