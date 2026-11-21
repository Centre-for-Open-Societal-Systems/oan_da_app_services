"""v1: who am I, in DA Services terms.

Two surfaces share one implementation:

- ``GET /api/v1/da-services/me`` returns the caller's DA Services profile: the single role
  code the portal switches on, its footer label, the Kebeles the
  caller is posted to and the nav hrefs the role may see.
- The ``on_user_profile`` hook in ``api/v1/profile.py`` (PR #4) gives oan_auth_service's
  ``GET /api/v1/auth/me`` the roles under ``data.profiles.da_services``; this
  endpoint adds the Kebeles and the menu on top.

The menu is a convenience for the UI, not an authorisation control: every endpoint
still enforces the scope rule itself.
"""

from __future__ import annotations

import frappe
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.services import constants as C
from da_services.services.notifications import own_kebeles

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
	# Level 1 (the Woreda's Supervisor) and level 2 (the OAN Administrator) of a profile change
	("/approvals", ("Supervisor", "Admin")),
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


def role_codes_for() -> list[str]:
	codes = {ROLE_CODES[r] for r in frappe.get_roles() if r in ROLE_CODES}
	return [c for c in PRIMARY_ROLE_ORDER if c in codes]


def menu_for() -> list[str]:
	"""Nav hrefs for every role the user holds. A UI convenience, not an access control."""
	codes = set(role_codes_for())
	return [href for href, roles in NAV if codes.intersection(roles)]


def scope_label_for(role: str | None) -> str:
	"""Sidebar footer line (frontend ROLE_SCOPES), e.g. "All regions"."""
	if role == "Admin":
		return "All regions"
	if role == "CommsOfficer":
		return "Broadcast domain"
	if role == "DA":
		return "Own record"
	if role == "Executive":
		return "Read only"
	return ""


def profile_for() -> dict:
	codes = role_codes_for()
	role = codes[0] if codes else None
	full_name = frappe.db.get_value("User", frappe.session.user, "full_name") if frappe.session.user else None
	data: dict = {
		"user": {
			"id": frappe.session.user,
			"name": full_name or frappe.session.user,
			"email": frappe.session.user,
		},
		"role": role,
		"role_label": ROLE_LABELS.get(role),
		"role_codes": codes,
		"roles": sorted(r for r in frappe.get_roles() if r in ROLE_CODES),
		"menu": menu_for(),
	}
	if kebeles := own_kebeles():
		# The Kebeles the caller is posted to (Effective assignments).
		names = dict(
			frappe.get_all(
				"DA Kebele", filters={"name": ("in", kebeles)}, fields=["name", "kebele_name"], as_list=True
			)
		)
		data["kebeles"] = [{"code": c, "name": names.get(c) or c} for c in kebeles]
	data["scope_label"] = scope_label_for(role)
	return data


@route("/me", methods=("GET",), summary="Current user's DA Services profile: roles, menu")
@frappe.whitelist()
@handle_api_errors
def get_me():
	return success_response(data=profile_for())
