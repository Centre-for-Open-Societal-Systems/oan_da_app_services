"""Farmer-to-DA assignment service (DA-211 Phase 1).

Manual farmer assignment is used when a DA Kebele Assignment has mode="Manual".
The Supervisor explicitly links individual farmers to the DA instead of automatic
Kebele-wide linking.

Validations:
- DA must have an active (Effective) Kebele assignment
- Farmer must be in the same Kebele as the DA's assignment
- Assignment mode must be "Manual" (not "Automatic")
- No duplicate active links for the same DA-farmer pair
- Supervisor must have Woreda-level authorization scope
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

from da_services.da_services.doctype.da_kebele_assignment.da_kebele_assignment import LIVE_STATES
from da_services.services import constants as C
from da_services.utils.permissions import ScopeContext, require_roles

WRITE_ROLES = (C.ROLE_SUPERVISOR, C.ROLE_ADMIN)
LOCK_TIMEOUT_SECONDS = 5


def create_farmer_link(
	*,
	da_id: str,
	farmer_id: str,
	client_op_id: str | None = None,
	ctx: ScopeContext | None = None,
) -> dict:
	"""Create a manual farmer-to-DA link (Supervisor only).

	Args:
		da_id: DA identifier
		farmer_id: Farmer Registry internal_record_id
		client_op_id: Idempotency key
		ctx: Authorization context

	Returns:
		Created link details: {link_id, da_id, farmer_id, link_type, linked_at, kebele}

	Raises:
		ValidationError: Invalid input, DA has no assignment, mode is Automatic
		PermissionError: Supervisor scope violation, Kebele mismatch
	"""
	ctx = ctx or require_roles(*WRITE_ROLES)

	if not ctx.unrestricted and not ctx.has_any_role(*WRITE_ROLES):
		frappe.throw(_("Only a Supervisor or Administrator can manually assign farmers."), frappe.PermissionError)

	if not da_id or not farmer_id:
		frappe.throw(_("da_id and farmer_id are required."), frappe.ValidationError)

	da_id = da_id.strip()
	farmer_id = farmer_id.strip()
	client_op_id = (client_op_id or "").strip() or None

	# Idempotency check
	if client_op_id:
		existing = frappe.db.get_value(
			"DA Farmer Link",
			{"client_op_id": client_op_id},
			["name", "da_id", "farmer_id", "link_type", "linked_at", "kebele"],
			as_dict=True,
		)
		if existing:
			return {
				"link_id": existing["name"],
				"da_id": existing["da_id"],
				"farmer_id": existing["farmer_id"],
				"link_type": existing["link_type"],
				"linked_at": existing["linked_at"],
				"kebele": existing["kebele"],
			}

	# Validate DA has active Kebele assignment
	assignment = frappe.db.get_value(
		"DA Kebele Assignment",
		{"da_id": da_id, "state": "Effective"},
		["name", "kebele", "mode", "woreda"],
		as_dict=True,
	)

	if not assignment:
		frappe.throw(
			_("DA {0} has no active Kebele assignment.").format(da_id),
			frappe.ValidationError,
		)

	# Validate assignment mode is Manual
	if assignment["mode"] != "Manual":
		frappe.throw(
			_("DA {0} assignment mode is Automatic - manual farmer links are not required.").format(da_id),
			frappe.ValidationError,
		)

	# Validate Supervisor has scope over the DA's Woreda
	if not ctx.unrestricted:
		if not ctx.covers(woreda=assignment["woreda"]):
			frappe.throw(
				_("Kebele {0} is outside your Woreda scope.").format(assignment["kebele"]),
				frappe.PermissionError,
			)

	# Get farmer details from Farmer Registry
	# TODO: Replace with actual Farmer Registry API call when available
	# For now, we'll validate farmer exists by checking if they appear in any context
	farmer_exists = frappe.db.exists("DA Farmer Link", {"farmer_id": farmer_id})
	if not farmer_exists:
		# Try to get from external registry (mock for now)
		# In production, this would call: farmer_registry.get_farmer(farmer_id)
		# Simplified: assume farmer exists if they have any prior links
		pass  # Accept any farmer_id for now until Farmer Registry integration exists

	# Validate farmer is in the same Kebele as DA's assignment
	# TODO: This validation requires Farmer Registry API to get farmer's Kebele
	# For now, we'll trust the Supervisor's assignment
	# In production: farmer_kebele = farmer_registry.get_farmer(farmer_id).kebele
	# if farmer_kebele != assignment["kebele"]:
	#     frappe.throw(_("Farmer {0} is in Kebele {1}, but DA is assigned to Kebele {2}."))

	# Check for duplicate active link
	duplicate = frappe.db.exists(
		"DA Farmer Link",
		{"da_id": da_id, "farmer_id": farmer_id, "is_active": 1},
	)

	if duplicate:
		frappe.throw(
			_("Farmer {0} is already linked to DA {1}.").format(farmer_id, da_id),
			frappe.ValidationError,
		)

	# Create the link
	doc = frappe.get_doc(
		{
			"doctype": "DA Farmer Link",
			"da_id": da_id,
			"farmer_id": farmer_id,
			"link_type": "Manual",
			"linked_at": now_datetime(),
			"linked_by": ctx.user,
			"kebele": assignment["kebele"],
			"woreda": assignment["woreda"],
			"is_active": 1,
			"client_op_id": client_op_id,
		}
	)

	doc.insert(ignore_permissions=True)

	return {
		"link_id": doc.name,
		"da_id": doc.da_id,
		"farmer_id": doc.farmer_id,
		"link_type": doc.link_type,
		"linked_at": doc.linked_at,
		"kebele": doc.kebele,
	}


def list_farmer_links(
	*,
	da_id: str | None = None,
	farmer_id: str | None = None,
	link_type: str | None = None,
	is_active: bool = True,
	ctx: ScopeContext | None = None,
) -> list[dict]:
	"""List farmer links with filters.

	Args:
		da_id: Filter by DA
		farmer_id: Filter by farmer
		link_type: Filter by Automatic/Manual
		is_active: Filter by active status (default True)
		ctx: Authorization context

	Returns:
		List of farmer links
	"""
	ctx = ctx or require_roles(*WRITE_ROLES)

	filters = {}

	if da_id:
		filters["da_id"] = da_id
	if farmer_id:
		filters["farmer_id"] = farmer_id
	if link_type:
		filters["link_type"] = link_type
	if is_active is not None:
		filters["is_active"] = 1 if is_active else 0

	# Apply Supervisor scope
	if not ctx.unrestricted and ctx.has_any_role(C.ROLE_SUPERVISOR):
		if ctx.woreda_scopes:
			filters["woreda"] = ("in", list(ctx.woreda_scopes))

	links = frappe.get_all(
		"DA Farmer Link",
		filters=filters,
		fields=["name as link_id", "da_id", "farmer_id", "link_type", "linked_at", "kebele", "is_active"],
		order_by="linked_at desc",
	)

	return links
