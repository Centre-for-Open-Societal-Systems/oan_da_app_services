"""Farmer profile and listing services (DA-40, DA-39).

Business logic for farmer profile display, combining Farmer Registry data
with DA Visit history, pending actions (DA-39), and permission checks.
"""

import json

import frappe
from frappe import _

from da_services.da_services.doctype.da_administrative_area.da_administrative_area import ancestors_of
from da_services.da_services.doctype.da_visit.da_visit import OPEN_STATES
from da_services.integrations.farmer_registry import get_farmer_registry_client, intake
from da_services.integrations.farmer_registry.client import FarmerRegistryClientError, FarmerRegistryError
from da_services.integrations.farmer_registry.mapper import (
	cache_registration_state,
	change_request_to_dict,
	da_form_to_ingest_payload,
	farmer_to_reference_dict,
	list_item_to_dict,
	reference_dict_to_profile,
	registration_message_id,
	registration_to_dict,
)
from da_services.services import constants as C
from da_services.services.pending_actions import derive_pending_actions_batch
from da_services.utils.permissions import ScopeContext


def get_farmer_profile(farmer_id: str, ctx: ScopeContext) -> dict:
	"""Get complete farmer profile for DA-40 profile screen.

	Combines:
	- Farmer Registry data (cached in DA Farmer Reference)
	- DA Visit history (last visit, pending actions)
	- Edit permissions (for DA-41)

	Args:
	    farmer_id: Farmer Registry internal_record_id
	    ctx: Scope context with user permissions

	Returns:
	    Complete profile dict for frontend

	Raises:
	    DoesNotExistError: Farmer not found
	    PermissionError: Farmer not in scope
	"""
	ref = _require_farmer_in_scope(farmer_id, ctx)

	# Get visit data (last visit, pending tasks)
	visit_data = _get_farmer_visit_data(farmer_id)

	# Check if farmer is editable by current DA
	can_edit = _can_edit_farmer(farmer_id, ctx)

	# Merge all data into profile
	profile = reference_dict_to_profile(ref, visit_data)
	# Sections the registry could not deliver on a fresh fetch (empty on cache hits
	# and complete reads); the UI can show "partial data" instead of blanks.
	profile["incomplete_sections"] = list(ref.get("incomplete_sections") or [])
	profile["can_edit"] = can_edit
	da_ids_list = list(ctx.da_ids) if ctx.da_ids else []
	profile["is_linked"] = _is_farmer_linked_to_da(farmer_id, da_ids_list)

	return profile


def _require_farmer_in_scope(farmer_id: str, ctx: ScopeContext) -> dict:
	"""The cached farmer reference, after the 404 / 403 checks every farmer read shares.

	Raises:
	    DoesNotExistError: Farmer not found
	    PermissionError: Farmer not in scope
	"""
	# Get or sync farmer reference from cache
	ref = _get_or_sync_farmer_reference(farmer_id)
	if not ref:
		frappe.throw(_("Farmer {0} not found.").format(farmer_id), frappe.DoesNotExistError)

	# Check scope: Different rules for DA vs Supervisor/Executive
	if not ctx.unrestricted:
		if ctx.has_any_role(C.ROLE_DA):
			# DAs can ONLY access linked farmers
			da_ids_list = list(ctx.da_ids) if ctx.da_ids else []
			if not _is_farmer_linked_to_da(farmer_id, da_ids_list):
				frappe.throw(_("Farmer is not linked or assigned to you."), frappe.PermissionError)
		else:
			# Supervisors/Executives check region/woreda scope
			farmer_region = ref.get("region")
			farmer_woreda = ref.get("woreda")
			farmer_zone = ref.get("zone")

			covered, reason = ctx.scope_decision(region=farmer_region, woreda=farmer_woreda, zone=farmer_zone)

			if not covered:
				from da_services.utils.permissions import SCOPE_UNRESOLVED

				if reason == SCOPE_UNRESOLVED:
					frappe.throw(
						_("Cannot verify farmer scope: missing Woreda or Zone data."),
						frappe.PermissionError,
					)
				else:
					frappe.throw(_("Farmer is not in your scope."), frappe.PermissionError)

	return ref


def resolve_farmer_id(farmer_id: str) -> str:
	"""Accept the registry's internal id or the Farmer-ID shown in the UI (FR-…)."""
	if frappe.db.exists("DA Farmer Reference", farmer_id):
		return farmer_id
	cached = frappe.db.get_value("DA Farmer Reference", {"reg_id": farmer_id}, "name")
	if cached:
		return cached
	finder = getattr(get_farmer_registry_client(), "find_internal_id", None)
	found = finder(farmer_id) if finder else None
	return found or farmer_id


def get_farmer_change_requests(
	farmer_id: str, ctx: ScopeContext, *, status: str | None = None, tab: str | None = None
) -> dict:
	"""Change requests the registry holds for a farmer (DA-272).

	The farmer must be readable by the caller (same 404 / 403 rules as the
	profile). ``status`` keeps one approval_status; ``tab`` one register tab.
	"""
	internal_id = resolve_farmer_id(farmer_id)
	_require_farmer_in_scope(internal_id, ctx)
	rows = get_farmer_registry_client().get_change_requests(internal_id, tab=tab, status=status)
	return {
		"farmer_id": farmer_id,
		"change_requests": [change_request_to_dict(r) for r in rows],
		"total": len(rows),
	}


