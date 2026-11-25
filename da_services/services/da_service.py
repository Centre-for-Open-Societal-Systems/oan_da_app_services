"""Development Agent listing service (DA-226).

DA listing with filters, search, paging and filter facets:
- list_agents / search_agents: every DA the registry returns
- list_my_team: the signed-in user's team by role (Admin: all, Executive: Region,
  Supervisor: Woreda, Development Agent: Kebele)
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import today

from da_services.integrations.da_registry.client import DANotFound, get_client
from da_services.services import constants as C
from da_services.utils.permissions import granted_areas

# Filters the agent lists accept. Each takes one value or several (comma-separated).
FILTER_FIELDS = (
	"region",
	"woreda",
	"kebele",
	"active_status",
	"education_tier",
	"fayda_status",
	"approval_status",
)
# The value a filter and a facet use for a DA with no area of that level (e.g. no Kebele posting).
UNASSIGNED = "unassigned"
# Geo levels cascade: a Woreda facet counts only the chosen Regions, a Kebele facet only the chosen Woredas.
_GEO_FIELDS = ("region", "woreda", "kebele")
_GEO_LABELS = {"region": "region_name", "woreda": "woreda_name", "kebele": "kebele_name"}


def split_values(value) -> list[str]:
	"""A filter value as a list: "a,b" or ["a", "b"] -> ["a", "b"]; None/"" -> []."""
	if not value:
		return []
	items = value if isinstance(value, list | tuple) else str(value).split(",")
	return [v.strip() for v in items if v and str(v).strip()]


def list_agents(
	*,
	woreda: str | None = None,
	kebele: str | None = None,
	region: str | None = None,
	fayda_status: str | None = None,
	active_status: str | None = None,
	education_tier: str | None = None,
	approval_status: str | None = None,
	search: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int]:
	"""List Development Agents visible to the user, narrowed by the filters.

	Not scoped: every DA the registry returns, narrowed by the filters. Each filter takes one
	value or several (comma-separated). See search_agents for the facets.

	Example:
	    >>> agents, total = list_agents(woreda="ET04-W01", limit=20)
	    >>> print(f"Found {total} agents, showing {len(agents)}")
	"""
	result = search_agents(
		woreda=woreda,
		kebele=kebele,
		region=region,
		fayda_status=fayda_status,
		active_status=active_status,
		education_tier=education_tier,
		approval_status=approval_status,
		search=search,
		limit=limit,
		start=start,
	)
	return result["agents"], result["total"]


def search_agents(*, search: str | None = None, limit: int = 50, start: int = 0, **filters) -> dict:
	"""Every DA the registry returns, filtered, searched and paged, with the filter facets.

	Args:
	    search: Search by DA-ID or full name (case-insensitive partial match)
	    limit: Page size (default 50, max 500)
	    start: Offset for pagination (default 0)
	    **filters: Any of FILTER_FIELDS; one value or several (comma-separated)

	Returns:
	    {"agents": [...], "total": int, "facets": {field: [{"value", "label", "count"}]}}
	"""
	# Step 1: The registry's list (unscoped: restricting it is for later)
	all_das = get_client().list_das()

	# Steps 2-9: filter, search, sort, page, facets
	return _query(all_das, filters, search=search, limit=limit, start=start)


def _query(das, filters: dict, *, search: str | None, limit: int, start: int) -> dict:
	"""Filter, search, sort and page a list of DAReference, and count the facets."""
	wanted = {field: split_values(filters.get(field)) for field in FILTER_FIELDS}

	# Step 2: Apply search (DA-ID or name)
	if search:
		search_lower = search.lower().strip()
		das = [da for da in das if search_lower in da.da_id.lower() or search_lower in da.full_name.lower()]

	# Step 3: Facets of the searched list, before the filters narrow it
	facets = _facets(das, wanted)

	# Step 4: Apply filters (each field: any of its values)
	filtered_das = [da for da in das if all(_matches(da, f, v) for f, v in wanted.items() if v)]

	# Step 5: Sort by DA-ID (consistent ordering)
	filtered_das.sort(key=lambda da: da.da_id)

	# Step 6: Calculate total before pagination
	total = len(filtered_das)

	# Step 7: Apply pagination
	paginated_das = filtered_das[start : start + limit]

	# Step 8: Convert to dicts for API response
	agents = [_da_reference_to_list_dict(da) for da in paginated_das]

	return {"agents": agents, "total": total, "facets": facets}


def _field_value(da, field: str) -> str:
	value = getattr(da, field, None)
	return value or (UNASSIGNED if field in _GEO_FIELDS else "")


def _matches(da, field: str, values: list[str]) -> bool:
	return _field_value(da, field) in values


def _facets(das, wanted: dict[str, list[str]]) -> dict[str, list[dict]]:
	"""Options for every filter, with how many DAs each would show.

	Region counts the whole list; Woreda only the chosen Regions; Kebele only the chosen Regions and
	Woredas. The status facets count the whole list.
	"""
	facets = {}
	scope = das
	for field in FILTER_FIELDS:
		counts: dict[str, int] = {}
		labels: dict[str, str] = {}
		for da in scope if field in _GEO_FIELDS else das:
			value = _field_value(da, field)
			if not value:
				continue
			counts[value] = counts.get(value, 0) + 1
			label_attr = _GEO_LABELS.get(field)
			label = getattr(da, label_attr, None) if label_attr else None
			labels.setdefault(value, label or ("Unassigned" if value == UNASSIGNED else value))
		facets[field] = [
			{"value": v, "label": labels[v], "count": counts[v]}
			for v in sorted(counts, key=lambda v: (v == UNASSIGNED, labels[v].lower()))
		]
		if field in _GEO_FIELDS and wanted.get(field):
			scope = [da for da in scope if _matches(da, field, wanted[field])]
	return facets


# The team scope of each role, widest first: a user holding several roles gets the widest one.
SCOPE_ALL = "all"
SCOPE_REGION = "region"
SCOPE_WOREDA = "woreda"
SCOPE_KEBELE = "kebele"
SCOPE_NONE = "none"
ADMIN_ROLES = frozenset({C.ROLE_ADMIN, "System Manager"})


def team_scope(user: str) -> str:
	"""The widest team scope the user's roles give: all > region > woreda > kebele > none."""
	if not user or user == "Guest":
		return SCOPE_NONE
	roles = set(frappe.get_roles(user))
	if user == "Administrator" or roles & ADMIN_ROLES:
		return SCOPE_ALL
	if C.ROLE_EXECUTIVE in roles:
		return SCOPE_REGION
	if C.ROLE_SUPERVISOR in roles:
		return SCOPE_WOREDA
	if C.ROLE_DA in roles:
		return SCOPE_KEBELE
	return SCOPE_NONE


