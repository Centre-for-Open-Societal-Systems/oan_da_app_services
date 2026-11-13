"""Pending action state derivation for DA-39.

Determines what action is pending for each farmer based on:
1. Fayda verification status (from Farmer Registry via DA Farmer Reference)
2. Credit application status (from A2C)
3. Visit history - crop survey due (from DA Visit table)
4. Visit history - livestock survey due (from DA Visit table)

NO integration with Crop/Livestock Registries needed - uses visit frequency only.

Priority order (highest first):
1. Fayda check - verification pending/mismatch
2. Credit in review - active application exists
3. Crop survey due - no crop survey in threshold days
4. Livestock due - no livestock survey in threshold days
5. No action due - default state
"""

from datetime import datetime
from typing import Literal

import frappe
from frappe.utils import get_datetime, now_datetime

from da_services.integrations.a2c import get_a2c_client

# Pending action state constants (shared with frontend)
STATE_FAYDA_CHECK = "Fayda check"
STATE_CREDIT_IN_REVIEW = "Credit in review"
STATE_CROP_SURVEY_DUE = "Crop survey due"
STATE_LIVESTOCK_DUE = "Livestock due"
STATE_NO_ACTION_DUE = "No action due"

# Type definitions
PendingActionState = Literal[
	"Crop survey due", "Livestock due", "Credit in review", "Fayda check", "No action due"
]

PendingActionPriority = Literal["high", "medium", "low"]

# Business rules - thresholds for survey due dates
# TODO: Move to site_config or DA Settings doctype for configurability
CROP_SURVEY_DUE_DAYS = 30
LIVESTOCK_SURVEY_DUE_DAYS = 60


class PendingActionResult:
	"""Result of pending action derivation."""

	def __init__(
		self,
		state: PendingActionState,
		priority: PendingActionPriority,
		reason: str | None = None,
		days_overdue: int | None = None,
	):
		self.state = state
		self.priority = priority
		self.reason = reason
		self.days_overdue = days_overdue

	def to_dict(self) -> dict:
		"""Convert to dict for API response."""
		result = {"state": self.state, "priority": self.priority}
		if self.reason:
			result["reason"] = self.reason
		if self.days_overdue is not None:
			result["days_overdue"] = self.days_overdue
		return result


def derive_pending_action(farmer_id: str, farmer_data: dict | None = None) -> PendingActionResult:
	"""Derive pending action state for a farmer (DA-39).

	Args:
	    farmer_id: Farmer Registry internal_record_id
	    farmer_data: Optional farmer reference dict (to avoid re-query)

	Returns:
	    PendingActionResult with state, priority, reason
	"""
	# Use batch method to avoid duplicating logic
	results = derive_pending_actions_batch([farmer_id])
	return results.get(farmer_id, PendingActionResult(state=STATE_NO_ACTION_DUE, priority="low"))


def derive_pending_actions_batch(farmer_ids: list[str]) -> dict[str, PendingActionResult]:
	"""Derive pending actions for multiple farmers (optimized batch).

	Args:
	    farmer_ids: List of farmer IDs

	Returns:
	    Dict mapping farmer_id -> PendingActionResult
	"""
	if not farmer_ids:
		return {}

	# Batch 1: Load farmer references (for registration status)
	farmer_refs = frappe.get_all(
		"DA Farmer Reference",
		filters={"farmer_id": ["in", farmer_ids]},
		fields=["farmer_id", "registration_state"],
	)
	farmer_map = {f["farmer_id"]: f for f in farmer_refs}

	# Batch 2: Load last crop surveys
	crop_visits = _get_last_visits_batch(farmer_ids, "Crop Survey")

	# Batch 3: Load last livestock surveys
	livestock_visits = _get_last_visits_batch(farmer_ids, "Livestock Survey")

	# Batch 4: Check credit applications (single batch call)
	credit_farmer_ids = set()
	a2c_client = get_a2c_client()
	try:
		credit_farmer_ids = a2c_client.has_active_applications(farmer_ids)
	except NotImplementedError:
		# HttpA2CClient raises this when contract is pending
		frappe.log_error(
			title="A2C Integration - Contract Pending",
			message="A2C HTTP API not yet implemented. Using mock client for development.",
		)
	except Exception as e:
		# Log outage once per batch, not per farmer
		frappe.log_error(
			title="A2C Integration - Batch Check Failed",
			message=f"Failed to check {len(farmer_ids)} farmers for credit applications: {e!s}",
		)

	# Derive pending action for each farmer
	results = {}
	for farmer_id in farmer_ids:
		farmer_data = farmer_map.get(farmer_id)
		result = None

		# Priority 1: Fayda check
		# Note: Currently uses registration_state as fallback until Fayda
		# verification service is integrated. FSD 3.4 specifies this should
		# check Fayda status (Pending/Mismatch) when available.
		if farmer_data:
			result = _check_fayda_status(farmer_id, farmer_data)

		# Priority 2: Credit in review
		# Note: Fixed priority - credit (medium) outranks crop-survey-due (high)
		# as specified in FSD 3.4. Credit applications are time-sensitive and
		# require immediate DA attention regardless of other pending tasks.
		if not result and farmer_id in credit_farmer_ids:
			result = PendingActionResult(
				state=STATE_CREDIT_IN_REVIEW, priority="medium", reason="Active loan application"
			)

		# Priority 3: Crop survey due
		if not result:
			last_crop = crop_visits.get(farmer_id)
			result = _check_crop_survey_due_with_date(last_crop)

		# Priority 4: Livestock survey due
		if not result:
			last_livestock = livestock_visits.get(farmer_id)
			result = _check_livestock_survey_due_with_date(last_livestock)

		# Priority 5: Default
		if not result:
			result = PendingActionResult(state=STATE_NO_ACTION_DUE, priority="low")

		results[farmer_id] = result

	return results