def search_farmers(
	ctx: ScopeContext,
	*,
	search_text: str = "",
	woreda: str | None = None,
	kebele: str | None = None,
	state: str | None = None,
	page: int = 1,
	page_size: int = 50,
) -> dict:
	"""Search the live Farmer register within the caller's scope (DA-272).

	Unlike list_farmers this does not read the DA Farmer Reference cache: the
	registry is asked directly with search_text, so a farmer registered
	yesterday is found. Scope is applied as a registry filter, so a restricted
	caller always searches inside one Woreda:

	- DA: their assigned Kebeles' Woreda, narrowed to those Kebeles
	- Supervisor with one Woreda: that Woreda
	- Supervisor with several Woredas, Zone or Region officers: ``woreda`` is required
	- Admin: whatever filters were asked for
	"""
	filter_by: dict = {}
	keep_kebeles: set[str] | None = None

	if ctx.unrestricted:
		filter_by.update({"woreda": woreda, "kebele": kebele})
	elif ctx.has_any_role(C.ROLE_DA):
		assigned = _get_da_assigned_kebeles(list(ctx.da_ids) if ctx.da_ids else [])
		if not assigned:
			return _empty_search_page(page, page_size)
		if kebele:
			if kebele not in assigned:
				return _empty_search_page(page, page_size)
			assigned = [kebele]
		scope_woreda = woreda or (next(iter(ctx.woreda_scopes)) if len(ctx.woreda_scopes) == 1 else None)
		if scope_woreda and ctx.woreda_scopes and scope_woreda not in ctx.woreda_scopes:
			frappe.throw(_("Woreda {0} is not in your scope.").format(scope_woreda), frappe.PermissionError)
		filter_by["woreda"] = scope_woreda
		if len(assigned) == 1:
			filter_by["kebele"] = assigned[0]
		else:
			keep_kebeles = set(assigned) | set(_area_names(assigned))
	else:
		if woreda:
			_require_woreda_in_scope(ctx, woreda)
		elif len(ctx.woreda_scopes) == 1:
			woreda = next(iter(ctx.woreda_scopes))
		else:
			frappe.throw(
				_("Pass a woreda to search in: your scope covers more than one Woreda."),
				frappe.ValidationError,
			)
		filter_by.update({"woreda": woreda, "kebele": kebele})
	if state:
		filter_by["state"] = state

	result = get_farmer_registry_client().search_farmers(
		search_text=search_text, filter_by=filter_by, page=page, page_size=page_size
	)
	items = result.get("items") or []
	total = int(result.get("total") or 0)
	pages = int(result.get("number_of_pages") or 0)
	narrowed = bool(result.get("filtered_client_side"))
	if keep_kebeles is not None and any(i.kebele for i in items):
		kept = [i for i in items if not i.kebele or i.kebele in keep_kebeles]
		if len(kept) != len(items):
			narrowed = True
			# One page is the whole result, so its remainder is the true count.
			if pages <= 1:
				total, pages = len(kept), (1 if kept else 0)
		items = kept
	return {
		"farmers": [list_item_to_dict(i) for i in items],
		"total": total,
		"page": int(result.get("page") or page),
		"page_size": int(result.get("page_size") or page_size),
		"number_of_pages": pages,
		"filtered_client_side": narrowed,
	}


def _empty_search_page(page: int, page_size: int) -> dict:
	return {
		"farmers": [],
		"total": 0,
		"page": page,
		"page_size": page_size,
		"number_of_pages": 0,
		"filtered_client_side": False,
	}


def _area_names(codes: list) -> list[str]:
	"""English names of DA Administrative Areas, for matching the registry's *_name values."""
	if not codes:
		return []
	rows = frappe.get_all("DA Administrative Area", filters={"name": ("in", list(codes))}, fields=["name_en"])
	return [r.name_en for r in rows if r.name_en]


def _require_woreda_in_scope(ctx: ScopeContext, woreda: str) -> None:
	"""A Woreda a Supervisor / Zone / Region officer may search in; raises otherwise."""
	if ctx.woreda_scopes:
		if woreda not in ctx.woreda_scopes:
			frappe.throw(_("Woreda {0} is not in your scope.").format(woreda), frappe.PermissionError)
		return
	if ctx.zone_scopes:
		woreda_zone = ancestors_of(woreda).get("zone")
		if not woreda_zone:
			frappe.throw(
				_(
					"Woreda {0} is not in the administrative master data (no Zone known); "
					"ask an Administrator to load it before filtering by it."
				).format(woreda),
				frappe.ValidationError,
			)
		if woreda_zone not in ctx.zone_scopes:
			frappe.throw(_("Woreda {0} is not in your Zone scope.").format(woreda), frappe.PermissionError)
		return
	if ctx.region_scopes:
		woreda_region = ancestors_of(woreda).get("region") or (
			woreda.split("-")[0] if "-" in woreda else None
		)
		if woreda_region and woreda_region not in ctx.region_scopes:
			frappe.throw(_("Woreda {0} is not in your Region scope.").format(woreda), frappe.PermissionError)
		return
	frappe.throw(_("You do not have scope to search by Woreda."), frappe.PermissionError)


