"""Offline retry queue for grievance integration events.

Uses DA Integration Event DocType rows as the persistent queue. The scheduler sweeps
pending rows every 5 minutes; ``frappe.enqueue()`` fires an immediate best-effort retry
on first failure so the user doesn't wait for the next tick.

The DocType row is the source of truth, not the Redis queue.
"""

from __future__ import annotations

import json
import traceback
from datetime import timedelta

import frappe
from frappe.utils import now_datetime

from .client import GrievanceServiceUnavailable, get_client
from .schemas import GrievanceAction, GrievanceSubmission

BACKOFF_BASE_SECONDS = 60
MAX_BACKOFF_SECONDS = 3600
STALE_PROCESSING_MINUTES = 10


def _backoff_seconds(attempt: int) -> int:
	"""Exponential backoff: 1m, 2m, 4m, 8m, … capped at 1h."""
	return min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)


def enqueue_event(
	event_type: str,
	payload: dict,
	reference_id: str | None = None,
) -> str:
	"""Insert a DA Integration Event row and optionally fire an immediate retry."""
	doc = frappe.get_doc(
		{
			"doctype": "DA Integration Event",
			"event_type": event_type,
			"status": "Pending",
			"payload": json.dumps(payload, default=str),
			"reference_id": reference_id or "",
			"attempts": 0,
			"max_attempts": 5,
			"next_retry_at": now_datetime(),
		}
	)
	doc.insert(ignore_permissions=True)

	frappe.enqueue(
		"da_services.integrations.grievance.queue.process_single",
		event_name=doc.name,
		queue="short",
		enqueue_after_commit=True,
	)
	return doc.name


def process_single(event_name: str) -> None:
	"""Process one integration event. Called by enqueue or the scheduler sweep."""
	if not frappe.db.exists("DA Integration Event", event_name):
		return

	doc = frappe.get_doc("DA Integration Event", event_name)
	if doc.status not in ("Pending", "Processing"):
		return

	doc.db_set("status", "Processing", update_modified=False)
	doc.db_set("attempts", (doc.attempts or 0) + 1, update_modified=False)
	frappe.db.commit()

	try:
		result = _dispatch(doc.event_type, json.loads(doc.payload))
		doc.db_set("status", "Completed", update_modified=True)
		doc.db_set("completed_at", now_datetime(), update_modified=False)
		if result and result.ticket_id:
			doc.db_set("external_reference", result.ticket_id, update_modified=False)
		frappe.db.commit()
	except GrievanceServiceUnavailable as exc:
		_handle_failure(doc, exc)
	except Exception as exc:
		_handle_failure(doc, exc, retryable=False)


def _dispatch(event_type: str, payload: dict):
	"""Route an event to the right client method."""
	client = get_client()
	if event_type == "Grievance Submit":
		payload.pop("priority", None)
		return client.submit_grievance(GrievanceSubmission(**payload))
	if event_type == "Grievance Action":
		return client.take_action(GrievanceAction(**payload))
	frappe.log_error(f"Unknown integration event type: {event_type}", "DA Integration Event")
	return None


def _handle_failure(doc, exc: Exception, retryable: bool = True) -> None:
	error_msg = traceback.format_exc()
	attempts = doc.attempts or 1

	if retryable and attempts < (doc.max_attempts or 5):
		next_retry = now_datetime() + timedelta(seconds=_backoff_seconds(attempts))
		doc.db_set("status", "Pending", update_modified=True)
		doc.db_set("next_retry_at", next_retry, update_modified=False)
	else:
		doc.db_set("status", "Failed", update_modified=True)

	doc.db_set("last_error", error_msg, update_modified=False)
	frappe.db.commit()


def process_pending() -> None:
	"""Scheduler entry point: sweep retryable rows and recover stale Processing rows.

	Only processes grievance events - other event types (e.g., Farmer Update) have
	their own processors.
	"""
	now = now_datetime()

	_recover_stale_processing(now)

	events = frappe.get_all(
		"DA Integration Event",
		filters={
			"status": "Pending",
			"next_retry_at": ("<=", now),
			"event_type": ["in", ["Grievance Submit", "Grievance Action"]],
		},
		fields=["name"],
		order_by="next_retry_at asc",
		limit_page_length=50,
	)
	for row in events:
		try:
			process_single(row.name)
		except Exception:
			frappe.log_error(
				f"Failed to process integration event {row.name}",
				"DA Integration Queue",
			)


def _recover_stale_processing(now) -> None:
	"""Reset rows stuck in Processing for longer than STALE_PROCESSING_MINUTES.

	This handles the case where the server crashed mid-processing — the row would
	otherwise be orphaned forever since the scheduler only sweeps Pending rows.

	Only recovers grievance events - leaves Conflict status and other event types alone.
	"""
	cutoff = now - timedelta(minutes=STALE_PROCESSING_MINUTES)
	stale = frappe.get_all(
		"DA Integration Event",
		filters={
			"status": "Processing",
			"modified": ("<=", cutoff),
			"event_type": ["in", ["Grievance Submit", "Grievance Action"]],
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


def retry_failed(event_name: str | None = None) -> list[str]:
	"""Reset failed events back to Pending for re-processing.

	If event_name is given, retry that single event. Otherwise retry all Failed events.
	Returns list of event names that were reset.
	"""
	filters = {"status": "Failed"}
	if event_name:
		filters["name"] = event_name

	events = frappe.get_all("DA Integration Event", filters=filters, fields=["name"])
	retried = []
	for row in events:
		doc = frappe.get_doc("DA Integration Event", row.name)
		doc.db_set("status", "Pending", update_modified=True)
		doc.db_set("attempts", 0, update_modified=False)
		doc.db_set("next_retry_at", now_datetime(), update_modified=False)
		doc.db_set("last_error", "", update_modified=False)
		retried.append(row.name)

	if retried:
		frappe.db.commit()
	return retried
