"""Development Agent listing service (DA-226).

Provides scope-aware DA listing with filters and search, enforcing RBAC:
- DA: sees only self
- Supervisor: sees their Woreda
- Executive: sees their Region
- Admin: sees all
"""

from __future__ import annotations

import frappe
from frappe import _

from da_services.integrations.da_registry.client import get_client
from da_services.services import constants as C
from da_services.utils.permissions import ScopeContext


def list_agents(
	ctx: ScopeContext,
	*,
	woreda: str | None = None,
	kebele: str | None = None,
	region: str | None = None,
	fayda_status: str | None = None,
	active_status: str | None = None,
	education_tier: str | None = None,
	search: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int]:
	"""List Development Agents visible to the user with RBAC scope enforcement.

	Scope enforcement (DA-226). The widest grant wins:
	- Admin: All DAs (unrestricted), even when the user also holds Development Agent
	- Supervisor: All DAs in their Woreda(s)
	- Executive: All DAs in their Region(s)
	- DA: Only their own record

	Args:
	    ctx: Scope context with user permissions
	    woreda: Filter by woreda code
	    kebele: Filter by kebele code
	    region: Filter by region code
	    fayda_status: Filter by fayda verification status (Verified, Pending, Mismatch)
	    active_status: Filter by active status (Active, On-leave, Separated)
	    education_tier: Filter by education tier (Diploma, Degree, etc.)
	    search: Search by DA-ID or full name (case-insensitive partial match)
	    limit: Page size (default 50, max 500)
	    start: Offset for pagination (default 0)

	Returns:
	    Tuple of (list of DA dicts, total count)

	Example:
	    >>> agents, total = list_agents(ctx, woreda="ET04-W01", limit=20)
	    >>> print(f"Found {total} agents, showing {len(agents)}")
	"""
	registry_client = get_client()

	# Widest grant wins. The Administrator user holds every Frappe role, including
	# Development Agent, so the DA branch must not run ahead of an unrestricted role.
	if ctx.unrestricted:
		all_das = registry_client.list_das()

	elif ctx.has_any_role(C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE):
		# Supervisors/Executives see DAs in their scope
		all_das = []

		# Fetch by woreda scope (narrowest, most common)
		if ctx.woreda_scopes:
			for woreda_code in ctx.woreda_scopes:
				das = registry_client.list_das(woreda=woreda_code)
				all_das.extend(das)
		# Fetch by region scope (broader)
		elif ctx.region_scopes:
			for region_code in ctx.region_scopes:
				das = registry_client.list_das(region=region_code)
				all_das.extend(das)
		else:
			# No scope assigned (shouldn't happen)
			return [], 0

		# Deduplicate (a DA might appear in multiple woredas if scope overlaps)
		seen_ids = set()
		unique_das = []
		for da in all_das:
			if da.da_id not in seen_ids:
				seen_ids.add(da.da_id)
				unique_das.append(da)
		all_das = unique_das

	elif ctx.has_any_role(C.ROLE_DA):
		# A Development Agent with no wider grant sees only their own record.
		if not ctx.da_ids:
			return [], 0

		all_das = []
		for da_id in ctx.da_ids:
			try:
				all_das.append(registry_client.get_da(da_id))
			except Exception:
				# DA not found in registry - skip
				continue

	else:
		frappe.throw(_("You do not have permission to view Development Agents."), frappe.PermissionError)

	# Step 2: Apply filters
	filtered_das = all_das

	# Filter by woreda (explicit filter narrows scope further)
	if woreda:
		filtered_das = [da for da in filtered_das if da.woreda == woreda]

	# Filter by kebele
	if kebele:
		filtered_das = [da for da in filtered_das if da.kebele == kebele]

	# Filter by region
	if region:
		filtered_das = [da for da in filtered_das if da.region == region]

	# Filter by fayda_status
	if fayda_status:
		filtered_das = [da for da in filtered_das if da.fayda_status == fayda_status]

	# Filter by active_status
	if active_status:
		filtered_das = [da for da in filtered_das if da.active_status == active_status]

	# Filter by education_tier
	if education_tier:
		filtered_das = [da for da in filtered_das if da.education_tier == education_tier]

	# Step 3: Apply search (DA-ID or name)
	if search:
		search_lower = search.lower().strip()
		filtered_das = [
			da
			for da in filtered_das
			if search_lower in da.da_id.lower() or search_lower in da.full_name.lower()
		]

	# Step 4: Sort by DA-ID (consistent ordering)
	filtered_das.sort(key=lambda da: da.da_id)

	# Step 5: Calculate total before pagination
	total = len(filtered_das)

	# Step 6: Apply pagination
	paginated_das = filtered_das[start : start + limit]

	# Step 7: Convert to dicts for API response
	result = [_da_reference_to_list_dict(da) for da in paginated_das]

	return result, total


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
	}