def list_farmers(
	ctx: ScopeContext,
	*,
	woreda: str | None = None,
	kebele: str | None = None,
	state: str | None = None,
	da_id: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list, int]:
	"""List farmers visible to the user (DA-40 farmers list, DA-211 Kebele scoping).

	Scope enforcement (DA-211 updated):
	- DA: All farmers in assigned Kebele (not just linked farmers)
	- Supervisor: All farmers in their Woreda
	- Executive: All farmers in their Region (read-only)
	- Admin: All farmers

	Args:
	    ctx: Scope context
	    woreda: Filter by woreda
	    kebele: Filter by kebele
	    state: Filter by registration state
	    da_id: Filter by linked DA
	    limit: Page size
	    start: Offset

	Returns:
	    (farmers list, total count)
	"""
	filters = {}

	# Scope filters
	# - DA: all farmers in the assigned Kebele(s), not just manually linked ones (DA-211).
	# - Supervisor: their Woreda(s); the permission_query_conditions hook applies this.
	# - Zone-scoped officers: the Woredas under their Zone, via the zone column on
	#   DA Farmer Reference (Zone scope) and ancestors_of() for the woreda filter.
	# - Executive (Region): their Region, read-only.
	if not ctx.unrestricted and ctx.has_any_role(C.ROLE_DA):
		# DA sees all farmers in assigned Kebele (DA-211 change)
		da_ids_list = list(ctx.da_ids) if ctx.da_ids else []
		assigned_kebeles = _get_da_assigned_kebeles(da_ids_list)
		if not assigned_kebeles:
			# DA has no Kebele assignment - empty list
			return [], 0
		filters["kebele"] = ("in", assigned_kebeles)
		# Live registry: fill the cache for Kebeles we have never synced.
		_ensure_registry_cache(kebeles=assigned_kebeles)

	# User filters - must not override scope
	# Woreda filter can only narrow within the scope, not expand it
	if woreda:
		if not ctx.unrestricted:
			# Validate that requested woreda is within scope
			if ctx.has_any_role(C.ROLE_DA):
				# DA scope is linked farmers, woreda filter is additional narrowing
				filters["woreda"] = woreda
			elif ctx.has_any_role(C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE):
				# Check if the woreda is in the user's scope
				if ctx.woreda_scopes:
					# Woreda-scoped: must be in their woredas
					if woreda not in ctx.woreda_scopes:
						frappe.throw(
							_("Woreda {0} is not in your scope.").format(woreda), frappe.PermissionError
						)
					filters["woreda"] = woreda
				elif ctx.zone_scopes:
					# Zone-scoped: the Woreda must sit under one of their Zones in master data.
					# An unknown Woreda is a data problem, not a scope problem; say so.
					woreda_zone = ancestors_of(woreda).get("zone")
					if not woreda_zone:
						frappe.throw(
							_(
								"Woreda {0} is not in the administrative master data (no Zone known); "
								"ask an Administrator to load it before filtering by it."
							).format(woreda),
							frappe.ValidationError,
						)
					if woreda_zone not in ctx.zone_scopes:
						frappe.throw(
							_("Woreda {0} is not in your Zone scope.").format(woreda), frappe.PermissionError
						)
					filters["woreda"] = woreda
				elif ctx.region_scopes:
					# Region-scoped: woreda must be in their region
					# Simplified check: assume woreda code starts with region code
					woreda_region = woreda.split("-")[0] if "-" in woreda else None
					if woreda_region and woreda_region not in ctx.region_scopes:
						frappe.throw(
							_("Woreda {0} is not in your Region scope.").format(woreda),
							frappe.PermissionError,
						)
					filters["woreda"] = woreda
				else:
					# No scope defined, deny
					frappe.throw(_("You do not have scope to filter by Woreda."), frappe.PermissionError)
			else:
				# Other roles: apply filter directly
				filters["woreda"] = woreda
		else:
			filters["woreda"] = woreda

	if kebele:
		# A requested Kebele may only narrow the caller's scope.
		# Replacing the DA's assigned-Kebele filter would let them read another Kebele.
		assigned = filters.get("kebele")
		if (
			not ctx.unrestricted
			and ctx.has_any_role(C.ROLE_DA)
			and isinstance(assigned, tuple)
			and kebele not in assigned[1]
		):
			return [], 0
		filters["kebele"] = kebele
	if state:
		filters["registration_state"] = state

	# If filtering by DA, apply linked farmers
	if da_id:
		linked_to_da = frappe.get_all(
			"DA Farmer Link",
			filters={"da_id": da_id, "is_active": 1},
			pluck="farmer_id",
		)
		if linked_to_da:
			# Intersect with existing farmer_id filter if present
			if "farmer_id" in filters:
				existing = (
					filters["farmer_id"][1]
					if isinstance(filters["farmer_id"], tuple)
					else [filters["farmer_id"]]
				)
				filters["farmer_id"] = ("in", list(set(existing) & set(linked_to_da)))
			else:
				filters["farmer_id"] = ("in", linked_to_da)
		else:
			return [], 0

	# Query DA Farmer Reference using get_list to apply permission hooks
	# This ensures Zone-scoped officers see only their Zone and multi-Woreda
	# Supervisors see all their Woredas
	farmers = frappe.get_list(
		"DA Farmer Reference",
		filters=filters,
		fields=[
			"farmer_id",
			"reg_id",
			"farmer_name",
			"phone",
			"kebele",
			"woreda",
			"region",
			"total_land_hectares",
			"registration_state",
			"registered_at",
		],
		order_by="farmer_name asc",
		limit_page_length=limit,
		limit_start=start,
	)

	# Count with the same filters using get_list to apply permission hooks
	# frappe.db.count() bypasses permission hooks, so we use get_list with pluck
	total_ids = frappe.get_list("DA Farmer Reference", filters=filters, pluck="name")
	total = len(total_ids)

	# Enrich with visit counts and pending actions (DA-39)
	if farmers:
		# Batch operations for all farmers (optimized - no N+1)
		farmer_ids = [f["farmer_id"] for f in farmers]
		pending_actions = derive_pending_actions_batch(farmer_ids)
		visit_counts = _get_farmer_visit_counts_batch(farmer_ids)
		last_visits = _get_last_visit_dates_batch(farmer_ids)
		assigned_specs = _get_farmer_assigned_specializations_batch(farmer_ids)  # DA-211 Phase 4

		# Add visit counts and pending actions to each farmer
		for farmer in farmers:
			farmer_id = farmer["farmer_id"]

			# Visit history (batched)
			farmer["visit_count"] = visit_counts.get(farmer_id, 0)
			farmer["last_visit"] = last_visits.get(farmer_id)

			# Pending action state (DA-39)
			pending_action = pending_actions.get(farmer_id)
			if pending_action:
				farmer["pending_action"] = pending_action.state
				farmer["pending_action_priority"] = pending_action.priority
				if pending_action.reason:
					farmer["pending_action_reason"] = pending_action.reason
			else:
				# Fallback if derivation failed
				farmer["pending_action"] = "No action due"
				farmer["pending_action_priority"] = "low"

			# DA-211 Phase 4: Assignment flags (which specializations have active visits)
			farmer["assigned_specializations"] = assigned_specs.get(farmer_id, [])

	return farmers, total


