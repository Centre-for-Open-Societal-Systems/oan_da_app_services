"""v1: Farmer profile and listing APIs (DA-40, DA-39).

GET /api/v1/da-services/farmers
GET /api/v1/da-services/farmers/{id}/profile
GET /api/v1/da-services/farmers/{id}/visits
GET /api/v1/da-services/farmers/{id}/permissions
GET /api/v1/da-services/farmers/{id}/plots

DA-39: Adds pending action state (system-derived chip) to farmer list.

Scope selector:
- Development Agent: My linked farmers only
- Supervisor/Woreda: All farmers in their Woreda
- Executive/Regional: All farmers in their Region (read-only)
- OAN Administrator: All farmers

Contract: docs/api-da-screens.md §2.2, §2.3
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint
from oan_auth_service.api.utils import handle_api_errors, success_response
from pydantic import ValidationError as PydanticValidationError

from da_services.api.router import route
from da_services.integrations.farmer_registry import get_farmer_registry_client
from da_services.integrations.farmer_registry.schemas import FarmerRegistrationForm
from da_services.services import constants as C
from da_services.services import farmers
from da_services.utils.permissions import require_roles

# Roles that may read farmers
READ_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)

# Roles that may register a new farmer (Executive is read-only)
REGISTER_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_ADMIN)


@route("/farmers", methods=("GET",), summary="List farmers with scope selector")
@frappe.whitelist()
@handle_api_errors
def list_farmers(woreda=None, kebele=None, state=None, da_id=None, limit=None, start=None):
	"""List farmers visible to the user with scope enforcement (DA-40, DA-39).

	Scope selector:
	- Development Agent: Only linked farmers
	- Supervisor: All farmers in their Woreda
	- Executive: All farmers in their Region (read-only)
	- Admin: All farmers

	Query parameters:
	- woreda: Filter by woreda code
	- kebele: Filter by kebele code
	- state: Filter by registration state (APPROVED, PENDING_APPROVAL, etc.)
	- da_id: Filter by linked DA
	- limit: Page size (default 50, max 500)
	- start: Offset (default 0)

	Returns:
	    {
	        "success": true,
	        "data": {
	            "farmers": [
	                {
	                    "farmer_id": "...",
	                    "reg_id": "FR-xxxxx",
	                    "farmer_name": "...",
	                    "phone": "...",
	                    "kebele": "...",
	                    "woreda": "...",
	                    "region": "...",
	                    "total_land_hectares": 2.5,
	                    "registration_state": "APPROVED",
	                    "registered_at": "...",
	                    "visit_count": 5,
	                    "last_visit": "...",
	                    "pending_action": "Crop survey due",
	                    "pending_action_priority": "high",
	                    "pending_action_reason": "Last crop survey 45 days ago"
	                }
	            ],
	            "total": 123,
	            "page": {"limit": 50, "start": 0}
	        }
	    }

	DA-39 Pending Actions:
	- pending_action: "Crop survey due" | "Livestock due" | "Credit in review" |
	                  "Fayda check" | "No action due" (system-derived)
	- pending_action_priority: "high" | "medium" | "low"
	- pending_action_reason: Optional description
	"""
	ctx = require_roles(*READ_ROLES)

	# Parse params
	woreda = woreda or frappe.form_dict.get("woreda")
	kebele = kebele or frappe.form_dict.get("kebele")
	state = state or frappe.form_dict.get("state")
	da_id = da_id or frappe.form_dict.get("da_id")

	try:
		limit = cint(limit or frappe.form_dict.get("limit") or 50)
		start = cint(start or frappe.form_dict.get("start") or 0)
	except ValueError, TypeError:
		frappe.throw(_("Pagination parameters must be integers."), frappe.ValidationError)

	if limit < 1 or limit > 500:
		frappe.throw(_("Limit must be between 1 and 500."), frappe.ValidationError)
	if start < 0:
		frappe.throw(_("Start must be >= 0."), frappe.ValidationError)

	# Get farmers
	farmer_list, total = farmers.list_farmers(
		ctx,
		woreda=woreda,
		kebele=kebele,
		state=state,
		da_id=da_id,
		limit=limit,
		start=start,
	)

	return success_response(
		data={
			"farmers": farmer_list,
			"total": total,
			"page": {"limit": limit, "start": start},
		}
	)


@route("/farmers/<farmer_id>/profile", methods=("GET",), summary="Get farmer profile")
@frappe.whitelist()
@handle_api_errors
def get_farmer_profile(farmer_id: str):
	"""Get complete farmer profile (DA-40 profile screen).

	Combines:
	- Farmer Registry data (identity, contact, location, demographics, etc.)
	- DA Visit history (last visit, pending actions)
	- Edit permissions (can_edit flag)

	Args:
	    farmer_id: Farmer Registry internal_record_id

	Returns:
	    {
	        "success": true,
	        "data": {
	            "farmer_id": "...",
	            "reg_id": "FR-xxxxx",
	            "name": "...",
	            "kebele": "...",
	            "verification_state": null,
	            "pending_actions": 3,
	            "phone": "...",
	            "email": "...",
	            "household_size": 5,
	            "total_land_area": 2.5,
	            "land_ownership": "OWNER",
	            "primary_crop": null,
	            "registration_date": "...",
	            "last_visit_date": "...",
	            "last_visit_outcome": "...",
	            "can_edit": true,
	            "is_linked": true
	        }
	    }

	Raises:
	    DoesNotExistError: Farmer not found
	    PermissionError: Farmer not in scope
	"""
	ctx = require_roles(*READ_ROLES)

	if not farmer_id or not isinstance(farmer_id, str):
		frappe.throw(_("Farmer ID is required."), frappe.ValidationError)

	profile = farmers.get_farmer_profile(farmer_id, ctx)
	return success_response(data=profile)


@route("/farmers/<farmer_id>/visits", methods=("GET",), summary="Get farmer visit history")
@frappe.whitelist()
@handle_api_errors
def get_farmer_visits(farmer_id: str, limit=None, start=None):
	"""Get visit history for a farmer (DA-40 Visit History tab).

	Args:
	    farmer_id: Farmer Registry internal_record_id
	    limit: Page size (default 50)
	    start: Offset (default 0)

	Returns:
	    {
	        "success": true,
	        "data": {
	            "visits": [
	                {
	                    "name": "DA-VIS-...",
	                    "scheduled_at": "...",
	                    "physical_status": "Physical Visit Complete",
	                    "visit_type": "Crop Survey",
	                    "outcome": "...",
	                    "da_id": "..."
	                }
	            ],
	            "total": 15
	        }
	    }

	Raises:
	    DoesNotExistError: Farmer not found
	    PermissionError: Farmer not in scope
	"""
	ctx = require_roles(*READ_ROLES)

	if not farmer_id:
		frappe.throw(_("Farmer ID is required."), frappe.ValidationError)

	# Scope check: Ensure farmer is in user's scope before showing visits
	# This prevents a DA from reading another DA's farmer visits
	farmers.get_farmer_profile(farmer_id, ctx)

	# Validate pagination
	try:
		limit = cint(limit or frappe.form_dict.get("limit") or 50)
		start = cint(start or frappe.form_dict.get("start") or 0)
	except ValueError, TypeError:
		frappe.throw(_("Pagination parameters must be integers."), frappe.ValidationError)

	# Get visits
	visits = frappe.get_all(
		"DA Visit",
		filters={"farmer_id": farmer_id},
		fields=[
			"name",
			"farmer_id",
			"scheduled_at",
			"physical_status",
			"visit_type",
			"outcome",
			"da_id",
			"completed_at",
		],
		order_by="scheduled_at desc",
		limit=limit,
		start=start,
	)

	total = frappe.db.count("DA Visit", {"farmer_id": farmer_id})

	return success_response(
		data={
			"visits": visits,
			"total": total,
		}
	)


@route("/farmers/<farmer_id>/permissions", methods=("GET",), summary="Check farmer edit permissions")
@frappe.whitelist()
@handle_api_errors
def check_farmer_permissions(farmer_id: str):
	"""Check if user can edit this farmer (DA-41 permission check).

	Used by frontend to show/hide Edit button and validate actions.

	Args:
	    farmer_id: Farmer Registry internal_record_id

	Returns:
	    {
	        "success": true,
	        "data": {
	            "can_edit": true,
	            "reason": null,
	            "is_linked": true,
	            "link_type": "Automatic"
	        }
	    }
	"""
	ctx = require_roles(*READ_ROLES)

	if not farmer_id:
		frappe.throw(_("Farmer ID is required."), frappe.ValidationError)

	permissions = farmers.check_farmer_edit_permissions(farmer_id, ctx)
	return success_response(data=permissions)


@route("/farmers/<farmer_id>/plots", methods=("GET",), summary="Get farmer land plots")
@frappe.whitelist()
@handle_api_errors
def get_farmer_plots(farmer_id: str):
	"""Get land plots for a farmer (DA-40 Holdings tab).

	Args:
	    farmer_id: Farmer Registry internal_record_id

	Returns:
	    {
	        "success": true,
	        "data": {
	            "plots": [
	                {
	                    "plot_id": "...",
	                    "land_kebele": "...",
	                    "area_in_hectare": 2.5,
	                    "land_ownership_type": "OWNER",
	                    "soil_fertility": "Good",
	                    "current_land_use": "CROP_PRODUCTION",
	                    "certificate_provided": true
	                }
	            ]
	        }
	    }

	Raises:
	    DoesNotExistError: Farmer not found
	    PermissionError: Farmer not in scope
	"""
	ctx = require_roles(*READ_ROLES)

	if not farmer_id:
		frappe.throw(_("Farmer ID is required."), frappe.ValidationError)

	# Scope check: Ensure farmer is in user's scope before showing plots
	farmers.get_farmer_profile(farmer_id, ctx)

	# Get plots from Farmer Registry
	client = get_farmer_registry_client()
	plots = client.get_farmer_plots(farmer_id)

	return success_response(
		data={
			"plots": [plot.dict() for plot in plots],
		}
	)


@route("/farmers", methods=("POST",), summary="Register a new farmer")
@frappe.whitelist()
@handle_api_errors
def register_farmer():
	"""Register a new farmer via the Farmer Registry ingest pipeline.

	The DA fills the registration form; this submits it to the registry's
	ingest-data endpoint. Ingestion is asynchronous, so the response carries a
	correlation_id (and a local event_id) to track progress, not a final farmer.

	Allowed roles: Development Agent, Supervisor, Admin (Executive is read-only).

	Request body (JSON):
	    {
	        "first_name": "Abebe",            // required
	        "father_name": "Kebede",
	        "grandfather_name": "Tesfaye",
	        "gender": "male",
	        "date_of_birth": "1985-04-12",
	        "phone": "+251911234567",
	        "woreda": "40801",                // numeric admin code
	        "kebele": "40801101001",          // numeric admin code
	        "national_id": "1234567890123456",
	        "location": "9.0300 38.7400 2350 5",
	        "lands": [{"ownership": "owner", "area_hectare": 1.5}],
	        "crops": [{"name": "teff", "planted_date": "2026-07-01"}],
	        "livestock": [{"animal": "cattle", "count": 3}],
	        "household_head": true,           // true = registry also creates a Household
	        "is_psnp_user": false,
	        "disabled": false,
	        "marital_status": "married",
	        "education_level": "can_read_and_write",
	        "income_source": "crop_production",
	        "da_id": "DA-ID-001",
	        "client_op_id": "<uuid>"          // optional idempotency key
	    }

	The body is validated against FarmerRegistrationForm (400 on bad input).
	With client_op_id, resending the same request returns the original event
	(status "DUPLICATE", duplicate: true) instead of registering twice.

	Returns:
	    {
	        "success": true,
	        "data": {
	            "correlation_id": "...",   // track async ingestion
	            "event_id": "DA-INT-00123",
	            "status": "SUCCESS",
	            "message_id": "da-svc-...",
	            "duplicate": false
	        }
	    }
	"""
	ctx = require_roles(*REGISTER_ROLES)

	# Parse JSON body (silent=True avoids a 500 on malformed JSON)
	request_data = frappe.request.get_json(silent=True) if frappe.request else None
	if not request_data and frappe.form_dict:
		framework_keys = {"cmd", "_", "sid"}
		request_data = {k: v for k, v in frappe.form_dict.items() if k not in framework_keys}

	if not request_data:
		frappe.throw(_("Request body is required and must be valid JSON."), frappe.ValidationError)

	# Validate at the boundary: types, ranges and formats are checked here so the
	# service and mapper only ever see a well-formed form.
	try:
		form = FarmerRegistrationForm(**request_data)
	except PydanticValidationError as e:
		problems = "; ".join(
			f"{'.'.join(str(p) for p in err['loc']) or 'body'}: {err['msg']}" for err in e.errors()
		)
		frappe.throw(_("Invalid registration form: {0}").format(problems), frappe.ValidationError)

	result = farmers.register_farmer(form.model_dump(mode="json", exclude_none=True), ctx)

	return success_response(
		message=_("Registration already submitted.")
		if result.get("duplicate")
		else _("Farmer submitted for registration."),
		data={
			"correlation_id": result.get("correlation_id"),
			"event_id": result.get("event_id"),
			"status": result.get("status"),
			"message_id": result.get("message_id"),
			"duplicate": bool(result.get("duplicate")),
		},
	)
