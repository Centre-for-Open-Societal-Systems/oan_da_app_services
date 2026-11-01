"""v1: Farmer profile and listing APIs (DA-40, DA-39).

GET /api/v1/da-services/farmers                (?search_text= searches the live registry, DA-272)
GET /api/v1/da-services/farmers/{id}/profile
GET /api/v1/da-services/farmers/{id}/visits
GET /api/v1/da-services/farmers/{id}/permissions
GET /api/v1/da-services/farmers/{id}/plots
GET /api/v1/da-services/farmers/{id}/photo          (signed link, read fresh)
GET /api/v1/da-services/farmers/{id}/change-requests   (DA-272)
POST /api/v1/da-services/farmers
POST /api/v1/da-services/farmers/registrations/documents
GET /api/v1/da-services/farmers/registrations/{submission_id}   (DA-272)

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
from oan_auth_service.api.utils import error_response, handle_api_errors, success_response
from pydantic import ValidationError as PydanticValidationError

from da_services.api.router import route
from da_services.integrations.farmer_registry import get_farmer_registry_client
from da_services.integrations.farmer_registry.client import (
	FarmerRegistryClientError,
	FarmerRegistryServerError,
)
from da_services.integrations.farmer_registry.schemas import FarmerRegistrationForm
from da_services.services import constants as C
from da_services.services import farmers
from da_services.utils.permissions import require_roles

# Roles that may read farmers
READ_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)

# Roles that may register a new farmer (Executive is read-only)
REGISTER_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_ADMIN)

_REGISTRY_UNAVAILABLE = _("Farmer Registry is unavailable. Try again shortly.")


@route("/farmers", methods=("GET",), summary="List farmers with scope selector")
@frappe.whitelist()
@handle_api_errors
def list_farmers(
	woreda=None,
	kebele=None,
	state=None,
	da_id=None,
	limit=None,
	start=None,
	search_text=None,
	page=None,
	page_size=None,
):
	"""List farmers visible to the user with scope enforcement (DA-40, DA-39, DA-272).

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
	- page / page_size: 1-based alternative to limit / start (registry style)
	- search_text: Free text (name, Farmer-ID, phone). When given, the live
	  Farmer register is searched inside the caller's scope instead of the
	  DA Farmer Reference cache, so newly registered farmers are found too.
	  Supervisors of several Woredas, Zone and Region officers must pass
	  ``woreda`` with it. ``da_id`` does not apply to a live search.

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

	search_text = (search_text or frappe.form_dict.get("search_text") or "").strip()
	page = page or frappe.form_dict.get("page")
	page_size = page_size or frappe.form_dict.get("page_size")

	try:
		if page or page_size:
			# Registry-style paging; converted to limit / start for the cache path.
			page = cint(page or 1)
			limit = cint(page_size or limit or frappe.form_dict.get("limit") or 50)
			if page < 1:
				frappe.throw(_("Page must be >= 1."), frappe.ValidationError)
			start = (page - 1) * limit
		else:
			limit = cint(limit or frappe.form_dict.get("limit") or 50)
			start = cint(start or frappe.form_dict.get("start") or 0)
	except ValueError, TypeError:
		frappe.throw(_("Pagination parameters must be integers."), frappe.ValidationError)

	if limit < 1 or limit > 500:
		frappe.throw(_("Limit must be between 1 and 500."), frappe.ValidationError)
	if start < 0:
		frappe.throw(_("Start must be >= 0."), frappe.ValidationError)

	if search_text:
		try:
			found = farmers.search_farmers(
				ctx,
				search_text=search_text,
				woreda=woreda,
				kebele=kebele,
				state=state,
				page=start // limit + 1,
				page_size=limit,
			)
		except FarmerRegistryClientError as exc:
			frappe.throw(str(exc), frappe.ValidationError)
		except FarmerRegistryServerError:
			frappe.response["http_status_code"] = 503
			return error_response(str(_REGISTRY_UNAVAILABLE), "SERVICE_UNAVAILABLE")
		return success_response(
			data={
				"farmers": found["farmers"],
				"total": found["total"],
				"page": {
					"limit": limit,
					"start": start,
					"page": found["page"],
					"page_size": found["page_size"],
					"number_of_pages": found["number_of_pages"],
					# True when scope was applied here because the registry ignored the
					# filter; total / number_of_pages are then the registry's figures.
					"filtered_client_side": found["filtered_client_side"],
				},
				"source": "registry",
			}
		)

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
			"page": {
				"limit": limit,
				"start": start,
				"page": start // limit + 1,
				"page_size": limit,
				"number_of_pages": (total + limit - 1) // limit,
			},
			"source": "cache",
		}
	)