def check_farmer_edit_permissions(farmer_id: str, ctx: ScopeContext) -> dict:
	"""Check if user can edit this farmer (DA-41 permission check).

	Args:
	    farmer_id: Farmer Registry internal_record_id
	    ctx: Scope context

	Returns:
	    {
	        "can_edit": bool,
	        "reason": str (if can_edit is False),
	        "is_linked": bool,
	        "link_type": str (Automatic/Manual)
	    }
	"""
	can_edit = _can_edit_farmer(farmer_id, ctx)
	da_ids_list = list(ctx.da_ids) if ctx.da_ids else []
	is_linked = _is_farmer_linked_to_da(farmer_id, da_ids_list)

	link = None
	if da_ids_list:
		link = frappe.db.get_value(
			"DA Farmer Link",
			{"farmer_id": farmer_id, "da_id": ("in", da_ids_list), "is_active": 1},
			["link_type"],
			as_dict=False,
		)

	reason = None
	if not can_edit:
		if not is_linked:
			reason = "Farmer is not linked or assigned to you"
		else:
			reason = "You do not have permission to edit this farmer"

	return {
		"can_edit": can_edit,
		"reason": reason,
		"is_linked": is_linked,
		"link_type": link if is_linked else None,
	}


def _get_or_sync_farmer_reference(farmer_id: str) -> dict | None:
	"""Get farmer from cache, or fetch and cache from registry.

	Args:
	    farmer_id: Farmer Registry internal_record_id

	Returns:
	    Farmer reference dict or None if not found
	"""
	# Check cache first
	cached = frappe.db.get_value(
		"DA Farmer Reference",
		farmer_id,
		"*",
		as_dict=True,
	)

	if cached:
		return cached

	# Not in cache, fetch from registry
	client = get_farmer_registry_client()
	farmer = client.get_farmer(farmer_id)

	if not farmer:
		return None

	incomplete = list(getattr(farmer, "incomplete_sections", None) or [])
	if incomplete:
		frappe.logger("da_services").warning(
			"Farmer %s cached with incomplete sections %s (registry tab fetch failed)", farmer_id, incomplete
		)

	# Cache it. No explicit commit: the row lands with the request's transaction, so a
	# later failure in the same request does not leave a half-written cache behind.
	ref_dict = farmer_to_reference_dict(farmer)
	ref_dict["last_synced_at"] = frappe.utils.now_datetime()
	ref_dict["sync_version"] = 1

	doc = frappe.get_doc(
		{
			"doctype": "DA Farmer Reference",
			**ref_dict,
		}
	)
	try:
		doc.insert(ignore_permissions=True)
		out = doc.as_dict()
	except frappe.DuplicateEntryError:
		# Another worker cached the same farmer between our lookup and insert: use its row.
		out = frappe.db.get_value("DA Farmer Reference", farmer_id, "*", as_dict=True)

	out["incomplete_sections"] = incomplete
	return out


def _get_farmer_visit_data(farmer_id: str) -> dict:
	"""Get visit-related data for a farmer.

	Returns:
	    {
	        "last_visit_date": datetime,
	        "last_visit_outcome": str,
	        "pending_task_count": int,
	        "total_visits": int,
	    }
	"""
	# Get last completed visit
	last_visit = frappe.db.get_value(
		"DA Visit",
		{
			"farmer_id": farmer_id,
			"physical_status": "Physical Visit Complete",
		},
		["completed_at", "outcome"],
		as_dict=True,
		order_by="completed_at desc",
	)

	# Get pending tasks
	pending_tasks = frappe.db.count(
		"DA Task",
		{
			"farmer_id": farmer_id,
			"status": "Open",
		},
	)

	# Get total visits
	total_visits = frappe.db.count(
		"DA Visit",
		{"farmer_id": farmer_id},
	)

	return {
		"last_visit_date": last_visit.completed_at if last_visit else None,
		"last_visit_outcome": last_visit.outcome if last_visit else None,
		"pending_task_count": pending_tasks,
		"total_visits": total_visits,
	}


