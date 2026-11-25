"""Profile resolution hook for oan_auth_service's GET /api/v1/auth/me."""

import frappe

from da_services.api.v1.me import ROLE_CODES


def resolve_user_profile_hook(user_doc, roles=None) -> tuple[str, dict | None]:
	"""Hook subscriber for oan_auth_service's on_user_profile.

	Returns ("da_services", profile_data) namespaced under data["profiles"]["da_services"]
	containing the DA Services roles that auth_service does not already provide.
	"""
	# The DA Services roles the user holds (Frappe roles).
	da_roles = {r for r in frappe.get_roles(user_doc.name) if r in ROLE_CODES}

	if not da_roles:
		return "da_services", None

	return "da_services", {"roles": sorted(da_roles)}
