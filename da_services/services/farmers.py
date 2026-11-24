"""Farmer profile and listing services (DA-40, DA-39).

Business logic for farmer profile display, combining Farmer Registry data
with DA Visit history, pending actions (DA-39), and permission checks.
"""

import json

import frappe
from frappe import _
from frappe.query_builder.functions import Count, Max

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
from da_services.services import geo
from da_services.services.da_lookup import get_da_reference
from da_services.services.pending_actions import derive_pending_actions_batch
from da_services.utils.permissions import require_permission


def get_farmer_profile(farmer_id: str) -> dict:
	"""Get complete farmer profile for DA-40 profile screen.

	Combines:
	- Farmer Registry data (cached in DA Farmer Reference)
	- DA Visit history (last visit, pending actions)
	- Edit permissions (for DA-41)

	Args:
	    farmer_id: Farmer Registry internal_record_id

	Returns:
	    Complete profile dict for frontend

	Raises:
	    DoesNotExistError: Farmer not found
	    PermissionError: Farmer not in scope
	"""
	ref = _require_farmer_in_scope(farmer_id)

	# Get visit data (last visit, pending tasks)
	visit_data = _get_farmer_visit_data(farmer_id)

	# Merge all data into profile
	profile = reference_dict_to_profile(ref, visit_data)
	# Sections the registry could not deliver on a fresh fetch (empty on cache hits
	# and complete reads); the UI can show "partial data" instead of blanks.
	profile["incomplete_sections"] = list(ref.get("incomplete_sections") or [])
	profile["can_edit"] = True  # no edit permission check
	profile["is_linked"] = False  # no caller DA-ID to link against

	return profile


def _require_farmer_in_scope(farmer_id: str) -> dict:
	"""The cached farmer reference, after the 404 / 403 checks every farmer read shares.

	Raises:
	    DoesNotExistError: Farmer not found
	    PermissionError: Farmer not in scope
	"""
	# Get or sync farmer reference from cache
	ref = _get_or_sync_farmer_reference(farmer_id)
	if not ref:
		frappe.throw(_("Farmer {0} not found.").format(farmer_id), frappe.DoesNotExistError)

	# Role permission, then the farmer is inside the caller's User Permissions (DA Kebele /
	# Woreda / Zone / Region) or linked to the caller's own DA-ID (DA Farmer Link).
	if not _farmer_permitted(farmer_id, "read"):
		require_permission(
			"DA Farmer Reference", "read", doc=ref.get("name") or farmer_id, user=frappe.session.user
		)

	return ref


def resolve_farmer_id(farmer_id: str) -> str:
	"""Accept the registry's internal id or the Farmer-ID shown in the UI (FR-…).

	A registry lookup failure (no auth, unreachable staff, not found) leaves the
	input unchanged so callers such as ``resolve_farmer_for_capture`` still 404
	on an unknown Farmer-ID instead of surfacing a 500.
	"""
	if frappe.db.exists("DA Farmer Reference", farmer_id):
		return farmer_id
	cached = frappe.db.get_value("DA Farmer Reference", {"reg_id": farmer_id}, "name")
	if cached:
		return cached
	finder = getattr(get_farmer_registry_client(), "find_internal_id", None)
	if not finder:
		return farmer_id
	try:
		found = finder(farmer_id)
	except FarmerRegistryError:
		found = None
	return found or farmer_id


def resolve_farmer_for_capture(
	farmer_id: str,
	collector_da_id: str | None = None,
	*,
	survey_woreda: str | None = None,
) -> dict:
	"""Resolve a farmer and prove a collector may capture a survey response for them.

	This is the shared entry point for survey capture (FR-09d). It does not duplicate
	scope logic: ``_require_farmer_in_scope`` applies the normal 404 / 403 read rules,
	and ``_is_farmer_in_da_kebele`` applies the DA-211 Kebele-assignment rule.

	Args:
	    farmer_id: Registry internal id or the Farmer-ID shown in the UI (``FR-…``).
	    collector_da_id: DA whose Kebeles must contain the farmer. A Development
	        Agent defaults to their own DA-ID.
	    survey_woreda: Woreda stamped on the survey template, when it has one.

	Returns:
	    The cached farmer reference with ``farmer_id`` set to the canonical internal id.

	Raises:
	    DoesNotExistError: Farmer unknown (404).
	    PermissionError: Farmer outside the collector's Kebele / Woreda, or outside
	        the survey's Woreda (403).
	    ValidationError: Farmer has no Kebele (or no Woreda needed to check scope).
	"""
	farmer_id = (farmer_id or "").strip()
	if not farmer_id:
		frappe.throw(_("farmer_id is required."), frappe.ValidationError)

	canonical_id = resolve_farmer_id(farmer_id)
	ref = _require_farmer_in_scope(canonical_id)
	ref = dict(ref)
	ref["farmer_id"] = canonical_id

	kebele = ref.get("kebele")
	if not kebele:
		frappe.throw(
			_("Farmer {0} has no Kebele; a survey response cannot be recorded.").format(canonical_id),
			frappe.ValidationError,
		)

	collector_da_id = (collector_da_id or "").strip() or None

	if collector_da_id:
		if not _is_farmer_in_da_kebele(canonical_id, [collector_da_id], kebele=kebele):
			frappe.throw(
				_("Farmer {0} is not in a Kebele assigned to DA {1}.").format(canonical_id, collector_da_id),
				frappe.PermissionError,
			)

		# The collector's own Woreda must agree with the farmer's, so a response is
		# never filed against a DA that does not cover the farmer's Woreda.
		da_ref = get_da_reference(collector_da_id)
		farmer_woreda = ref.get("woreda")
		if farmer_woreda and da_ref.woreda and farmer_woreda != da_ref.woreda:
			frappe.throw(
				_("Farmer {0} is in Woreda {1}, outside DA {2}'s Woreda.").format(
					canonical_id, farmer_woreda, collector_da_id
				),
				frappe.PermissionError,
			)
		if survey_woreda and da_ref.woreda and da_ref.woreda != survey_woreda:
			frappe.throw(
				_("DA {0} is outside the survey Woreda {1}.").format(collector_da_id, survey_woreda),
				frappe.PermissionError,
			)

	if survey_woreda:
		farmer_woreda = ref.get("woreda")
		if not farmer_woreda:
			frappe.throw(
				_("Farmer {0} has no Woreda; the survey's Woreda cannot be verified.").format(canonical_id),
				frappe.ValidationError,
			)
		if farmer_woreda != survey_woreda:
			frappe.throw(
				_("Farmer {0} is outside the survey Woreda {1}.").format(canonical_id, survey_woreda),
				frappe.PermissionError,
			)

	return ref