def list_my_team(
	*,
	search: str | None = None,
	limit: int = 50,
	start: int = 0,
	user: str | None = None,
	**filters,
) -> dict:
	"""List the Development Agents of the signed-in user's team, by their role.

	- Admin (OAN Administrator / System Manager / Administrator): every DA
	- Executive: the DAs of their Region(s)
	- Supervisor: the DAs of their Woreda(s)
	- Development Agent: the DAs of their Kebele, the caller included
	- Anyone else: an empty list

	The areas come from the user's DA Region / DA Woreda User Permissions, else their active RBAC
	grants; a DA's Kebele from their Effective DA Kebele Assignment, else their registry record. A DA
	is in a Woreda / Kebele when the registry posts them there or they hold an Effective DA Kebele
	Assignment there.

	Args:
	    search: Search by DA-ID or full name (case-insensitive partial match)
	    limit: Page size (default 50, max 500)
	    start: Offset for pagination (default 0)
	    user: The caller; defaults to the session user
	    **filters: Any of FILTER_FIELDS; one value or several (comma-separated)

	Returns:
	    {"scope": str, "areas": [codes], "agents": [...], "total": int, "facets": {...}}

	Example:
	    >>> team = list_my_team(limit=20)
	    >>> print(f"{team['total']} agents in {team['scope']} {team['areas']}")
	"""
	user = user or frappe.session.user
	registry_client = get_client()

	# Step 1: The caller's scope and its areas
	scope = team_scope(user)
	areas = _scope_areas(scope, user, registry_client)

	# Step 2: The DAs in that scope
	scoped_das = _scope_das(scope, areas, registry_client)

	# Steps 3-9: filter, search, sort, page, facets
	return {
		"scope": scope,
		"areas": areas,
		**_query(scoped_das, filters, search=search, limit=limit, start=start),
	}