def _get_farmer_visit_count(farmer_id: str) -> int:
	"""Get total visit count for a farmer."""
	return frappe.db.count("DA Visit", {"farmer_id": farmer_id})


def _get_last_visit_date(farmer_id: str) -> str | None:
	"""Get last visit date for a farmer."""
	return frappe.db.get_value(
		"DA Visit",
		{"farmer_id": farmer_id},
		"scheduled_at",
		order_by="scheduled_at desc",
	)


def _get_farmer_visit_counts_batch(farmer_ids: list[str]) -> dict[str, int]:
	"""Get visit counts for multiple farmers (batch query).

	Args:
	    farmer_ids: List of farmer IDs

	Returns:
	    Dict mapping farmer_id -> visit count
	"""
	if not farmer_ids:
		return {}

	# Single query with GROUP BY
	counts = frappe.db.sql(
		"""
		SELECT farmer_id, COUNT(*) as count
		FROM `tabDA Visit`
		WHERE farmer_id IN %(farmer_ids)s
		GROUP BY farmer_id
		""",
		{"farmer_ids": farmer_ids},
		as_dict=True,
	)

	return {row["farmer_id"]: row["count"] for row in counts}


def _get_last_visit_dates_batch(farmer_ids: list[str]) -> dict[str, str | None]:
	"""Get last visit dates for multiple farmers (batch query).

	Args:
	    farmer_ids: List of farmer IDs

	Returns:
	    Dict mapping farmer_id -> last visit scheduled_at
	"""
	if not farmer_ids:
		return {}

	# Single query to get most recent visit per farmer
	# Using subquery to get max(scheduled_at) per farmer
	visits = frappe.db.sql(
		"""
		SELECT v.farmer_id, v.scheduled_at
		FROM `tabDA Visit` v
		INNER JOIN (
			SELECT farmer_id, MAX(scheduled_at) as max_scheduled
			FROM `tabDA Visit`
			WHERE farmer_id IN %(farmer_ids)s
			GROUP BY farmer_id
		) latest ON v.farmer_id = latest.farmer_id
		          AND v.scheduled_at = latest.max_scheduled
		WHERE v.farmer_id IN %(farmer_ids)s
		""",
		{"farmer_ids": farmer_ids},
		as_dict=True,
	)

	return {row["farmer_id"]: row["scheduled_at"] for row in visits}


def _can_edit_farmer(farmer_id: str, ctx: ScopeContext) -> bool:
	"""Check if user can edit this farmer (DA-41 rule, DA-211 updated).

	DA-41 rule: Only farmers linked or assigned to the DA are editable.
	DA-211 update: "Assigned" means farmer is in the DA's assigned Kebele.

	Args:
	    farmer_id: Farmer ID
	    ctx: Scope context

	Returns:
	    True if editable
	"""
	if ctx.unrestricted:
		return True

	# Only DAs and Supervisors can edit
	if not ctx.has_any_role(C.ROLE_DA, C.ROLE_SUPERVISOR):
		return False

	# For DA role: Must be in assigned Kebele (DA-211 Kebele-based scoping)
	if ctx.has_any_role(C.ROLE_DA):
		return _is_farmer_in_da_kebele(farmer_id, ctx.da_ids)

	# For Supervisor: Can edit any farmer in their woreda
	if ctx.has_any_role(C.ROLE_SUPERVISOR):
		woreda_scope = next(iter(ctx.woreda_scopes), None) if ctx.woreda_scopes else None
		if woreda_scope:
			farmer_woreda = frappe.db.get_value("DA Farmer Reference", farmer_id, "woreda")
			return farmer_woreda == woreda_scope

	return False


def _is_farmer_linked_to_da(farmer_id: str, da_ids: list) -> bool:
	"""Check if farmer is linked to any of the given DA IDs.

	DEPRECATED: For Manual mode assignments only.
	For Kebele-based scoping (DA-211), use _is_farmer_in_da_kebele instead.
	"""
	if not da_ids:
		return False

	return frappe.db.exists(
		"DA Farmer Link",
		{
			"farmer_id": farmer_id,
			"da_id": ("in", da_ids),
			"is_active": 1,
		},
	)


def _is_farmer_in_da_kebele(farmer_id: str, da_ids: list) -> bool:
	"""Check if farmer is in any Kebele assigned to the given DA IDs (DA-211).

	Args:
		farmer_id: Farmer identifier
		da_ids: List of DA identifiers

	Returns:
		True if farmer's Kebele matches any DA's assigned Kebele
	"""
	if not da_ids:
		return False

	# Get farmer's Kebele
	farmer_kebele = frappe.db.get_value("DA Farmer Reference", farmer_id, "kebele")
	if not farmer_kebele:
		return False

	# Get DA's assigned Kebeles
	assigned_kebeles = _get_da_assigned_kebeles(list(da_ids))

	return farmer_kebele in assigned_kebeles


