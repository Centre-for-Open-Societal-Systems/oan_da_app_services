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
from da_services.da_services.doctype.da_portal_menu_item.da_portal_menu_item import cached_menu
from da_services.services import constants as C
from da_services.services import geo

# Single role code the portal switches on (frontend lib/rbac.ts ROLES).
ROLE_CODES: dict[str, str] = {
	C.ROLE_DA: "DA",
	C.ROLE_SUPERVISOR: "Supervisor",
	C.ROLE_EXECUTIVE: "Executive",
	C.ROLE_ADMIN: "Admin",
	C.ROLE_COMMS: "CommsOfficer",
}
# When a user holds more than one role, the portal shows the widest one.
PRIMARY_ROLE_ORDER = ("Admin", "Supervisor", "Executive", "DA", "CommsOfficer")


def role_codes_for() -> list[str]:
	codes = {ROLE_CODES[r] for r in frappe.get_roles() if r in ROLE_CODES}
	return [c for c in PRIMARY_ROLE_ORDER if c in codes]


def menu_for() -> list[str]:
	"""Nav hrefs (DA Portal Menu Item) for every role the user holds. A UI convenience, not
	an access control."""
	roles = set(frappe.get_roles())
	return [route for route, allowed in cached_menu() if roles & allowed]


# DA Hierarchy Link field -> geo master doctype, top level first.
HIERARCHY_GEO_FIELDS = (
	("country", "DA Country"),
	("region", "DA Region"),
	("zone", "DA Zone"),
	("woreda", "DA Woreda"),
	("kebele", "DA Kebele"),
)


def hierarchy_for(user: str) -> tuple[str | None, dict]:
	"""(designation, location) from the user's DA Hierarchy node. Each location level is
	{"id", "name"}, or None when the node does not set it (or the user has no node)."""
	row = frappe.db.get_value(
		"DA Hierarchy",
		{"user": user},
		["designation", *(field for field, _doctype in HIERARCHY_GEO_FIELDS)],
		as_dict=True,
	)
	location: dict = {}
	for field, doctype in HIERARCHY_GEO_FIELDS:
		code = row.get(field) if row else None
		location[field] = (
			{"id": code, "name": geo.display_names(doctype, [code]).get(code) or code} if code else None
		)
	return (row.designation if row else None), location


def profile_for() -> dict:
	codes = role_codes_for()
	role = codes[0] if codes else None
	full_name = frappe.db.get_value("User", frappe.session.user, "full_name") if frappe.session.user else None
	designation, location = hierarchy_for(frappe.session.user)
	return {
		"user": {
			"id": frappe.session.user,
			"name": full_name or frappe.session.user,
			"email": frappe.session.user,
		},
		"role": role,
		"designation": designation,
		**location,  # country, region, zone, woreda, kebele: {"id", "name"} or None
		"roles": sorted(r for r in frappe.get_roles() if r in ROLE_CODES),
		"menu": menu_for(),
	}


@route("/me", methods=("GET",), summary="Current user's DA Services profile: roles, menu")
@frappe.whitelist()
@handle_api_errors
def get_me():
	return success_response(data=profile_for())
