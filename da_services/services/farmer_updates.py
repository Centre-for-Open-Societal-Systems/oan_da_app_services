"""Farmer profile update service (DA-41).

Implements queued farmer profile updates with retry (server-side outbox pattern):
- Permission validation (linked farmers only)
- Field validation (approved fields only)
- Server-side queue with exponential backoff retry
- Version conflict detection (mock-only, Aggregation Layer has no update endpoint yet)
- Integration with Farmer Registry API (via mock client)

Note: This is NOT client-side offline-first. DAs must be online to submit updates.
The queue ensures updates don't block the API response and retry on transient failures.

Uses DA Integration Event for persistent queue.
"""

from __future__ import annotations

import json
import traceback
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from da_services.integrations.farmer_registry import get_farmer_registry_client
from da_services.integrations.farmer_registry.client import FarmerRegistryError
from da_services.services import constants as C
from da_services.utils.permissions import ScopeContext

# ============================================================================
# CONSTANTS
# ============================================================================

# Editable fields (DA-41 approved fields)
EDITABLE_FIELDS = {
	# Basic contact information
	"phone",
	"email",
	"alternative_contact",
	# Household information
	"household_size",
	"dependents",
	# Socio-economic data
	"occupation",
	"income_source",
	"livelihood_type",
	# Land details
	"total_land_hectares",
	"land_tenure",
	"irrigated_land",
	# Certifications
	"certifications",
	"training_status",
}

# Identity/structural fields (NOT editable)
READONLY_FIELDS = {
	"farmer_id",
	"reg_id",
	"farmer_name",
	"national_id",
	"date_of_birth",
	"gender",
	"region",
	"woreda",
	"kebele",
	"registration_state",
	"registered_at",
}

# Retry configuration
BACKOFF_BASE_SECONDS = 60  # 1 minute
MAX_BACKOFF_SECONDS = 3600  # 1 hour
MAX_RETRY_ATTEMPTS = 5


# ============================================================================
# PUBLIC API
# ============================================================================


def queue_farmer_update(
	farmer_id: str,
	updates: dict,
	ctx: ScopeContext,
	client_op_id: str | None = None,
) -> str:
	"""Queue farmer profile update for offline sync (DA-41).

	Saves update to DA Integration Event queue for background processing.
	Triggers immediate sync attempt but doesn't block on failure.

	Args:
	    farmer_id: Farmer Registry internal_record_id
	    updates: Dict of field:value pairs to update
	    ctx: User scope context (for audit trail)
	    client_op_id: Optional idempotency key

	Returns:
	    Integration event ID (queue reference)

	Raises:
	    ValidationError: Invalid fields or values
	    PermissionError: Farmer not linked to DA
	"""
	# Validate farmer exists and get current version
	client = get_farmer_registry_client()
	farmer = client.get_farmer(farmer_id)
	if not farmer:
		frappe.throw(_("Farmer {0} not found.").format(farmer_id), frappe.DoesNotExistError)

	# Validate permissions: linked to the DA or in their assigned Kebele (DA-41, DA-211)
	from da_services.services.farmers import _is_farmer_in_da_kebele, _is_farmer_linked_to_da

	da_ids = list(ctx.da_ids) if ctx.da_ids else []
	if not da_ids or not (
		_is_farmer_linked_to_da(farmer_id, da_ids) or _is_farmer_in_da_kebele(farmer_id, da_ids)
	):
		frappe.throw(
			_(
				"You do not have permission to update this farmer. "
				"Farmer must be linked to you or in your assigned Kebele."
			),
			frappe.PermissionError,
		)

	# Validate fields
	_validate_update_fields(updates)

	# Validate field values
	_validate_field_values(updates)

	# Store client_op_id for deduplication (FR-08 idempotency)
	# reference_id = client_op_id (queryable), farmer_id stays in payload
	op_id = client_op_id or frappe.generate_hash(length=16)

	# Build payload
	payload = {
		"farmer_id": farmer_id,  # Farmer ID stays in payload
		"updates": updates,
		"expected_version": getattr(farmer, "version", 1),
		"da_id": da_ids[0] if da_ids else None,
		"updated_by": frappe.session.user,
		"client_op_id": op_id,  # For reference in error messages
	}

	# Check for existing event with same client_op_id (idempotency)
	existing = frappe.db.get_value(
		"DA Integration Event",
		{
			"event_type": "Farmer Update",
			"reference_id": op_id,  # Match on client_op_id, not farmer_id
			"status": ("in", ["Pending", "Processing", "Completed"]),
		},
		["name", "status", "external_reference"],
		as_dict=True,
	)
	if existing:
		# Already queued/completed, return existing
		frappe.msgprint(_("Update already queued or completed."))
		return existing.name

	# Create integration event (persistent queue)
	event = frappe.get_doc(
		{
			"doctype": "DA Integration Event",
			"event_type": "Farmer Update",
			"status": "Pending",
			"reference_id": op_id,  # Store client_op_id for idempotency
			"payload": json.dumps(payload, default=str),
			"attempts": 0,
			"max_attempts": MAX_RETRY_ATTEMPTS,
			"next_retry_at": now_datetime(),
		}
	)
	event.insert(ignore_permissions=True)
	frappe.db.commit()

	# Trigger immediate sync attempt (best effort, non-blocking)
	frappe.enqueue(
		"da_services.services.farmer_updates.process_farmer_update",
		event_name=event.name,
		queue="short",
		enqueue_after_commit=True,
	)

	return event.name