def _ensure_registry_cache(*, kebeles: list) -> None:
	"""Fill DA Farmer Reference from the live registry for Kebeles with no cached farmers.

	Only runs on the http backend; the mock backend ships its own fixtures. A
	registry failure is logged, never raised: the list falls back to whatever is
	cached.
	"""
	if frappe.conf.get("farmer_registry_backend") != "http" or not kebeles:
		return
	missing = [k for k in kebeles if not frappe.db.exists("DA Farmer Reference", {"kebele": k})]
	if not missing:
		return
	# The sync runs inside the caller's transaction behind a savepoint: a registry
	# failure half-way rolls back only the partial cache rows, never the request.
	frappe.db.savepoint("farmer_registry_cache")
	try:
		sync_farmer_references(kebeles=missing)
	except Exception:
		frappe.db.rollback(save_point="farmer_registry_cache")
		frappe.log_error(title="Farmer Registry sync failed", message=frappe.get_traceback())


def sync_farmer_references(
	*,
	kebeles: list | None = None,
	woreda: str | None = None,
	page_size: int = 200,
	max_pages: int = 25,
	commit: bool = False,
) -> int:
	"""Pull farmers from the registry into the DA Farmer Reference cache.

	Searches the Farmer register per Kebele (or per Woreda when no Kebele is
	given) and upserts one cache row per farmer. The Kebele code we searched
	with is stored on the row so the DA's Kebele-scoped list matches it.

	Runs in the caller's transaction. Pass ``commit=True`` only from a scheduler
	or bench job that owns its transaction; a request must never commit
	half-way. Two workers syncing the same Kebele at once are safe: the upsert
	tolerates the duplicate-key race and the last writer wins per row.

	Returns the number of rows upserted.
	"""
	client = get_farmer_registry_client()
	log = frappe.logger("da_services")
	count = 0
	for kebele in kebeles or [None]:
		offset = 0
		pages = 0
		while True:
			items, total = client.list_farmers(woreda=woreda, kebele=kebele, limit=page_size, offset=offset)
			for item in items:
				_upsert_farmer_reference(item, kebele_code=kebele)
				count += 1
			offset += page_size
			pages += 1
			if not items or offset >= total or pages >= max_pages:
				break
		log.info(
			"Farmer Registry sync: %s %s -> %s page(s), %s row(s) so far (registry total %s)",
			"kebele" if kebele else "woreda",
			kebele or woreda or "*",
			pages,
			count,
			total,
		)
		if pages >= max_pages and offset < (total or 0):
			log.warning(
				"Farmer Registry sync: stopped after %s pages for %s; %s of %s rows cached",
				max_pages,
				kebele or woreda or "*",
				count,
				total,
			)
	if commit:
		frappe.db.commit()
	return count


def _upsert_farmer_reference(item, *, kebele_code: str | None = None) -> None:
	"""Write a FarmerListItem into the cache (insert or refresh).

	Insert-first: if another worker inserted the same farmer between our
	existence check and the insert, the duplicate-key error is caught and the
	row is refreshed instead, so concurrent syncs never fail or double-insert.
	"""
	values = {
		"reg_id": item.reg_id,
		"farmer_name": item.name,
		"phone": item.phone,
		"region": item.region,
		"woreda": item.woreda,
		"kebele": kebele_code or item.kebele,
		"total_land_hectares": item.total_land_area,
		"registration_state": cache_registration_state(item.state),
		"registered_at": item.registration_date,
		"last_synced_at": frappe.utils.now_datetime(),
	}
	if not frappe.db.exists("DA Farmer Reference", item.farmer_id):
		try:
			frappe.get_doc({"doctype": "DA Farmer Reference", "farmer_id": item.farmer_id, **values}).insert(
				ignore_permissions=True
			)
			return
		except frappe.DuplicateEntryError:
			pass  # lost the race; fall through and refresh the existing row
	frappe.db.set_value("DA Farmer Reference", item.farmer_id, values, update_modified=False)


def _get_linked_farmer_ids(da_ids: list) -> list:
	"""Get all farmer IDs linked to the given DA IDs.

	DEPRECATED: This is used for Manual mode assignments only.
	For Kebele-based scoping (DA-211), use _get_da_assigned_kebeles instead.
	"""
	if not da_ids:
		return []

	return frappe.get_all(
		"DA Farmer Link",
		filters={"da_id": ("in", da_ids), "is_active": 1},
		pluck="farmer_id",
	)


def _get_farmer_assigned_specializations_batch(farmer_ids: list[str]) -> dict[str, list[str]]:
	"""Get specializations with active visits for each farmer (DA-211 Phase 4).

	Args:
		farmer_ids: List of farmer identifiers

	Returns:
		Dict mapping farmer_id to list of specializations with active visits
	"""
	if not farmer_ids:
		return {}

	# Only open visits mark a specialization as assigned. A completed visit does not.
	active_statuses = list(OPEN_STATES)

	# Query active visits and their specializations
	results = frappe.db.sql(
		"""
		SELECT
			v.farmer_id,
			vs.specialization
		FROM `tabDA Visit` v
		INNER JOIN `tabDA Visit Specialization` vs ON vs.parent = v.name
		WHERE
			v.farmer_id IN %(farmer_ids)s
			AND v.physical_status IN %(statuses)s
		""",
		{"farmer_ids": farmer_ids, "statuses": active_statuses},
		as_dict=True,
	)

	# Group by farmer_id and deduplicate specializations
	farmer_specs = {}
	for row in results:
		farmer_id = row["farmer_id"]
		spec = row["specialization"]

		if farmer_id not in farmer_specs:
			farmer_specs[farmer_id] = set()

		if spec:
			farmer_specs[farmer_id].add(spec)

	# Convert sets to sorted lists
	return {fid: sorted(list(specs)) for fid, specs in farmer_specs.items()}


