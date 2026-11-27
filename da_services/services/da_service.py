"""Development Agent listing service (DA-226).

DA listing with filters, search, paging and filter facets:
- list_agents / search_agents: every DA the registry returns
- list_my_team / my_team_tree: the signed-in user's team from the DA Hierarchy (Admin: the
  whole tree; a manager: their reports; a DA: their boss's reports)
"""

from __future__ import annotations

import frappe
from frappe import _

from da_services.da_services.doctype.da_hierarchy.da_hierarchy import ROOT
from da_services.integrations.da_registry.client import get_client
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
	"specialisation",
)
# Filters of the My Team list: no active status, education tier or approval status there.
TEAM_FILTER_FIELDS = ("region", "woreda", "kebele", "fayda_status", "specialisation")
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


def _query(
	das,
	filters: dict,
	*,
	search: str | None,
	limit: int,
	start: int,
	fields: tuple[str, ...] = FILTER_FIELDS,
	to_dict=None,
) -> dict:
	"""Filter, search, sort and page a list of DAReference, and count the facets.

	fields: the filters (and facets) this list has; to_dict: how each DA is returned.
	"""
	to_dict = to_dict or _da_reference_to_list_dict
	wanted = {field: split_values(filters.get(field)) for field in fields}

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
	agents = [to_dict(da) for da in paginated_das]

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
	for field in wanted:
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


# -- My team: the DA Hierarchy (who reports to whom) ---------------------------------------

HIERARCHY = "DA Hierarchy"
ADMIN_ROLES = frozenset({C.ROLE_ADMIN, "System Manager"})
# How the team was chosen: the whole tree, the caller's reports, the caller's boss's reports, or nobody.
TEAM_ALL = "all"
TEAM_REPORTS = "reports"
TEAM_PEERS = "peers"
TEAM_SELF = "self"
TEAM_NONE = "none"
# A Supervisor's list: the Development Agents posted to the Supervisor's Woreda(s).
TEAM_WOREDA = "woreda"
# A DA's list: the Development Agents posted to the DA's own Kebele.
TEAM_KEBELE = "kebele"
_NODE_FIELDS = [
	"name",
	"user",
	"full_name",
	"designation",
	"region",
	"zone",
	"woreda",
	"kebele",
	"is_group",
	"lft",
	"rgt",
]


def team_root(user: str) -> tuple[str, dict | None]:
	"""The DA Hierarchy node whose subtree is the user's team, and how it was chosen.

	- Admin (OAN Administrator / System Manager / Administrator): the root, the whole tree
	- someone with people under them: their own node (their reports, at every level)
	- someone with nobody under them (a DA): their boss's node (the boss's reports, them included)
	- a DA with no boss (placed straight under the root): their own node, so just them, never the
	  whole tree
	- no node: none
	"""
	if not user or user == "Guest":
		return TEAM_NONE, None
	if user == "Administrator" or set(frappe.get_roles(user)) & ADMIN_ROLES:
		return TEAM_ALL, frappe.db.get_value(HIERARCHY, ROOT, _NODE_FIELDS, as_dict=True)

	node = frappe.db.get_value(
		HIERARCHY, {"user": user}, [*_NODE_FIELDS, "parent_da_hierarchy"], as_dict=True
	)
	if not node:
		return TEAM_NONE, None
	if node.rgt - node.lft > 1:  # nested set: someone sits under this node
		return TEAM_REPORTS, node
	if not node.parent_da_hierarchy or node.parent_da_hierarchy == ROOT:
		return TEAM_SELF, node
	boss = frappe.db.get_value(HIERARCHY, node.parent_da_hierarchy, _NODE_FIELDS, as_dict=True)
	return TEAM_PEERS, boss


def _team_nodes(root: dict) -> list[dict]:
	"""Every user node under ``root`` (not ``root`` itself), top of the tree first."""
	return frappe.get_all(
		HIERARCHY,
		filters={"lft": (">", root.lft), "rgt": ("<", root.rgt), "user": ("is", "set")},
		fields=[*_NODE_FIELDS, "parent_da_hierarchy"],
		order_by="lft asc",
	)


