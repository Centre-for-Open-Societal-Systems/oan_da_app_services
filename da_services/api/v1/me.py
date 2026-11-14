"""v1: who am I, in DA Services terms.

Two surfaces share one implementation:

- ``GET /api/v1/da-services/me`` returns the caller's DA Services profile: the single role
  code the portal switches on, Region/Woreda scope and its footer label, bound DA-ID, the
  DA record from the registry (for a Development Agent) and the nav hrefs the role may see.
- The ``on_user_profile`` hook in ``api/v1/profile.py`` (PR #4) gives oan_auth_service's
  ``GET /api/v1/auth/me`` the roles and scope under ``data.profiles.da_services``; this
  endpoint adds the registry record and the menu on top.

The menu is a convenience for the UI, not an authorisation control: every endpoint
still enforces the scope rule itself.
"""

from __future__ import annotations

import frappe
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.integrations.da_registry.client import DARegistryAuthError, DARegistryError, get_client
from da_services.services import constants as C
from da_services.utils.permissions import ScopeContext, require_roles

# Single role code and label the portal switches on (frontend lib/rbac.ts ROLES / ROLE_LABELS).
ROLE_CODES: dict[str, str] = {
	C.ROLE_DA: "DA",
	C.ROLE_SUPERVISOR: "Supervisor",
	C.ROLE_EXECUTIVE: "Executive",
	C.ROLE_ADMIN: "Admin",
	C.ROLE_COMMS: "CommsOfficer",
}
ROLE_LABELS: dict[str, str] = {
	"DA": "Development Agent",
	"Supervisor": "Supervisor (Woreda)",
	"Executive": "Executive (Regional)",
	"Admin": "OAN / ATI Administrator",
	"CommsOfficer": "Communications Officer",
}
# When a user holds more than one role, the portal shows the widest one.
PRIMARY_ROLE_ORDER = ("Admin", "Supervisor", "Executive", "DA", "CommsOfficer")

# Sidebar items in the portal's order, with the role codes that see each one
# (frontend lib/rbac.ts NAV_GROUPS, FSD 2.4). Routes without a nav item (/profile, /sync,
# /visits/{id}/outcome, ...) are not listed; the portal reaches them from a page.
_ALL = ("DA", "Supervisor", "Executive", "Admin", "CommsOfficer")
_OFFICERS = ("Supervisor", "Executive", "Admin")
NAV: tuple[tuple[str, tuple[str, ...]], ...] = (
	("/dashboard", _ALL),
	("/farmers", ("DA", "Supervisor", "Executive")),
	("/beneficiaries", ("DA", "Supervisor", "Admin")),
	("/grievances", ("DA", "Supervisor", "Admin")),
	("/my-teams", ("DA",)),
	("/agents", _OFFICERS),
	("/visits", ("Supervisor",)),
	("/assignments", ("Supervisor",)),
	("/approvals", ("Admin",)),
	("/registry-sync", ("Admin",)),
	("/feedback", ("DA", "Supervisor", "Admin")),
	("/knowledge", _ALL),
	("/broadcast", ("DA", "CommsOfficer", "Admin")),
	("/alerts", ("CommsOfficer", "Admin", "Supervisor")),
	("/performance", ("DA", "Supervisor")),
	("/surveys", ("DA", "Supervisor", "Executive", "Admin")),
	("/admin/users", ("Admin",)),
	("/admin/reports", _OFFICERS),
	("/admin/settings", ("Admin",)),
)


def role_codes_for(ctx: ScopeContext) -> list[str]:
	if ctx.unrestricted:
		# System Manager / Administrator act as OAN Administrator here. Frappe's Administrator
		# also holds every role, which must not merge the DA and Comms menus into theirs.
		return ["Admin"]
	codes = {ROLE_CODES[r] for r in C.DA_SERVICES_ROLES if r in ctx.roles}
	return [c for c in PRIMARY_ROLE_ORDER if c in codes]


def menu_for(ctx: ScopeContext) -> list[str]:
	"""Nav hrefs for every role the user holds. A UI convenience, not an access control."""
	codes = set(role_codes_for(ctx))
	return [href for href, roles in NAV if codes.intersection(roles)]


def _scope_text(ctx: ScopeContext) -> str:
	# Codes for now; names come once the location master data is in (DA Services backlog).
	for label, values in (
		("Woreda", ctx.woreda_scopes),
		("Zone", ctx.zone_scopes),
		("Region", ctx.region_scopes),
	):
		if values:
			return f"{label} · {', '.join(sorted(values))}"
	return ""


def scope_label_for(role: str | None, ctx: ScopeContext, agent: dict | None = None) -> str:
	"""Sidebar footer line (frontend ROLE_SCOPES), e.g. "Woreda · ET04-W01"."""
	if role == "Admin":
		return "All regions"
	if role == "CommsOfficer":
		return "Broadcast domain"
	if role == "DA":
		where = (agent or {}).get("woreda") or next(iter(sorted(ctx.woreda_scopes)), None)
		return f"Own record · {where}" if where else "Own record"
	text = _scope_text(ctx)
	if role == "Executive":
		return f"{text} (read)" if text else "Read only"
	return text


def profile_for(ctx: ScopeContext, include_agent: bool = True) -> dict:
	codes = role_codes_for(ctx)
	role = codes[0] if codes else None
	user_row = (
		frappe.db.get_value("User", ctx.user, ["full_name", "da_registry_id"], as_dict=True)
		if ctx.user
		else None
	)
	full_name = user_row.full_name if user_row else None
	da_id = (user_row.da_registry_id if user_row else None) or None
	data: dict = {
		"user": {"id": ctx.user, "name": full_name or ctx.user, "email": ctx.user},
		"role": role,
		"role_label": ROLE_LABELS.get(role),
		"role_codes": codes,
		"roles": [r for r in C.DA_SERVICES_ROLES if r in ctx.roles],
		"unrestricted": ctx.unrestricted,
		"scope": {
			"regions": sorted(ctx.region_scopes),
			"zones": sorted(ctx.zone_scopes),
			"woredas": sorted(ctx.woreda_scopes),
		},
		"da_id": da_id,
		"menu": menu_for(ctx),
	}
	if include_agent and da_id:
		try:
			data["agent"] = get_client().get_da(da_id).to_dict()
		except DARegistryAuthError as exc:
			# Roles, scope and menu still come from DA Services; the registry's refusal
			# (expired token, or no registry role) is reported on the profile screen.
			data["agent"] = None
			data["agent_error"] = f"registry refused the token ({exc.http_status_code})"
		except DARegistryError as exc:
			data["agent"] = None
			data["agent_error"] = f"registry unavailable: {exc.__class__.__name__}"
	data["scope_label"] = scope_label_for(role, ctx, data.get("agent"))
	return data


@route("/me", methods=("GET",), summary="Current user's DA Services profile: roles, scope, DA-ID, menu")
@frappe.whitelist()
@handle_api_errors
def get_me():
	ctx = require_roles(*C.DA_SERVICES_ROLES, *C.UNRESTRICTED_ROLES)
	return success_response(data=profile_for(ctx))