def _get_da_assigned_kebeles(da_ids: list) -> list:
	"""Get Kebeles assigned to the given DA IDs (DA-211 Kebele-based scoping).

	Args:
		da_ids: List of DA identifiers

	Returns:
		List of Kebele codes assigned to these DAs
	"""
	if not da_ids:
		return []

	kebeles = frappe.get_all(
		"DA Kebele Assignment",
		filters={"da_id": ("in", da_ids), "state": "Effective"},
		pluck="kebele",
	)

	return kebeles


def _record_registration_failure(event, message: str) -> None:
	event.db_set("status", "Failed", update_modified=False)
	event.db_set("last_error", (message or "Farmer registration failed")[:500], update_modified=False)
	frappe.db.commit()


REGISTER_MODES = ("ingest", "intake")


def _register_mode() -> str:
	mode = str(frappe.conf.get("farmer_registry_register_mode") or "ingest").strip().lower()
	if mode not in REGISTER_MODES:
		frappe.throw(
			_("farmer_registry_register_mode must be one of: {0}").format(", ".join(REGISTER_MODES)),
			frappe.ValidationError,
		)
	return mode


def register_farmer(form_data: dict, ctx: ScopeContext) -> dict:
	"""Register a new farmer in the Farmer Registry.

	Two paths, chosen by site config ``farmer_registry_register_mode``:

	- "ingest" (default): one ingest-data call, processed asynchronously; the
	  result carries a correlation_id.
	- "intake": the intake form path the staff portal uses (see
	  integrations/farmer_registry/intake.py): each form section is saved into
	  one draft, which is then finalized for approval; the result carries the
	  registry submission_id.

	Either way the attempt is recorded as a DA Integration Event, and the
	farmer appears in the registry after Woreda approval.

	Args:
	    form_data: validated DA registration form (FarmerRegistrationForm dump).
	    ctx: Scope context of the submitting user (for audit attribution).

	Returns:
	    {
	        "success": bool,
	        "message_id": str,
	        "correlation_id": str | None,   # ingest
	        "submission_id": str | None,    # intake
	        "status": str,
	        "event_id": str,
	        "error_code": str | None,
	        "error_message": str | None,
	        "duplicate": bool,
	    }
	"""
	if not isinstance(form_data, dict) or not form_data.get("first_name"):
		frappe.throw(_("Farmer first name is required."), frappe.ValidationError)

	mode = _register_mode()
	client = get_farmer_registry_client()

	if mode == "intake":
		form_data = {"enumerator_name": frappe.utils.get_fullname(frappe.session.user), **form_data}
		# Mapping needs the registry's form layout, code lists and places, so a
		# bad form is rejected here, before anything is recorded or sent.
		try:
			sections = intake.build_plan(client, form_data)
		except intake.IntakeFormError as exc:
			frappe.throw(
				_("Invalid registration form: {0}").format("; ".join(exc.problems)), frappe.ValidationError
			)
		message_id = registration_message_id(form_data)
		payload = {"mode": "intake", "message_id": message_id, "form": form_data, "sections": sections}
	else:
		payload = da_form_to_ingest_payload(form_data)
		message_id = payload["header"]["message_id"]

	# Idempotency: with a client_op_id the message_id is deterministic, so a
	# retried submission finds its earlier event instead of registering twice.
	if form_data.get("client_op_id"):
		existing = frappe.db.get_value(
			"DA Integration Event",
			{"event_type": "Farmer Registration", "reference_id": message_id},
			["name", "status", "external_reference"],
			as_dict=True,
		)
		if existing and existing.status in ("Processing", "Completed"):
			reference = existing.external_reference or None
			return {
				"success": existing.status == "Completed",
				"message_id": message_id,
				"correlation_id": reference if mode == "ingest" else None,
				"submission_id": reference if mode == "intake" else None,
				"status": "DUPLICATE",
				"event_id": existing.name,
				"error_message": None,
				"duplicate": True,
			}

	event = frappe.get_doc(
		{
			"doctype": "DA Integration Event",
			"event_type": "Farmer Registration",
			"status": "Processing",
			"reference_id": message_id,
			"payload": json.dumps(payload, default=str),
			"attempts": 1,
			"max_attempts": 1,
		}
	)
	event.insert(ignore_permissions=True)

	def keep_draft(submission_id: str) -> None:
		# Recorded as soon as the draft exists, so a later failure still points at it.
		event.db_set("external_reference", submission_id, update_modified=False)

	try:
		if mode == "intake":
			result = intake.submit_plan(client, payload["sections"], on_draft=keep_draft)
		else:
			result = client.register_farmer(payload)
	except FarmerRegistryClientError as exc:
		# 4xx: the request was wrong (auth, roles, payload). Not retryable.
		note = _discard_failed_draft(client, event.get("external_reference")) if mode == "intake" else ""
		_record_registration_failure(event, f"{exc!s}{note}")
		frappe.throw(
			_("Farmer Registry rejected the registration: {0}").format(exc),
			frappe.ValidationError,
		)
	except FarmerRegistryError as exc:
		# A 5xx or timeout half-way leaves the same half-filled draft behind.
		note = _discard_failed_draft(client, event.get("external_reference")) if mode == "intake" else ""
		_record_registration_failure(event, f"{exc!s}{note}")
		raise
	except Exception as exc:
		frappe.log_error(title="Farmer registration failed", message=frappe.get_traceback())
		_record_registration_failure(event, str(exc))
		raise

	reference = result.get("submission_id") if mode == "intake" else result.get("correlation_id")
	if result.get("success"):
		event.db_set("status", "Completed", update_modified=False)
		event.db_set("external_reference", reference or "", update_modified=False)
	else:
		failed_section = result.get("failed_section")
		message = result.get("error_message") or "Registration rejected"
		# A section the registry rejected leaves a half-filled draft behind, which
		# registry staff would see as a second "farmer". Remove it; the event keeps
		# its id for diagnosis.
		note = _discard_failed_draft(client, result.get("submission_id")) if mode == "intake" else ""
		_record_registration_failure(
			event, f"{failed_section}: {message}{note}" if failed_section else f"{message}{note}"
		)
		return {
			"success": False,
			"message_id": message_id,
			"correlation_id": result.get("correlation_id"),
			"submission_id": result.get("submission_id"),
			"status": result.get("status"),
			"event_id": event.name,
			"error_code": result.get("error_code"),
			"error_message": result.get("error_message"),
			"failed_section": failed_section,
			"duplicate": False,
		}
	frappe.db.commit()

	return {
		"success": bool(result.get("success")),
		"message_id": message_id,
		"correlation_id": result.get("correlation_id"),
		"submission_id": result.get("submission_id"),
		"status": result.get("status"),
		"event_id": event.name,
		"error_message": result.get("error_message"),
		"duplicate": False,
	}