def process_farmer_update(event_name: str) -> None:
	"""Process farmer update from integration queue.

	Called by:
	- Immediate enqueue after queue_farmer_update()
	- Background scheduler (every 5 minutes for pending events)
	- Manual retry from UI

	Args:
	    event_name: DA Integration Event document name

	Side effects:
	    Updates event status (Processing → Completed/Failed/Conflict)
	    Updates DA Farmer Reference cache on success
	    Schedules retry on failure with exponential backoff
	"""
	if not frappe.db.exists("DA Integration Event", event_name):
		frappe.log_error(
			f"Integration event {event_name} not found",
			"Farmer Update Queue Error",
		)
		return

	event = frappe.get_doc("DA Integration Event", event_name)

	# Skip if not pending/processing
	if event.status not in ("Pending", "Processing"):
		return

	# Mark as processing
	event.db_set("status", "Processing", update_modified=False)
	event.db_set("attempts", (event.attempts or 0) + 1, update_modified=False)
	frappe.db.commit()

	try:
		# Parse payload
		payload = json.loads(event.payload)
		farmer_id = payload["farmer_id"]
		updates = payload["updates"]
		expected_version = payload.get("expected_version")

		# Attempt update via Farmer Registry
		client = get_farmer_registry_client()
		result = client.update_farmer(farmer_id, updates, expected_version)

		# Check for version conflict
		if result.get("conflict"):
			_handle_version_conflict(event, payload, result)
			return

		# Success! Update cache and mark completed
		_update_local_cache(farmer_id, updates)

		event.db_set("status", "Completed", update_modified=False)
		event.db_set("completed_at", now_datetime(), update_modified=False)
		event.db_set("external_reference", str(result.get("version", "")), update_modified=False)
		frappe.db.commit()

		# Log success
		frappe.logger().info(
			f"Farmer update completed: {farmer_id}, event: {event_name}, version: {result.get('version')}"
		)

	except FarmerRegistryError as exc:
		# Registry-specific error (likely transient)
		_handle_sync_failure(event, f"Farmer Registry error: {exc}")

	except Exception as exc:
		# Unexpected error
		frappe.log_error(
			f"Event: {event_name}\nPayload: {event.payload}\n\n{traceback.format_exc()}",
			"Farmer Update Unexpected Error",
		)
		_handle_sync_failure(event, f"Unexpected error: {exc}")


def get_pending_updates(farmer_id: str) -> list[dict]:
	"""Get pending update events for a farmer.

	Used by UI to show "Awaiting sync" status.

	Note: Since reference_id now stores client_op_id (for idempotency),
	we need to search through all Farmer Update events and filter by
	farmer_id in the payload.

	Args:
	    farmer_id: Farmer Registry internal_record_id

	Returns:
	    List of pending event summaries
	"""
	events = frappe.get_all(
		"DA Integration Event",
		filters={
			"event_type": "Farmer Update",
			"status": ("in", ["Pending", "Processing"]),
		},
		fields=["name", "status", "attempts", "next_retry_at", "last_error", "modified", "payload"],
		order_by="modified desc",
	)

	# Filter events by farmer_id in payload (since reference_id is now client_op_id)
	farmer_events = []
	for e in events:
		try:
			payload = json.loads(e.payload)
			if payload.get("farmer_id") == farmer_id:
				farmer_events.append(
					{
						"event_id": e.name,
						"status": e.status,
						"attempts": e.attempts,
						"next_retry": e.next_retry_at,
						"last_error": e.last_error,
						"queued_at": e.modified,
					}
				)
		except json.JSONDecodeError, KeyError:
			continue

	return farmer_events


