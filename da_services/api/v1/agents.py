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

# Valid enum values for filters
VALID_ACTIVE_STATUSES = frozenset(C.ACTIVE_STATUSES)
VALID_APPROVAL_STATUSES = frozenset(C.APPROVAL_STATUSES)
VALID_FAYDA_STATUSES = frozenset({"Verified", "Pending", "Mismatch"})
VALID_EDUCATION_TIERS = frozenset({"Diploma", "Degree", "Certificate", "MSc", "PhD"})

# Filters with a fixed set of values, checked before the service runs. Approval status is matched as given:
# the registry may use values beyond C.APPROVAL_STATUSES, and the facets only offer values it returned.
ENUM_FILTERS = {
	"active_status": VALID_ACTIVE_STATUSES,
	"fayda_status": VALID_FAYDA_STATUSES,
	"education_tier": VALID_EDUCATION_TIERS,
}


def _parse_filters(given: dict, fields: tuple[str, ...] = da_service.FILTER_FIELDS) -> dict:
	"""The list filters (of fields) from the call or the query string, each one value or several
	(comma-separated).

	Raises ValidationError for a value outside an enum filter's set.
	"""
	filters = {}
	for field in fields:
		value = given.get(field) or frappe.form_dict.get(field)
		values = da_service.split_values(value)
		valid = ENUM_FILTERS.get(field)
		for v in values if valid else ():
			if v not in valid:
				frappe.throw(
					_("Invalid {0}: {1}. Valid values: {2}").format(field, v, ", ".join(sorted(valid))),
					frappe.ValidationError,
				)
		if values:
			filters[field] = values
	return filters


def _parse_page(limit, start) -> tuple[int, int]:
	"""limit (default 50, 1..500) and start (default 0) from the call or the query string."""
	try:
		limit = cint(limit or frappe.form_dict.get("limit") or 50)
		start = cint(start or frappe.form_dict.get("start") or 0)
	except ValueError, TypeError:
		frappe.throw(
			_("Invalid pagination parameters: limit and start must be integers."), frappe.ValidationError
		)

	# Validate limit bounds
	if limit < 1:
		limit = 50
	if limit > 500:
		limit = 500
	if start < 0:
		start = 0
	return limit, start


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
	approval_status=None,
	specialisation=None,
	search=None,
	limit=None,
	start=None,
):
	"""List Development Agents visible to the user with scope enforcement (DA-226).

	Scope selector, from the caller's DA RBAC grants:
	- Region / Zone / Woreda grants: the DAs in those areas
	- No area granted: only the caller's own DA record

	Query parameters:
	- woreda: Filter by woreda code (e.g., "ET04-W01")
	- kebele: Filter by kebele code (e.g., "ET04-W01-K03")
	- region: Filter by region code (e.g., "ET04")
	- fayda_status: Filter by Fayda verification status (Verified, Pending, Mismatch)
	- active_status: Filter by active status (Active, On-leave, Separated)
	- education_tier: Filter by education tier (Diploma, Degree, Certificate, MSc, PhD)
	- approval_status: Filter by registry approval status (e.g. Published)
	- specialisation: Filter by specialisation (e.g. Crop production)
	- search: Search by DA-ID or full name (case-insensitive partial match)
	- limit: Page size (default 50, max 500)
	- start: Offset for pagination (default 0)

	Every filter takes one value or several, comma-separated ("ET04-W01,ET03-W02"); a DA matches
	any of them. "unassigned" matches a DA with no area at that level (e.g. no Kebele).

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
	            "page": {"limit": 50, "start": 0},
	            "facets": {
	                "region": [{"value": "ET04", "label": "Oromia", "count": 30}],
	                "woreda": [...], "kebele": [...], "active_status": [...],
	                "education_tier": [...], "fayda_status": [...], "approval_status": [...]
	            }
	        }
	    }

	Errors:
	- 403: Permission denied (user lacks DA read roles)
	- 400: Validation error (invalid filter values)
	"""

	# Parse filters (validated) and pagination
	filters = _parse_filters(
		{
			"woreda": woreda,
			"kebele": kebele,
			"region": region,
			"fayda_status": fayda_status,
			"active_status": active_status,
			"education_tier": education_tier,
			"approval_status": approval_status,
			"specialisation": specialisation,
		}
	)
	search = search or frappe.form_dict.get("search")
	limit, start = _parse_page(limit, start)

	# Call service layer
	result = da_service.search_agents(search=search, limit=limit, start=start, **filters)

	return success_response(
		data={
			"agents": result["agents"],
			"total": result["total"],
			"page": {"limit": limit, "start": start},
			"facets": result["facets"],
		}
	)