# ============================================================================
# Private helper functions
# ============================================================================


def _check_fayda_status(farmer_id: str, farmer_data: dict | None = None) -> PendingActionResult | None:
	"""Check if Fayda verification is pending.

	Currently uses registration_state as fallback until Fayda verification
	service integration is complete. Per FSD 3.4 and contract, this should
	check Fayda status (Pending/Mismatch) when available.

	Args:
	    farmer_id: Farmer ID
	    farmer_data: Optional pre-loaded farmer data

	Returns:
	    PendingActionResult if Fayda check needed, None otherwise
	"""
	if not farmer_data:
		farmer_data = frappe.db.get_value(
			"DA Farmer Reference", farmer_id, ["registration_state"], as_dict=True
		)

	if not farmer_data:
		return None

	registration_state = farmer_data.get("registration_state")

	# Check for states indicating verification needed
	# DRAFT: Farmer registration not yet submitted
	# PENDING_APPROVAL: Farmer registration submitted, awaiting approval
	if registration_state in ("DRAFT", "PENDING_APPROVAL"):
		return PendingActionResult(
			state=STATE_FAYDA_CHECK,
			priority="high",
			reason=f"Farmer registration {registration_state.lower().replace('_', ' ')}",
		)

	return None


def _check_crop_survey_due(farmer_id: str) -> PendingActionResult | None:
	"""Check if crop survey is due based on DA Visit history."""
	last_survey_date = frappe.db.get_value(
		"DA Visit",
		filters={
			"farmer_id": farmer_id,
			"visit_type": "Crop Survey",
			"physical_status": "Physical Visit Complete",
		},
		fieldname="completed_at",
		order_by="completed_at desc",
	)

	return _check_crop_survey_due_with_date(last_survey_date)


def _check_crop_survey_due_with_date(last_survey_date: datetime | None) -> PendingActionResult | None:
	"""Check if crop survey is due given the last survey date."""
	if not last_survey_date:
		return PendingActionResult(
			state=STATE_CROP_SURVEY_DUE, priority="high", reason="No crop survey on record"
		)

	now = now_datetime()
	last_date = get_datetime(last_survey_date)
	days_since = (now - last_date).days

	if days_since > CROP_SURVEY_DUE_DAYS:
		days_overdue = days_since - CROP_SURVEY_DUE_DAYS
		priority = "high" if days_overdue >= 15 else "medium"

		return PendingActionResult(
			state=STATE_CROP_SURVEY_DUE,
			priority=priority,
			reason=f"Last crop survey {days_since} days ago",
			days_overdue=days_overdue,
		)

	return None


def _check_livestock_survey_due(farmer_id: str) -> PendingActionResult | None:
	"""Check if livestock survey is due based on DA Visit history."""
	last_survey_date = frappe.db.get_value(
		"DA Visit",
		filters={
			"farmer_id": farmer_id,
			"visit_type": "Livestock Survey",
			"physical_status": "Physical Visit Complete",
		},
		fieldname="completed_at",
		order_by="completed_at desc",
	)

	return _check_livestock_survey_due_with_date(last_survey_date)


def _check_livestock_survey_due_with_date(last_survey_date: datetime | None) -> PendingActionResult | None:
	"""Check if livestock survey is due given the last survey date."""
	if not last_survey_date:
		# Don't flag if never surveyed (farmer may not have livestock)
		return None

	now = now_datetime()
	last_date = get_datetime(last_survey_date)
	days_since = (now - last_date).days

	if days_since > LIVESTOCK_SURVEY_DUE_DAYS:
		days_overdue = days_since - LIVESTOCK_SURVEY_DUE_DAYS
		return PendingActionResult(
			state=STATE_LIVESTOCK_DUE,
			priority="medium",
			reason=f"Last livestock survey {days_since} days ago",
			days_overdue=days_overdue,
		)

	return None


def _get_last_visits_batch(farmer_ids: list[str], visit_type: str) -> dict[str, datetime | None]:
	"""Get last completed visits for multiple farmers (batch query).

	Args:
	    farmer_ids: List of farmer IDs
	    visit_type: Visit type filter

	Returns:
	    Dict mapping farmer_id -> last visit datetime
	"""
	if not farmer_ids:
		return {}

	visits = frappe.get_all(
		"DA Visit",
		filters={
			"farmer_id": ["in", farmer_ids],
			"visit_type": visit_type,
			"physical_status": "Physical Visit Complete",
		},
		fields=["farmer_id", "completed_at"],
		order_by="completed_at desc",
	)

	# Keep only the most recent visit per farmer
	result = {}
	for visit in visits:
		farmer_id = visit["farmer_id"]
		if farmer_id not in result:
			result[farmer_id] = visit["completed_at"]

	return result