def get_farmer_change_requests(farmer_id: str, *, status: str | None = None, tab: str | None = None) -> dict:
	"""Change requests the registry holds for a farmer (DA-272).

	The farmer must be readable by the caller (same 404 / 403 rules as the
	profile). ``status`` keeps one approval_status; ``tab`` one register tab.
	"""
	internal_id = resolve_farmer_id(farmer_id)
	_require_farmer_in_scope(internal_id)
	rows = get_farmer_registry_client().get_change_requests(internal_id, tab=tab, status=status)
	return {
		"farmer_id": farmer_id,
		"change_requests": [change_request_to_dict(r) for r in rows],
		"total": len(rows),
	}


def search_farmers(
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
	yesterday is found. ``woreda`` is required, so a search always runs
	inside one Woreda.
	"""
	filter_by: dict = {}

	if not woreda:
		frappe.throw(_("woreda is required."), frappe.ValidationError)
	_require_woreda_in_scope(woreda)
	filter_by.update({"woreda": woreda, "kebele": kebele})
	if state:
		filter_by["state"] = state

	result = get_farmer_registry_client().search_farmers(
		search_text=search_text, filter_by=filter_by, page=page, page_size=page_size
	)
	return {
		"farmers": [list_item_to_dict(i) for i in result.get("items") or []],
		"total": int(result.get("total") or 0),
		"page": int(result.get("page") or page),
		"page_size": int(result.get("page_size") or page_size),
		"number_of_pages": int(result.get("number_of_pages") or 0),
		"filtered_client_side": bool(result.get("filtered_client_side")),
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


def _kebele_filter_values(kebele: str):
	"""Match a Kebele code or display name, because the registry stores display names."""
	raw = (kebele or "").strip()
	if not raw:
		return raw
	row = frappe.db.get_value(
		"DA Administrative Area",
		raw.upper(),
		["name", "name_en"],
		as_dict=True,
	)
	if not row:
		return raw
	values = [row.name]
	if row.name_en and row.name_en not in values:
		values.append(row.name_en)
	return ("in", values) if len(values) > 1 else row.name


def _require_woreda_in_scope(woreda: str) -> None:
	"""A Woreda the caller may search in: read on that DA Woreda, which the caller's User
	Permissions (DA Woreda, or DA Zone through its zone link) narrow."""
	if not frappe.db.exists("DA Woreda", woreda):
		frappe.throw(_("Woreda {0} is not in the DA Woreda master.").format(woreda), frappe.ValidationError)
	if not frappe.has_permission("DA Woreda", "read", doc=woreda, user=frappe.session.user):
		frappe.throw(_("Woreda {0} is not in your scope.").format(woreda), frappe.PermissionError)


def list_farmers(
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

	# Scope: get_list below applies the caller's User Permissions (DA Kebele / Woreda /
	# Zone / Region). Live registry: fill the cache for the caller's own Kebeles.
	assigned_kebeles = frappe.get_all(
		"DA Kebele Assignment",
		filters={"user": frappe.session.user, "state": "Effective"},
		pluck="kebele",
	)
	if assigned_kebeles:
		_ensure_registry_cache(kebeles=sorted(set(assigned_kebeles)))

	# User filters only narrow: User Permissions still bound the rows.
	if woreda:
		filters["woreda"] = woreda

	if kebele:
		filters["kebele"] = _kebele_filter_values(kebele)
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

	# get_list, so the caller's User Permissions (DA Region / Zone / Woreda / Kebele) apply:
	# Zone-scoped officers see only their Zone, multi-Woreda Supervisors all their Woredas.
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

	# Count through get_list too: frappe.db.count() ignores User Permissions.
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


def check_farmer_edit_permissions(farmer_id: str) -> dict:
	"""Check if user can edit this farmer (DA-41 permission check).

	Args:
	    farmer_id: Farmer Registry internal_record_id

	Returns:
	    {
	        "can_edit": True (no edit permission check),
	        "reason": None,
	        "is_linked": False (no caller DA-ID to link against),
	        "link_type": None
	    }
	"""
	return {
		"can_edit": True,
		"reason": None,
		"is_linked": False,
		"link_type": None,
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

	# Not in cache, fetch from registry. Auth / transport failures are treated as
	# "not found" here so farmer reads still 404 instead of 500 outside a staff session.
	client = get_farmer_registry_client()
	try:
		farmer = client.get_farmer(farmer_id)
	except FarmerRegistryError:
		return None

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
	ref_dict.update(_geo_links(farmer_id, ref_dict))
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


def _geo_links(farmer_id: str, places: dict) -> dict:
	"""Region / Zone / Woreda / Kebele of ``places`` as master codes.

	The registry gives the places by name; the cache links them to the geo masters. A
	name the masters do not have is left empty and logged, so the farmer can still be read.
	"""
	levels = ("region", "zone", "woreda", "kebele")
	codes = geo.codes_for_names(**{level: places.get(level) for level in levels})
	unknown = {level: places.get(level) for level in levels if places.get(level) and not codes[level]}
	if unknown:
		frappe.logger("da_services").warning(
			"Farmer %s: not in the geo masters: %s (run geo_master_sync)", farmer_id, unknown
		)
	return codes


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

	Visit = frappe.qb.DocType("DA Visit")
	counts = (
		frappe.qb.from_(Visit)
		.select(Visit.farmer_id, Count("*").as_("count"))
		.where(Visit.farmer_id.isin(farmer_ids))
		.groupby(Visit.farmer_id)
		.run(as_dict=True)
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

	Visit = frappe.qb.DocType("DA Visit")
	visits = (
		frappe.qb.from_(Visit)
		.select(Visit.farmer_id, Max(Visit.scheduled_at).as_("scheduled_at"))
		.where(Visit.farmer_id.isin(farmer_ids))
		.groupby(Visit.farmer_id)
		.run(as_dict=True)
	)

	return {row["farmer_id"]: row["scheduled_at"] for row in visits}


def _farmer_permitted(farmer_id: str, ptype: str) -> bool:
	"""`ptype` on the farmer, inside the caller's User Permissions."""
	return frappe.has_permission("DA Farmer Reference", ptype, doc=farmer_id, user=frappe.session.user)


def _is_farmer_in_da_kebele(farmer_id: str, da_ids, kebele: str | None = None) -> bool:
	"""Check if farmer is in any Kebele assigned to the given DA IDs (DA-211).

	The farmer's Kebele is the registry's name ("Lekatit") or a code; an
	assignment holds a code or a name. Both the codes and their English names
	are matched, as the farmer list does, so list, profile and edit agree.

	Args:
		farmer_id: Farmer identifier
		da_ids: DA identifiers
		kebele: The farmer's Kebele when the caller already has it

	Returns:
		True if farmer's Kebele matches any DA's assigned Kebele
	"""
	if not da_ids:
		return False

	farmer_kebele = kebele or frappe.db.get_value("DA Farmer Reference", farmer_id, "kebele")
	if not farmer_kebele:
		return False

	assigned_kebeles = _get_da_assigned_kebeles(list(da_ids))
	return farmer_kebele in set(assigned_kebeles) | set(_area_names(assigned_kebeles))


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
	places = _geo_links(
		item.farmer_id, {"region": item.region, "woreda": item.woreda, "kebele": kebele_code or item.kebele}
	)
	values = {
		"reg_id": item.reg_id,
		"farmer_name": item.name,
		"phone": item.phone,
		**places,
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

	Visit = frappe.qb.DocType("DA Visit")
	Spec = frappe.qb.DocType("DA Visit Specialization")
	results = (
		frappe.qb.from_(Visit)
		.inner_join(Spec)
		.on((Spec.parent == Visit.name) & (Spec.parenttype == "DA Visit"))
		.select(Visit.farmer_id, Spec.specialization)
		.where(Visit.farmer_id.isin(farmer_ids))
		.where(Visit.physical_status.isin(active_statuses))
		.run(as_dict=True)
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


def register_farmer(form_data: dict) -> dict:
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


def get_registration(submission_id: str) -> dict:
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
	if not event:
		frappe.throw(_("Registration {0} not found.").format(submission_id), frappe.DoesNotExistError)
	if event.owner != frappe.session.user and not frappe.has_permission(
		"DA Integration Event", "read", user=frappe.session.user
	):
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


def upload_registration_document(content: bytes, filename: str, content_type: str | None) -> dict:
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
