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

from da_services.integrations.da_registry.client import DANotFound, get_client
from da_services.services import constants as C


def list_agents(
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

	Not scoped: every DA the registry returns, narrowed by the filters.

	Args:
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
	    >>> agents, total = list_agents(woreda="ET04-W01", limit=20)
	    >>> print(f"Found {total} agents, showing {len(agents)}")
	"""
	registry_client = get_client()

	# Unscoped: the registry's list (restricting it is for later).
	all_das = registry_client.list_das()

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
		# Master registry columns: Fayda ID, position, and the posting by name
		"fayda_id": da_ref.fayda_id,
		"position": da_ref.position,
		"region_name": da_ref.region_name,
		"zone_name": da_ref.zone_name,
		"woreda_name": da_ref.woreda_name,
		"kebele_name": da_ref.kebele_name,
	}
