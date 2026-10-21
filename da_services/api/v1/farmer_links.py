"""v1 farmer links: manual farmer-to-DA assignment (DA-211 Phase 1).

POST /api/v1/da-services/farmer-links    Create manual farmer link
GET  /api/v1/da-services/farmer-links    List farmer links

Manual farmer assignment is used when DA Kebele Assignment mode="Manual".
Supervisor explicitly assigns individual farmers to the DA.
"""

from __future__ import annotations

import frappe
from frappe import _
from oan_auth_service.api.utils import error_response, handle_api_errors, success_response

from da_services.api.router import route
from da_services.api.v1.common import request_fields
from da_services.services import constants as C
from da_services.services import farmer_links as svc
from da_services.utils.permissions import require_roles


@route("/farmer-links", methods=("POST",), status=201, summary="Create manual farmer-to-DA link")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def create_farmer_link(**kwargs):
	"""Create a manual farmer-to-DA link (Supervisor only).

	Request body:
		{
			"da_id": "DA-001",
			"farmer_id": "FARMER-001",
			"client_op_id": "unique-operation-id"  // optional
		}

	Response (201):
		{
			"message": "Farmer linked successfully",
			"data": {
				"link_id": "DA-FL-2026-0000001",
				"da_id": "DA-001",
				"farmer_id": "FARMER-001",
				"link_type": "Manual",
				"linked_at": "2026-10-19T14:30:00",
				"kebele": "ET04-W01-K03"
			}
		}

	Errors:
		400: Invalid input, DA has no assignment, mode is Automatic, duplicate link
		403: Supervisor scope violation
	"""
	ctx = require_roles(C.ROLE_SUPERVISOR, C.ROLE_ADMIN)

	body = request_fields(kwargs, "da_id", "farmer_id", "client_op_id")

	result = svc.create_farmer_link(
		da_id=(body.get("da_id") or "").strip(),
		farmer_id=(body.get("farmer_id") or "").strip(),
		client_op_id=body.get("client_op_id"),
		ctx=ctx,
	)

	return success_response(data=result, message="Farmer linked successfully")


@route("/farmer-links", methods=("GET",), summary="List farmer links")
@frappe.whitelist()
@handle_api_errors
def list_farmer_links(**kwargs):
	"""List farmer links with filters.

	Query params:
		- da_id: Filter by DA
		- farmer_id: Filter by farmer
		- link_type: Filter by Automatic/Manual
		- is_active: Filter by active status (default true)

	Response:
		{
			"data": [
				{
					"link_id": "DA-FL-2026-0000001",
					"da_id": "DA-001",
					"farmer_id": "FARMER-001",
					"link_type": "Manual",
					"linked_at": "2026-10-19T14:30:00",
					"kebele": "ET04-W01-K03",
					"is_active": 1
				}
			]
		}
	"""
	ctx = require_roles(C.ROLE_SUPERVISOR, C.ROLE_ADMIN, C.ROLE_DA)

	query = request_fields(kwargs, "da_id", "farmer_id", "link_type", "is_active")

	is_active = None
	if "is_active" in query:
		is_active = query["is_active"] in ("1", "true", True, 1)

	links = svc.list_farmer_links(
		da_id=query.get("da_id"),
		farmer_id=query.get("farmer_id"),
		link_type=query.get("link_type"),
		is_active=is_active,
		ctx=ctx,
	)

	return success_response(data=links)
