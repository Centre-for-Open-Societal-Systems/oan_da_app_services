"""Visit specialization derivation (DA-211 Phase 3).

Determines which specializations a visit covers based on visit type and DA profile.
Supports multi-specialization visits (e.g., Combined Crop + Livestock survey).
"""

from __future__ import annotations

import frappe

from da_services.integrations.da_registry.client import DANotFound, DARegistryUnavailable, get_client

# Specialization constants (matching DA Registry values)
SPEC_CROP_PRODUCTION = "Crop Production"
SPEC_LIVESTOCK = "Livestock"
SPEC_NATURAL_RESOURCES = "Natural Resources"
SPEC_SERVICES = "Services"
SPEC_OTHER = "Other"


def derive_visit_specializations(visit_type: str, da_id: str) -> list[str]:
	"""Derive specializations for a visit based on type and DA profile.

	Args:
		visit_type: Visit type (Crop Survey, Livestock Survey, Services, etc.)
		da_id: DA identifier

	Returns:
		List of specialization strings (e.g., ["Crop Production", "Livestock"])

	Derivation rules:
		- Crop Survey → ["Crop Production"]
		- Livestock Survey → ["Livestock"]
		- Services/Follow-up/Advisory → DA's specialization from Registry
		- Combined visits → Multiple specializations (future enhancement)
	"""
	if not visit_type:
		return []

	# Direct mapping for specific visit types
	if visit_type == "Crop Survey":
		return [SPEC_CROP_PRODUCTION]
	elif visit_type == "Livestock Survey":
		return [SPEC_LIVESTOCK]

	# For Services, Advisory, Follow-up: use DA's specialization
	elif visit_type in ("Services", "Advisory", "Follow-up"):
		da_spec = _get_da_specialization(da_id)
		if da_spec:
			return [da_spec]
		# Fallback if DA specialization not available
		return [SPEC_SERVICES]

	# Default fallback
	return []


def _get_da_specialization(da_id: str) -> str | None:
	"""Get DA's specialization from DA Registry.

	Args:
		da_id: DA identifier

	Returns:
		Specialization string or None if unavailable
	"""
	if not da_id:
		return None

	try:
		client = get_client()
		da_profile = client.get_da(da_id)
		# Normalize the specialization from DA Registry
		return normalize_specialization(da_profile.specialisation)
	except DANotFound:
		frappe.log_error(
			title="DA Not Found",
			message=f"DA {da_id} not found in DA Registry when deriving specialization",
		)
		return None
	except (DARegistryUnavailable, Exception) as e:
		frappe.log_error(
			title="DA Registry Unavailable",
			message=f"Could not fetch DA {da_id} specialization: {e!s}",
		)
		return None


def normalize_specialization(spec: str) -> str:
	"""Normalize specialization string to standard values.

	Args:
		spec: Specialization string (may have variations in casing/spacing)

	Returns:
		Normalized specialization string
	"""
	if not spec:
		return SPEC_OTHER

	# Normalize to title case and trim
	spec_upper = spec.strip().upper()
	spec_normalized = spec.strip().title()

	# Handle abbreviations first (before title case)
	if spec_upper == "NRM":
		return SPEC_NATURAL_RESOURCES

	# Map variations to standard values
	if spec_normalized in ("Crop Production", "Crop", "Crops"):
		return SPEC_CROP_PRODUCTION
	elif spec_normalized in ("Livestock", "Animal Husbandry"):
		return SPEC_LIVESTOCK
	elif spec_normalized in ("Natural Resources", "Natural Resource"):
		return SPEC_NATURAL_RESOURCES
	elif spec_normalized in ("Services", "Service"):
		return SPEC_SERVICES
	else:
		return SPEC_OTHER


def get_visit_specializations(visit_name: str) -> list[str]:
	"""Get specializations for an existing visit.

	Args:
		visit_name: DA Visit document name

	Returns:
		List of specialization strings
	"""
	specializations = frappe.get_all(
		"DA Visit Specialization",
		filters={"parent": visit_name},
		pluck="specialization",
	)

	return specializations or []


def set_visit_specializations(visit_doc, specializations: list[str]) -> None:
	"""Set specializations on a visit document (before save).

	Args:
		visit_doc: DA Visit document instance
		specializations: List of specialization strings
	"""
	# Clear existing specializations
	visit_doc.set("specializations", [])

	# Add new specializations (deduplicated)
	seen = set()
	for spec in specializations:
		normalized = normalize_specialization(spec)
		if normalized and normalized not in seen:
			visit_doc.append("specializations", {"specialization": normalized})
			seen.add(normalized)