def _scope_areas(scope: str, user: str, registry_client) -> list[str]:
	"""The area codes the scope covers; [] for "all" and "none"."""
	if scope == SCOPE_REGION:
		return _own_areas(user, "regions", "region_scope")
	if scope == SCOPE_WOREDA:
		return _own_areas(user, "woredas", "woreda_scope")
	if scope == SCOPE_KEBELE:
		return _own_kebeles(user, registry_client)
	return []


def _scope_das(scope: str, areas: list[str], registry_client) -> list:
	"""The registry DAs in the scope's areas (plus the DAs locally assigned there)."""
	if scope == SCOPE_ALL:
		return registry_client.list_das()
	if not areas:
		return []
	field = {SCOPE_REGION: "region", SCOPE_WOREDA: "woreda", SCOPE_KEBELE: "kebele"}[scope]
	# DAs with an Effective Kebele Assignment in these areas (local assignments may be ahead of the registry)
	assigned_da_ids = set(
		frappe.get_all(
			"DA Kebele Assignment",
			filters={field: ("in", areas), "state": "Effective"},
			pluck="da_id",
		)
	)
	return [
		da for da in registry_client.list_das() if getattr(da, field) in areas or da.da_id in assigned_da_ids
	]


def _own_areas(user: str, granted_key: str, grant_field: str) -> list[str]:
	"""The user's Regions or Woredas: their DA Region / DA Woreda User Permissions first, else active RBAC grants."""
	codes = sorted(granted_areas(user)[granted_key])
	if codes:
		return codes

	grants = frappe.get_all(
		"DA RBAC Assignment",
		filters={"user": user, "active": 1, "effective_from": ("<=", today())},
		or_filters=[["effective_to", "is", "not set"], ["effective_to", ">=", today()]],
		pluck=grant_field,
	)
	return sorted({g for g in grants if g})


def _own_kebeles(user: str, registry_client) -> list[str]:
	"""The Kebeles the user is posted to: Effective assignments first, else their registry record."""
	if not user or user == "Guest":
		return []

	assigned = frappe.get_all(
		"DA Kebele Assignment",
		filters={"state": "Effective", "user": user},
		pluck="kebele",
	)
	kebeles = sorted({k for k in assigned if k})
	if kebeles:
		return kebeles

	kebeles = set()
	for da_id in _own_da_ids(user):
		try:
			da = registry_client.get_da(da_id)
		except DANotFound:
			continue
		if da.kebele:
			kebeles.add(da.kebele)
	return sorted(kebeles)


def _own_da_ids(user: str) -> list[str]:
	"""The DA-IDs bound to the user: User.da_registry_id and active DA RBAC grants."""
	da_ids = set()
	bound = frappe.db.get_value("User", user, "da_registry_id")
	if bound:
		da_ids.add(bound)

	grants = frappe.get_all(
		"DA RBAC Assignment",
		filters={"user": user, "role": C.ROLE_DA, "active": 1, "effective_from": ("<=", today())},
		or_filters=[["effective_to", "is", "not set"], ["effective_to", ">=", today()]],
		pluck="da_id",
	)
	da_ids.update(d for d in grants if d)
	return sorted(da_ids)


def _da_reference_to_list_dict(da_ref) -> dict:
	"""Convert DAReference to list item dict for API response."""
	return {
		"da_id": da_ref.da_id,
		"full_name": da_ref.full_name,
		"active_status": da_ref.active_status,
		"approval_status": da_ref.approval_status,
		"region": da_ref.region,
		"zone": da_ref.zone,
		"woreda": da_ref.woreda,
		"kebele": da_ref.kebele,
		"education_tier": da_ref.education_tier,
		"fayda_status": da_ref.fayda_status,
		"phone": da_ref.phone,
		"specialisation": da_ref.specialisation,
		"joined_at": da_ref.joined_at,
		"farmer_count": da_ref.farmer_count,
		# Master registry columns: Fayda ID, position, and the posting by name
		"fayda_id": da_ref.fayda_id,
		"position": da_ref.position,
		"region_name": da_ref.region_name,
		"zone_name": da_ref.zone_name,
		"woreda_name": da_ref.woreda_name,
		"kebele_name": da_ref.kebele_name,
	}