def retry_failed_update(event_name: str) -> dict:
	"""Manually retry a failed farmer update.

	Used by admin UI to retry failed syncs.

	Args:
	    event_name: DA Integration Event document name

	Returns:
	    Status dict with retry result

	Raises:
	    DoesNotExistError: Event not found
	    ValidationError: Event not in Failed status or not a Farmer Update event
	"""
	if not frappe.db.exists("DA Integration Event", event_name):
		frappe.throw(_("Event {0} not found.").format(event_name), frappe.DoesNotExistError)

	event = frappe.get_doc("DA Integration Event", event_name)

	# Guard: Only retry Farmer Update events (not grievance events)
	if event.event_type != "Farmer Update":
		frappe.throw(
			_("Event {0} is type '{1}', not 'Farmer Update'.").format(event_name, event.event_type),
			frappe.ValidationError,
		)

	if event.status not in ("Failed", "Conflict"):
		frappe.throw(
			_("Can only retry Failed or Conflict events. Current status: {0}").format(event.status),
			frappe.ValidationError,
		)

	# Reset to Pending and trigger immediate retry
	event.db_set("status", "Pending", update_modified=False)
	event.db_set("next_retry_at", now_datetime(), update_modified=False)
	frappe.db.commit()

	# Trigger sync
	frappe.enqueue(
		"da_services.services.farmer_updates.process_farmer_update",
		event_name=event_name,
		queue="short",
	)

	return {
		"success": True,
		"message": _("Retry triggered for event {0}").format(event_name),
		"event_id": event_name,
	}


# ============================================================================
# VALIDATION HELPERS
# ============================================================================


def _validate_update_fields(updates: dict) -> None:
	"""Validate update fields are editable.

	Args:
	    updates: Dict of field:value pairs

	Raises:
	    ValidationError: If any field is not editable
	"""
	if not updates:
		frappe.throw(_("No fields to update."), frappe.ValidationError)

	# Check for readonly fields
	readonly_attempted = set(updates.keys()) & READONLY_FIELDS
	if readonly_attempted:
		frappe.throw(
			_("Cannot update readonly fields: {0}").format(", ".join(sorted(readonly_attempted))),
			frappe.ValidationError,
		)

	# Check for unknown fields
	unknown_fields = set(updates.keys()) - EDITABLE_FIELDS
	if unknown_fields:
		frappe.throw(
			_("Unknown or non-editable fields: {0}").format(", ".join(sorted(unknown_fields))),
			frappe.ValidationError,
		)


def _validate_field_values(updates: dict) -> None:
	"""Validate field values meet format requirements.

	Args:
	    updates: Dict of field:value pairs

	Raises:
	    ValidationError: If any value is invalid
	"""
	# Validate phone number format (Ethiopian format: 9XXXXXXXX)
	if "phone" in updates:
		phone = updates["phone"]
		if phone:
			import re

			if not re.match(r"^9[0-9]{8}$", phone):
				frappe.throw(
					_("Invalid phone number format. Must be 9XXXXXXXX (9 followed by 8 digits)."),
					frappe.ValidationError,
				)

	# Validate email format
	if "email" in updates:
		email = updates["email"]
		if email:
			import re

			if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
				frappe.throw(_("Invalid email format."), frappe.ValidationError)

	# Validate numeric fields
	numeric_fields = {"household_size", "dependents", "total_land_hectares", "irrigated_land"}
	for field in numeric_fields:
		if field in updates:
			value = updates[field]
			if value is not None:
				try:
					num_value = float(value)
					if num_value < 0:
						frappe.throw(
							_("{0} cannot be negative.").format(field.replace("_", " ").title()),
							frappe.ValidationError,
						)
				except ValueError, TypeError:
					frappe.throw(
						_("{0} must be a number.").format(field.replace("_", " ").title()),
						frappe.ValidationError,
					)


# ============================================================================
# SYNC HELPERS
# ============================================================================


def _handle_version_conflict(event, payload: dict, result: dict) -> None:
	"""Handle version conflict (optimistic concurrency failure).

	Args:
	    event: DA Integration Event document
	    payload: Original update payload
	    result: Registry response with conflict info
	"""
	# Store minimal conflict info (versions only, not full server data)
	conflict_info = {
		"expected_version": payload.get("expected_version"),
		"current_version": result.get("current_version"),
		"conflict": True,
	}

	event.db_set("status", "Conflict", update_modified=False)
	event.db_set(
		"last_error",
		f"Version conflict: expected v{conflict_info['expected_version']}, "
		f"server is at v{conflict_info['current_version']}. "
		f"Manual resolution required.",
		update_modified=False,
	)
	event.db_set("external_reference", json.dumps(conflict_info), update_modified=False)
	frappe.db.commit()

	frappe.logger().warning(
		f"Version conflict for farmer {payload['farmer_id']}: "
		f"expected v{conflict_info['expected_version']}, "
		f"got v{conflict_info['current_version']}"
	)


