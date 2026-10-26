"""Farmer profile and listing services (DA-40, DA-39).

Business logic for farmer profile display, combining Farmer Registry data
with DA Visit history, pending actions (DA-39), and permission checks.
"""

import frappe
from frappe import _

from da_services.da_services.doctype.da_visit.da_visit import OPEN_STATES
from da_services.integrations.farmer_registry import get_farmer_registry_client
from da_services.integrations.farmer_registry.client import FarmerRegistryClientError
from da_services.integrations.farmer_registry.mapper import (
	da_form_to_ingest_payload,
	farmer_to_reference_dict,
	reference_dict_to_profile,
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

	# Get visit data (last visit, pending tasks)
	visit_data = _get_farmer_visit_data(farmer_id)

	# Check if farmer is editable by current DA
	can_edit = _can_edit_farmer(farmer_id, ctx)

	# Merge all data into profile
	profile = reference_dict_to_profile(ref, visit_data)
	profile["can_edit"] = can_edit
	da_ids_list = list(ctx.da_ids) if ctx.da_ids else []
	profile["is_linked"] = _is_farmer_linked_to_da(farmer_id, da_ids_list)

	return profile


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

	# Scope filters - DA-211 Kebele-based scoping
	# DAs see all farmers in their assigned Kebele, not just manually linked ones.
	# Supervisors with multiple Woredas see all of them.
	# Zone-scoped Supervisors are denied (no zone field on DA Farmer Reference).
	if not ctx.unrestricted and ctx.has_any_role(C.ROLE_DA):
		# DA sees all farmers in assigned Kebele (DA-211 change)
		da_ids_list = list(ctx.da_ids) if ctx.da_ids else []
		assigned_kebeles = _get_da_assigned_kebeles(da_ids_list)
		if not assigned_kebeles:
			# DA has no Kebele assignment - empty list
			return [], 0
		filters["kebele"] = ("in", assigned_kebeles)

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
					# Zone-scoped: would need to validate woreda is in their zone
					# For now, deny (zone validation not implemented)
					frappe.throw(_("Cannot filter by Woreda with Zone-only scope."), frappe.PermissionError)
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
	# This ensures zone-scoped Supervisors are denied and multi-Woreda
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

	# Cache it
	ref_dict = farmer_to_reference_dict(farmer)
	ref_dict["last_synced_at"] = frappe.utils.now_datetime()
	ref_dict["sync_version"] = 1

	doc = frappe.get_doc(
		{
			"doctype": "DA Farmer Reference",
			**ref_dict,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	return doc.as_dict()


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


def _da_display_name(da_id: str) -> str | None:
	"""The DA's full name from the DA Registry, or None when it cannot be read."""
	from da_services.integrations.da_registry.client import DARegistryError, get_client

	try:
		return (get_client().get_da(da_id).full_name or "").strip()[:140] or None
	except DARegistryError:
		return None


def register_farmer(form_data: dict, ctx: ScopeContext) -> dict:
	"""Register a new farmer through the Farmer Registry ingest pipeline.

	Maps the DA registration form to the registry ingest envelope, submits it,
	and records the attempt as a DA Integration Event for traceability. The
	registry processes ingestion asynchronously, so a correlation_id is returned
	rather than a finished farmer; the farmer appears after Woreda approval.

	Args:
	    form_data: DA registration form (see mapper.da_form_to_ingest_payload).
	    ctx: Scope context of the submitting user (for audit attribution).

	Returns:
	    {
	        "success": bool,
	        "message_id": str,
	        "correlation_id": str | None,
	        "status": str,
	        "event_id": str,
	        "error_message": str | None,
	    }
	"""
	import json

	if not isinstance(form_data, dict) or not form_data.get("first_name"):
		frappe.throw(_("Farmer first name is required."), frappe.ValidationError)

	# The registry shows the enumerator's name as the record's creator; take it from
	# the DA Registry when the form only carries the DA-ID (best effort, never blocks).
	if form_data.get("da_id") and not form_data.get("enumerator_name"):
		name = _da_display_name(form_data["da_id"])
		if name:
			form_data = {**form_data, "enumerator_name": name}

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
			return {
				"success": existing.status == "Completed",
				"message_id": message_id,
				"correlation_id": existing.external_reference or None,
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

	client = get_farmer_registry_client()
	try:
		result = client.register_farmer(payload)
	except FarmerRegistryClientError as e:
		# 4xx: our request was wrong (auth, roles, payload). Not retryable —
		# surface the registry's reason to the caller as a 400.
		event.db_set("status", "Failed", update_modified=False)
		event.db_set("last_error", str(e)[:500], update_modified=False)
		frappe.db.commit()
		frappe.throw(_("Farmer Registry rejected the registration: {0}").format(e), frappe.ValidationError)
	except Exception as e:
		# 5xx / network: transient. Record it and let the error propagate.
		event.db_set("status", "Failed", update_modified=False)
		event.db_set("last_error", str(e)[:500], update_modified=False)
		frappe.db.commit()
		raise

	if result.get("success"):
		event.db_set("status", "Completed", update_modified=False)
		event.db_set("external_reference", result.get("correlation_id") or "", update_modified=False)
	else:
		event.db_set("status", "Failed", update_modified=False)
		event.db_set(
			"last_error", (result.get("error_message") or "Ingest rejected")[:500], update_modified=False
		)
	frappe.db.commit()

	return {
		"success": bool(result.get("success")),
		"message_id": message_id,
		"correlation_id": result.get("correlation_id"),
		"status": result.get("status"),
		"event_id": event.name,
		"error_message": result.get("error_message"),
		"duplicate": False,
	}
