"""v1: Development Agent listing API (DA-226).

GET /api/v1/da-services/agents

Scope-aware DA listing with filters and search:
- Development Agent: Sees only self
- Supervisor: All DAs in their Woreda
- Executive: All DAs in their Region
- Administrator: All DAs

Contract: DA-226 specification
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.services import constants as C
from da_services.services import da_service
from da_services.utils.permissions import require_roles

# Roles that may read DA records
READ_ROLES = C.DA_READ_ROLES


# Valid enum values for filters
VALID_ACTIVE_STATUSES = frozenset(C.ACTIVE_STATUSES)
VALID_APPROVAL_STATUSES = frozenset(C.APPROVAL_STATUSES)
VALID_FAYDA_STATUSES = frozenset({"Verified", "Pending", "Mismatch"})
VALID_EDUCATION_TIERS = frozenset({"Diploma", "Degree", "Certificate", "MSc", "PhD"})


@route("/agents", methods=("GET",), summary="List Development Agents with RBAC scope")
@frappe.whitelist()
@handle_api_errors
def list_agents(
	woreda=None,
	kebele=None,
	region=None,
	fayda_status=None,
	active_status=None,
	education_tier=None,
	search=None,
	limit=None,
	start=None,
):
	"""List Development Agents visible to the user with scope enforcement (DA-226).

	Scope selector:
	- Development Agent: Only their own record
	- Supervisor: All DAs in their Woreda(s)
	- Executive: All DAs in their Region(s)
	- Administrator: All DAs (unrestricted)

	Query parameters:
	- woreda: Filter by woreda code (e.g., "ET04-W01")
	- kebele: Filter by kebele code (e.g., "ET04-W01-K03")
	- region: Filter by region code (e.g., "ET04")
	- fayda_status: Filter by Fayda verification status (Verified, Pending, Mismatch)
	- active_status: Filter by active status (Active, On-leave, Separated)
	- education_tier: Filter by education tier (Diploma, Degree, Certificate, MSc, PhD)
	- search: Search by DA-ID or full name (case-insensitive partial match)
	- limit: Page size (default 50, max 500)
	- start: Offset for pagination (default 0)

	Returns:
	    {
	        "success": true,
	        "data": {
	            "agents": [
	                {
	                    "da_id": "DA-000001",
	                    "full_name": "Abebe Kebede",
	                    "active_status": "Active",
	                    "approval_status": "Published",
	                    "region": "ET04",
	                    "zone": "ET04-Z01",
	                    "woreda": "ET04-W01",
	                    "kebele": "ET04-W01-K03",
	                    "education_tier": "Diploma",
	                    "fayda_status": "Verified",
	                    "phone": "+251911000001",
	                    "specialisation": "Crop production",
	                    "joined_at": "2019-09-01",
	                    "farmer_count": 128
	                }
	            ],
	            "total": 42,
	            "page": {"limit": 50, "start": 0}
	        }
	    }

	Errors:
	- 403: Permission denied (user lacks DA read roles)
	- 422: Validation error (invalid filter values)
	"""
	ctx = require_roles(*READ_ROLES)

	# Parse query parameters
	woreda = woreda or frappe.form_dict.get("woreda")
	kebele = kebele or frappe.form_dict.get("kebele")
	region = region or frappe.form_dict.get("region")
	fayda_status = fayda_status or frappe.form_dict.get("fayda_status")
	active_status = active_status or frappe.form_dict.get("active_status")
	education_tier = education_tier or frappe.form_dict.get("education_tier")
	search = search or frappe.form_dict.get("search")

	# Parse pagination
	try:
		limit = cint(limit or frappe.form_dict.get("limit") or 50)
		start = cint(start or frappe.form_dict.get("start") or 0)
	except (ValueError, TypeError):
		frappe.throw(_("Invalid pagination parameters: limit and start must be integers."), frappe.ValidationError)

	# Validate limit bounds
	if limit < 1:
		limit = 50
	if limit > 500:
		limit = 500
	if start < 0:
		start = 0

	# Validate filter enum values
	if active_status and active_status not in VALID_ACTIVE_STATUSES:
		frappe.throw(
			_("Invalid active_status: {0}. Valid values: {1}").format(
				active_status, ", ".join(sorted(VALID_ACTIVE_STATUSES))
			),
			frappe.ValidationError,
		)

	if fayda_status and fayda_status not in VALID_FAYDA_STATUSES:
		frappe.throw(
			_("Invalid fayda_status: {0}. Valid values: {1}").format(
				fayda_status, ", ".join(sorted(VALID_FAYDA_STATUSES))
			),
			frappe.ValidationError,
		)

	if education_tier and education_tier not in VALID_EDUCATION_TIERS:
		frappe.throw(
			_("Invalid education_tier: {0}. Valid values: {1}").format(
				education_tier, ", ".join(sorted(VALID_EDUCATION_TIERS))
			),
			frappe.ValidationError,
		)

	# Call service layer
	agents, total = da_service.list_agents(
		ctx,
		woreda=woreda,
		kebele=kebele,
		region=region,
		fayda_status=fayda_status,
		active_status=active_status,
		education_tier=education_tier,
		search=search,
		limit=limit,
		start=start,
	)

	return success_response(
		data={
			"agents": agents,
			"total": total,
			"page": {"limit": limit, "start": start},
		}
	)