def _handle_sync_failure(event, error_message: str) -> None:
	"""Handle sync failure with retry logic.

	Args:
	    event: DA Integration Event document
	    error_message: Error description
	"""
	attempts = event.attempts or 0

	if attempts >= MAX_RETRY_ATTEMPTS:
		# Max retries reached, mark as failed
		event.db_set("status", "Failed", update_modified=False)
		event.db_set(
			"last_error",
			f"Max retries ({MAX_RETRY_ATTEMPTS}) reached. {error_message}",
			update_modified=False,
		)
		frappe.db.commit()

		frappe.logger().error(f"Farmer update failed after {attempts} attempts: {error_message}")
	else:
		# Schedule retry with exponential backoff
		backoff_seconds = min(BACKOFF_BASE_SECONDS * (2 ** (attempts - 1)), MAX_BACKOFF_SECONDS)
		next_retry = now_datetime() + timedelta(seconds=backoff_seconds)

		event.db_set("status", "Pending", update_modified=False)
		event.db_set("last_error", error_message[:500], update_modified=False)
		event.db_set("next_retry_at", next_retry, update_modified=False)
		frappe.db.commit()

		frappe.logger().info(
			f"Farmer update retry scheduled in {backoff_seconds}s "
			f"(attempt {attempts}/{MAX_RETRY_ATTEMPTS}): {error_message}"
		)


def _update_local_cache(farmer_id: str, updates: dict) -> None:
	"""Update DA Farmer Reference cache after successful Registry update.

	Args:
	    farmer_id: Farmer Registry internal_record_id
	    updates: Dict of updated field:value pairs
	"""
	if not frappe.db.exists("DA Farmer Reference", farmer_id):
		return

	try:
		# Update only the changed fields
		frappe.db.set_value(
			"DA Farmer Reference",
			farmer_id,
			updates,
			update_modified=False,
		)
		frappe.db.commit()
	except Exception as exc:
		# Cache update failure shouldn't block the sync
		frappe.log_error(
			f"Failed to update cache for farmer {farmer_id}: {exc}",
			"Farmer Cache Update Error",
		)


def _calculate_backoff_seconds(attempt: int) -> int:
	"""Calculate exponential backoff delay.

	Args:
	    attempt: Retry attempt number (1-based)

	Returns:
	    Backoff seconds (capped at MAX_BACKOFF_SECONDS)
	"""
	return min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)


# ============================================================================
# SCHEDULER ENTRY POINT
# ============================================================================


def process_pending_farmer_updates() -> None:
	"""Scheduler entry point: sweep pending farmer updates and recover stale processing rows.

	Called by cron scheduler every 5 minutes (configured in hooks.py).

	Processes only "Farmer Update" events - other event types (e.g., Grievance)
	have their own processors.

	Handles:
	- Pending events with next_retry_at <= now
	- Stale Processing events (stuck for >10 minutes)

	Side effects:
	    Processes up to 50 pending events per sweep
	    Resets stale Processing events to Pending
	"""
	now = now_datetime()

	# Recover events stuck in Processing state (e.g., server crash mid-processing)
	_recover_stale_processing_farmer_updates(now)

	# Sweep pending events ready for retry
	events = frappe.get_all(
		"DA Integration Event",
		filters={
			"event_type": "Farmer Update",
			"status": "Pending",
			"next_retry_at": ("<=", now),
		},
		fields=["name"],
		order_by="next_retry_at asc",
		limit=50,
	)

	for row in events:
		try:
			process_farmer_update(row.name)
		except Exception as exc:
			frappe.log_error(
				f"Failed to process farmer update event {row.name}: {exc}",
				"Farmer Update Queue Error",
			)


def _recover_stale_processing_farmer_updates(now) -> None:
	"""Reset farmer update events stuck in Processing for longer than 10 minutes.

	Handles server crashes mid-processing - the row would otherwise be orphaned
	forever since the scheduler only sweeps Pending rows.

	Only recovers Farmer Update events - leaves Conflict status and other event types alone.

	Args:
	    now: Current datetime for calculating cutoff
	"""
	STALE_PROCESSING_MINUTES = 10
	cutoff = now - timedelta(minutes=STALE_PROCESSING_MINUTES)

	stale = frappe.get_all(
		"DA Integration Event",
		filters={
			"event_type": "Farmer Update",
			"status": "Processing",
			"modified": ("<=", cutoff),
		},
		fields=["name"],
	)

	for row in stale:
		frappe.db.set_value(
			"DA Integration Event",
			row.name,
			{"status": "Pending", "next_retry_at": now},
			update_modified=True,
		)

	if stale:
		frappe.db.commit()
		frappe.logger().warning(f"Recovered {len(stale)} stale farmer update events stuck in Processing")