@route(
	"/farmers/<farmer_id>/change-requests",
	methods=("GET",),
	summary="List change requests raised on a farmer in the registry",
)
@frappe.whitelist()
@handle_api_errors
def get_farmer_change_requests(farmer_id: str, status=None, tab=None):
	"""Change requests the Farmer Registry holds for this farmer (DA-272).

	Every PATCH /farmers/{id} becomes a registry change request that a Woreda
	officer approves; this shows where each one stands. Staff-portal edits on
	the same farmer appear here too.

	Query parameters:
	- status: PENDING | APPROVED | REJECTED (all when omitted)
	- tab: farmer | location | household | land | crop | livestock (all when omitted)

	Returns:
	    {"farmer_id", "total", "change_requests": [
	        {"change_request_id", "status", "tab_id", "section_id", "source",
	         "created_by", "created_at", "approved_by", "approved_at",
	         "verifications_required", "verifications_done"}]}

	404 when the farmer does not exist, 403 when outside the caller's scope.
	"""
	ctx = require_roles(*READ_ROLES)
	if not farmer_id:
		frappe.throw(_("Farmer ID is required."), frappe.ValidationError)
	status = (status or frappe.form_dict.get("status") or "").strip().upper() or None
	tab = (tab or frappe.form_dict.get("tab") or "").strip().lower() or None
	if status and status not in ("PENDING", "APPROVED", "REJECTED"):
		frappe.throw(_("status must be PENDING, APPROVED or REJECTED."), frappe.ValidationError)

	try:
		data = farmers.get_farmer_change_requests(farmer_id, ctx, status=status, tab=tab)
	except FarmerRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except FarmerRegistryServerError:
		frappe.response["http_status_code"] = 503
		return error_response(str(_REGISTRY_UNAVAILABLE), "SERVICE_UNAVAILABLE")
	return success_response(data=data)


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

	# The path may carry the Farmer-ID people see (FR-…); the registry wants the internal id.
	profile = farmers.get_farmer_profile(farmers.resolve_farmer_id(farmer_id), ctx)
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
	farmer_id = farmers.resolve_farmer_id(farmer_id)
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

	permissions = farmers.check_farmer_edit_permissions(farmers.resolve_farmer_id(farmer_id), ctx)
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
	farmer_id = farmers.resolve_farmer_id(farmer_id)
	farmers.get_farmer_profile(farmer_id, ctx)

	# Get plots from Farmer Registry
	client = get_farmer_registry_client()
	plots = client.get_farmer_plots(farmer_id)

	return success_response(
		data={
			"plots": [plot.dict() for plot in plots],
		}
	)


@route("/farmers/<farmer_id>/photo", methods=("GET",), summary="Get the farmer's record photo")
@frappe.whitelist()
@handle_api_errors
def get_farmer_photo(farmer_id: str):
	"""The farmer's photo from the Farmer Registry record.

	The registry hands out a signed link that expires (about an hour), so it is
	read fresh on every call rather than kept with the cached profile. The link
	is usable straight in an <img>; ask again once it expires.

	Returns:
	    {"photo_url": "https://…" | null, "document_id": "…" | null}

	Raises:
	    DoesNotExistError: Farmer not found
	    PermissionError: Farmer not in scope
	"""
	ctx = require_roles(*READ_ROLES)

	if not farmer_id:
		frappe.throw(_("Farmer ID is required."), frappe.ValidationError)

	farmer_id = farmers.resolve_farmer_id(farmer_id)
	farmers.get_farmer_profile(farmer_id, ctx)

	photo = get_farmer_registry_client().get_farmer_photo(farmer_id) or {}
	return success_response(data={"photo_url": photo.get("url"), "document_id": photo.get("document_id")})