def _da_ids_of(users: list[str]) -> dict[str, str]:
	"""user -> DA-ID (User.da_registry_id) for the users bound to a DA record."""
	if not users:
		return {}
	rows = frappe.get_all(
		"User",
		filters={"name": ("in", users), "da_registry_id": ("is", "set")},
		fields=["name", "da_registry_id"],
	)
	return {r.name: r.da_registry_id for r in rows}


def list_my_team(
	*,
	search: str | None = None,
	limit: int = 50,
	start: int = 0,
	user: str | None = None,
	**filters,
) -> dict:
	"""List the Development Agents of the signed-in user's team, from the DA Hierarchy.

	The team is the DA Hierarchy subtree under team_root(): the whole tree for an Admin, the
	caller's reports (every level) for someone with people under them, and the caller's boss's
	reports (the caller included) for a DA. Each team member with a DA record
	(User.da_registry_id) is listed with that record from the DA Registry; an Admin gets every
	DA the registry returns.

	Args:
	    search: Search by DA-ID or full name (case-insensitive partial match)
	    limit: Page size (default 50, max 500)
	    start: Offset for pagination (default 0)
	    user: The caller; defaults to the session user
	    **filters: Any of TEAM_FILTER_FIELDS; one value or several (comma-separated)

	Each agent has the _da_reference_to_team_dict fields: no active status, education tier or
	approval status.

	Two roles get an area instead of a subtree:
	- a Supervisor: every Development Agent posted to their Woreda (team "woreda"); the Woreda is
	  the one on their DA Hierarchy node, else their DA Woreda User Permissions, else their own
	  DA record
	- a DA (nobody under them): every Development Agent posted to their own Kebele (team
	  "kebele"), the DA included; the Kebele is the one on the DA's registry record, else the one
	  on their DA Hierarchy node

	Returns:
	    {"team": "all" | "woreda" | "reports" | "kebele" | "none", "root": {node} | None,
	     "areas": [codes], "agents": [...], "total": int, "facets": {...}}
	    areas: the Woreda codes for "woreda", the Kebele code for "kebele", else [].
	    reports_to: the caller's boss in the DA Hierarchy (their parent node), None at the top.

	Example:
	    >>> team = list_my_team(limit=20)
	    >>> print(f"{team['total']} agents under {team['root']['name']}")
	"""
	user = user or frappe.session.user
	registry_client = get_client()

	# Step 1: The node whose subtree is the team
	team, root = team_root(user)

	# Step 2: The team's DAs: every registry DA for an Admin, the Supervisor's Woreda, the DA's
	# Kebele, else the DA records of the subtree's users
	areas: list[str] = []
	if team == TEAM_ALL:
		team_das = registry_client.list_das()
	elif C.ROLE_SUPERVISOR in frappe.get_roles(user):
		root = _own_node(user)
		all_das = registry_client.list_das()
		areas = _own_woredas(user, all_das, root)
		team = TEAM_WOREDA
		team_das = [da for da in all_das if da.woreda in areas]
	elif team in (TEAM_PEERS, TEAM_SELF):
		root = _own_node(user)
		all_das = registry_client.list_das()
		kebele = _own_kebele(user, all_das, root)
		areas = [kebele] if kebele else []
		team = TEAM_KEBELE
		team_das = [da for da in all_das if kebele and da.kebele == kebele]
	elif root:
		members = [root.user] if team == TEAM_SELF else [n.user for n in _team_nodes(root)]
		da_ids = set(_da_ids_of(members).values())
		team_das = [da for da in registry_client.list_das() if da.da_id in da_ids]
	else:
		team_das = []

	# Steps 3-9: filter, search, sort, page, facets
	return {
		"team": team,
		"root": _node_summary(root),
		"areas": areas,
		"reports_to": _node_summary(_boss_of(user)),
		**_query(
			team_das,
			filters,
			search=search,
			limit=limit,
			start=start,
			fields=TEAM_FILTER_FIELDS,
			to_dict=_da_reference_to_team_dict,
		),
	}