@route(
	"/agents/my-team", methods=("GET",), summary="List the Development Agents of the signed-in user's team"
)
@frappe.whitelist()
@handle_api_errors
def list_my_team(
	woreda=None,
	kebele=None,
	region=None,
	fayda_status=None,
	specialisation=None,
	search=None,
	limit=None,
	start=None,
):
	"""List the Development Agents of the signed-in user's team, from the DA Hierarchy.

	Team selector (the DA Hierarchy: who reports to whom):
	- Admin (OAN Administrator / System Manager / Administrator): every DA ("all")
	- Supervisor: every DA posted to their Woreda ("woreda"); the Woreda comes from their DA
	  Hierarchy node, else their DA Woreda User Permissions, else their own DA record
	- someone else with people under them (e.g. an Executive): their reports, at every level
	  ("reports")
	- a DA (nobody under them): every DA posted to the DA's own Kebele, them included ("kebele");
	  the Kebele comes from the DA's registry record, else their DA Hierarchy node
	- no DA Hierarchy node: an empty list ("none")

	Each team member bound to a DA record (User.da_registry_id) is listed with that record.

	Query parameters (each filter: one value or several, comma-separated; a DA matches any of them):
	- region / woreda / kebele: Narrow within the team (codes; "unassigned" = no area at that level)
	- fayda_status: Filter by Fayda status (Verified, Pending, Mismatch)
	- specialisation: Filter by specialisation (e.g. Crop production)
	- search: Search by DA-ID or full name (case-insensitive partial match)
	- limit: Page size (default 50, max 500)
	- start: Offset for pagination (default 0)

	Returns:
	    {
	        "success": true,
	        "data": {
	            "team": "reports",
	            "root": {"name": "sup@x", "user": "sup@x", "full_name": "...", "designation": "...",
	                     "region": "ET04", "zone": "ET04-Z01", "woreda": "ET04-W01", "kebele": null,
	                     "is_group": true},
	            "agents": [
	                {
	                    "da_id": "DA-000001",
	                    "full_name": "Abebe Kebede",
	                    "woreda": "ET04-W01",
	                    "kebele": "ET04-W01-K03",
	                    ...
	                }
	            ],
	            "total": 2,
	            "page": {"limit": 50, "start": 0},
	            "facets": {
	                "region": [{"value": "ET04", "label": "Oromia", "count": 2}],
	                "woreda": [...], "kebele": [...], "fayda_status": [...], "specialisation": [...]
	            }
	        }
	    }

	root is the DA Hierarchy node the team hangs from (the caller, or the root); for "kebele" it
	is the DA's own node; null for "none". areas: the Woreda codes for "woreda", the Kebele code
	for "kebele", else []. reports_to is the caller's boss (their parent DA Hierarchy node, same
	shape as root); null at the top of the tree or with no node.

	active_status, education_tier and approval_status are left out of this list: not on each agent,
	not in the facets, and not accepted as filters (GET /agents still has them).

	Errors:
	- 401: Not signed in
	- 400: Validation error (invalid filter values)
	"""

	# Parse filters (validated) and pagination
	filters = _parse_filters(
		{
			"woreda": woreda,
			"kebele": kebele,
			"region": region,
			"fayda_status": fayda_status,
			"specialisation": specialisation,
		},
		fields=da_service.TEAM_FILTER_FIELDS,
	)
	search = search or frappe.form_dict.get("search")
	limit, start = _parse_page(limit, start)

	# Call service layer
	result = da_service.list_my_team(search=search, limit=limit, start=start, **filters)

	return success_response(
		data={
			"team": result["team"],
			"root": result["root"],
			"areas": result["areas"],
			"reports_to": result["reports_to"],
			"agents": result["agents"],
			"total": result["total"],
			"page": {"limit": limit, "start": start},
			"facets": result["facets"],
		}
	)


@route(
	"/agents/my-team/tree",
	methods=("GET",),
	summary="The signed-in user's team as a tree, from the DA Hierarchy",
)
@frappe.whitelist()
@handle_api_errors
def my_team_tree():
	"""The signed-in user's team as a tree (who reports to whom), from the DA Hierarchy.

	The tree hangs from the same node as GET /agents/my-team: the root for an Admin, the caller
	for someone with people under them, the caller's boss for a DA.

	Returns:
	    {
	        "success": true,
	        "data": {
	            "team": "reports",
	            "tree": {
	                "name": "sup@x", "user": "sup@x", "full_name": "...", "designation": "...",
	                "region": "ET04", "zone": "ET04-Z01", "woreda": "ET04-W01", "kebele": null,
	                "is_group": true, "da_id": null,
	                "reports": [
	                    {"name": "da1@x", "user": "da1@x", "full_name": "...", "da_id": "DA-000001",
	                     "is_group": false, "reports": [], ...}
	                ]
	            }
	        }
	    }

	tree is null when the caller has no DA Hierarchy node ("none").

	Errors:
	- 401: Not signed in
	"""
	return success_response(data=da_service.my_team_tree())