@route("/farmers", methods=("POST",), summary="Register a new farmer")
@frappe.whitelist()
@handle_api_errors
def register_farmer():
	"""Register a new farmer in the Farmer Registry.

	Site config farmer_registry_register_mode picks the registry path:
	"ingest" (default, one async ingest-data call, returns correlation_id) or
	"intake" (the staff intake form: each section saved into one draft, then
	finalized for approval; returns submission_id).

	Allowed roles: Development Agent, Supervisor, Admin (Executive is read-only).

	Request body (JSON):
	    {
	        "first_name": "Abebe",            // required
	        "father_name": "Kebede",
	        "grandfather_name": "Tesfaye",    // registry last name; required for intake
	        "gender": "male",                 // required for intake
	        "date_of_birth": "1985-04-12",    // required for intake
	        "phone": "0911234567",
	        "kebele": "ET04080101",           // intake: Master Data kebele id; ingest: numeric P-code
	        "national_id": "1234567890123456",  // Fayda FAN (16) or FIN (12)
	        "fayda_rid": "<29 digits>",
	        "location": "9.0300 38.7400 2350 5",
	        "marital_status": "married",      // required for intake
	        "education_level": "primary",     // required for intake
	        "income_sources": ["crop", "livestock"],
	        "disabled": false,
	        "household": {                    // the farmer is the head and counts in size
	            "size": 2, "other_land_owner": false,
	            "members": [{"first_name": "Almaz", "grandfather_name": "Tesfaye", "gender": "female",
	                         "date_of_birth": "1990-01-01", "relationship": "spouse"}]
	        },
	        "identity_document_id": "<from POST /farmers/registrations/documents>",  // required for intake
	        "identity_document_type": "fayda",
	        "lands": [{"ownership": "owner", "area_hectare": 1.5}],
	        "crops": [{"name": "teff", "planted_date": "2026-07-01"}],
	        "livestock": [{"animal": "cattle", "count": 3}],
	        "da_id": "DA-ID-001",
	        "client_op_id": "<uuid>"          // optional idempotency key
	    }

	The body is validated against FarmerRegistrationForm (400 on bad input);
	in intake mode, missing required fields and unknown codes are listed in
	one 400. With client_op_id, resending the same request returns the
	original event (status "DUPLICATE", duplicate: true) instead of
	registering twice.

	Returns:
	    {
	        "success": true,
	        "data": {
	            "correlation_id": "...",   // ingest
	            "submission_id": "...",    // intake
	            "event_id": "DA-INT-00123",
	            "status": "SUCCESS" | "PENDING_APPROVAL",
	            "message_id": "da-svc-...",
	            "duplicate": false
	        }
	    }

	If the registry answers but does not accept the submission, the response is
	a 502 error (code FARMER_REGISTRY_REJECTED) carrying the event_id, the
	registry's status and error code, and for intake the section that failed;
	the registry's own message (e.g. a household rule) is in "message".
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

	# The registry answered but did not accept the submission (e.g. SYS-ERR-001):
	# nothing was registered, so this must not read as a success to the DA.
	if not result.get("success") and not result.get("duplicate"):
		frappe.response["http_status_code"] = 502
		return error_response(
			_("Farmer Registry did not accept the registration: {0}").format(
				result.get("error_message") or result.get("status") or _("unknown error")
			),
			"FARMER_REGISTRY_REJECTED",
			details={
				"event_id": result.get("event_id"),
				"message_id": result.get("message_id"),
				"submission_id": result.get("submission_id"),
				"failed_section": result.get("failed_section"),
				"registry_status": result.get("status"),
				"registry_error_code": result.get("error_code"),
			},
		)

	return success_response(
		message=_("Registration already submitted.")
		if result.get("duplicate")
		else _("Farmer submitted for registration."),
		data={
			"correlation_id": result.get("correlation_id"),
			"submission_id": result.get("submission_id"),
			"event_id": result.get("event_id"),
			"status": result.get("status"),
			"message_id": result.get("message_id"),
			"duplicate": bool(result.get("duplicate")),
		},
	)


@route(
	"/farmers/registrations/<submission_id>",
	methods=("GET",),
	summary="Read a farmer registration back from the registry",
)
@frappe.whitelist()
@handle_api_errors
def get_registration(submission_id: str):
	"""Everything the registry holds for one registration (DA-272).

	Use the ``submission_id`` returned by POST /farmers. Until registry staff
	approve it the farmer exists only here, not in /farmers or /profile.

	Returns:
	    {"submission_id", "application_reference", "record_name",
	     "status": DRAFT | PENDING_APPROVAL | APPROVED | REJECTED,
	     "draft_status", "approval_status", "finalized_at", "approved_by", "approved_at",
	     "sections": [{"section_id", "section_register_id", "is_list", "records": [...], "documents": [...]}],
	     "event": {"event_id", "status", "last_error", "submitted_by", "submitted_at"} | null}

	Admin reads any submission; other roles only ones made through DA
	Services, and a DA only their own. 404 when unknown.
	"""
	ctx = require_roles(*READ_ROLES)
	if not submission_id:
		frappe.throw(_("submission_id is required."), frappe.ValidationError)
	try:
		data = farmers.get_registration(submission_id, ctx)
	except FarmerRegistryClientError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	except FarmerRegistryServerError:
		frappe.response["http_status_code"] = 503
		return error_response(str(_REGISTRY_UNAVAILABLE), "SERVICE_UNAVAILABLE")
	return success_response(data=data)


@route(
	"/farmers/registrations/documents",
	methods=("POST",),
	status=201,
	summary="Upload a farmer identity document for registration",
)
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def upload_registration_document(**kwargs):
	"""Upload the farmer's identity document to the Farmer Registry.

	multipart/form-data with field "file" (PDF, JPEG or PNG, up to 5 MB).
	Returns {"document_id", "file_name", "size"}; send document_id as
	identity_document_id in POST /farmers. Needed for intake-mode registration.
	"""
	ctx = require_roles(*REGISTER_ROLES)
	request = getattr(frappe.local, "request", None)
	upload = request.files.get("file") if request is not None and request.files else None
	if upload is None:
		frappe.throw(_("file is required (multipart/form-data)."), frappe.ValidationError)
	out = farmers.upload_registration_document(
		upload.read(), upload.filename, upload.mimetype or upload.content_type, ctx
	)
	return success_response(data=out, message=_("Document uploaded."))