def _discard_failed_draft(client, submission_id: str | None) -> str:
	"""Delete the registry draft of a failed intake registration; returns a note for last_error."""
	if not submission_id:
		return ""
	try:
		deleted = client.delete_intake_submission(submission_id)
	except Exception as exc:
		frappe.log_error(
			title="Farmer registration draft not deleted",
			message=f"{submission_id}: {exc!s}\n{frappe.get_traceback()}",
		)
		return f" (draft {submission_id} kept: {exc!s})"
	return f" (draft {submission_id} {'deleted' if deleted else 'already gone'})"


def get_registration(submission_id: str, ctx: ScopeContext) -> dict:
	"""A registration as the registry holds it right now (DA-272).

	Before approval the farmer exists only as an intake submission, so the
	profile / search routes cannot show it; this reads the submission back
	with every saved section. Restricted callers see only submissions made
	through DA Services, and a DA only their own.
	"""
	event = frappe.db.get_value(
		"DA Integration Event",
		{"event_type": "Farmer Registration", "external_reference": submission_id},
		["name", "status", "last_error", "owner", "creation"],
		as_dict=True,
	)
	if not ctx.unrestricted:
		if not event:
			frappe.throw(_("Registration {0} not found.").format(submission_id), frappe.DoesNotExistError)
		if ctx.has_any_role(C.ROLE_DA) and event.owner != frappe.session.user:
			frappe.throw(_("Registration is not yours."), frappe.PermissionError)

	submission = get_farmer_registry_client().get_intake_submission(submission_id)
	if not submission:
		frappe.throw(_("Registration {0} not found.").format(submission_id), frappe.DoesNotExistError)

	out = registration_to_dict(submission)
	out["event"] = (
		{
			"event_id": event.name,
			"status": event.status,
			"last_error": event.last_error,
			"submitted_by": event.owner,
			"submitted_at": event.creation,
		}
		if event
		else None
	)
	return out


ALLOWED_DOCUMENT_TYPES = {"application/pdf", "image/jpeg", "image/png"}
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024


def upload_registration_document(
	content: bytes, filename: str, content_type: str | None, ctx: ScopeContext
) -> dict:
	"""Upload a farmer's identity document to the registry before registration.

	Returns {"document_id", "file_name", "size"}; pass document_id as
	identity_document_id in POST /farmers.
	"""
	content_type = (content_type or "").split(";")[0].strip().lower()
	if content_type not in ALLOWED_DOCUMENT_TYPES:
		frappe.throw(_("Document must be a PDF, JPEG or PNG file."), frappe.ValidationError)
	if not content:
		frappe.throw(_("Document is empty."), frappe.ValidationError)
	if len(content) > MAX_DOCUMENT_BYTES:
		frappe.throw(_("Document is larger than 5 MB."), frappe.ValidationError)

	safe_name = registry_document_name(filename, content_type)
	client = get_farmer_registry_client()
	try:
		document_id = client.upload_document(safe_name, content, content_type)
	except FarmerRegistryClientError as exc:
		frappe.throw(_("Farmer Registry rejected the document: {0}").format(exc), frappe.ValidationError)
	return {"document_id": document_id, "file_name": safe_name, "size": len(content)}


_DOCUMENT_EXTENSIONS = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}


def registry_document_name(filename: str | None, content_type: str) -> str:
	"""A file name the registry accepts: one extension, no spaces or extra dots.

	The registry rejects "WhatsApp Image 2026-10-29 at 7.46.44 PM.jpeg" as
	"multiple extensions", so the stem is reduced to [A-Za-z0-9_-] and the
	extension comes from the content type.
	"""
	ext = _DOCUMENT_EXTENSIONS.get(content_type, "bin")
	stem = (filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
	if "." in stem:
		stem = stem.rsplit(".", 1)[0]
	stem = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in stem).strip("-_")
	while "--" in stem:
		stem = stem.replace("--", "-")
	return f"{stem[:80] or 'identity-document'}.{ext}"
