"""Offline retry queue for grievance integration events.

Not finalized — kept as a skeleton while the approach is under discussion.
Do not rely on these entry points for production behaviour yet.
"""

from __future__ import annotations


def enqueue_event(
	event_type: str,
	payload: dict,
	reference_id: str | None = None,
) -> str:
	"""Placeholder: queue a grievance write for later retry."""
	raise NotImplementedError("Grievance offline queue is not finalized")


def process_single(event_name: str) -> None:
	"""Placeholder: process one DA Integration Event row."""
	return


def process_pending() -> None:
	"""Placeholder: scheduler sweep for pending grievance events."""
	return


def retry_failed(event_name: str | None = None) -> list[str]:
	"""Placeholder: reset failed events for re-processing."""
	return []