def _boss_of(user: str) -> dict | None:
	"""The node the user reports to in the DA Hierarchy; None with no node or straight under the root."""
	parent = frappe.db.get_value(HIERARCHY, {"user": user}, "parent_da_hierarchy")
	if not parent or parent == ROOT:
		return None
	return frappe.db.get_value(HIERARCHY, parent, _NODE_FIELDS, as_dict=True)


def _own_node(user: str) -> dict | None:
	"""The user's own DA Hierarchy node."""
	return frappe.db.get_value(HIERARCHY, {"user": user}, _NODE_FIELDS, as_dict=True)


def _own_woredas(user: str, das: list, node: dict | None) -> list[str]:
	"""A Supervisor's Woredas: their DA Hierarchy node, else their DA Woreda User Permissions,
	else their own DA record."""
	if node and node.woreda:
		return [node.woreda]
	granted = sorted(granted_areas(user)["woredas"])
	if granted:
		return granted
	da_id = _da_ids_of([user]).get(user)
	record = next((da for da in das if da.da_id == da_id), None) if da_id else None
	return [record.woreda] if record and record.woreda else []


def _own_kebele(user: str, das: list, node: dict | None) -> str | None:
	"""The Kebele a DA is posted to: from their DA record, else from their DA Hierarchy node."""
	da_id = _da_ids_of([user]).get(user)
	record = next((da for da in das if da.da_id == da_id), None) if da_id else None
	return (record and record.kebele) or (node and node.kebele) or None


def my_team_tree(user: str | None = None) -> dict:
	"""The signed-in user's team as a tree: team_root() and everyone under it, by reporting line.

	Each node: {name, user, full_name, designation, region, zone, woreda, kebele, is_group,
	da_id, reports: [...]}; da_id is the user's DA-ID when they have one.

	Returns:
	    {"team": "all" | "reports" | "peers" | "self" | "none", "tree": {node} | None}
	"""
	user = user or frappe.session.user

	# Step 1: The node whose subtree is the team
	team, root = team_root(user)
	if not root:
		return {"team": team, "tree": None}

	# Step 2: Its subtree, top first, and the DA-IDs of its users
	nodes = _team_nodes(root)
	da_ids = _da_ids_of([n.user for n in nodes] + ([root.user] if root.user else []))

	# Step 3: Hang every node under its parent
	tree = {**_node_summary(root), "da_id": da_ids.get(root.user), "reports": []}
	by_name = {root.name: tree}
	for n in nodes:  # lft order: a parent always comes before its children
		item = {**_node_summary(n), "da_id": da_ids.get(n.user), "reports": []}
		by_name[n.name] = item
		parent = by_name.get(n.parent_da_hierarchy)
		if parent is not None:
			parent["reports"].append(item)
	return {"team": team, "tree": tree}


def _node_summary(node: dict | None) -> dict | None:
	if not node:
		return None
	return {
		"name": node.name,
		"user": node.user,
		"full_name": node.full_name,
		"designation": node.designation,
		"region": node.region,
		"zone": node.zone,
		"woreda": node.woreda,
		"kebele": node.kebele,
		"is_group": bool(node.is_group),
	}


def _da_reference_to_list_dict(da_ref) -> dict:
	"""Convert DAReference to list item dict for API response (GET /agents)."""
	return {
		**_da_reference_to_team_dict(da_ref),
		"active_status": da_ref.active_status,
		"approval_status": da_ref.approval_status,
		"education_tier": da_ref.education_tier,
	}


def _da_reference_to_team_dict(da_ref) -> dict:
	"""A DAReference as a My Team list item: no active status, education tier or approval status."""
	return {
		"da_id": da_ref.da_id,
		"full_name": da_ref.full_name,
		"region": da_ref.region,
		"zone": da_ref.zone,
		"woreda": da_ref.woreda,
		"kebele": da_ref.kebele,
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
