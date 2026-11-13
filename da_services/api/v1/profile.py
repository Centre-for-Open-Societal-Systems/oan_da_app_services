"""Profile resolution hook for oan_auth_service's GET /api/v1/auth/me."""

import frappe

from da_services.services import constants as C
from da_services.utils.permissions import get_scope_context


def resolve_user_profile_hook(user_doc, roles=None) -> tuple[str, dict | None]:
	"""Hook subscriber for oan_auth_service's on_user_profile.

	Returns ("da_services", profile_data) namespaced under data["profiles"]["da_services"]
	containing DA-specific scope that auth_service does not already provide.
	"""
	ctx = get_scope_context(user_doc.name)
	da_roles = ctx.roles & set(C.DA_SERVICES_ROLES)

	if not da_roles:
		return "da_services", None

	return "da_services", {
		"roles": sorted(da_roles),
		"scope": {
			"regions": sorted(ctx.region_scopes),
			"zones": sorted(ctx.zone_scopes),
			"woredas": sorted(ctx.woreda_scopes),
			"da_ids": sorted(ctx.da_ids),
		},
	}
