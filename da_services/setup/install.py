"""Seed data created on install and refreshed on migrate.

Only configuration the specification names explicitly lives here, so a fresh site is
usable rather than empty. Everything is idempotent: migrate runs it again on every
deploy and must never duplicate or clobber operator changes.
"""

import frappe

from da_services.services import constants as C

# (role name, desk_access). The DA works from the mobile app and the v1 API, so Desk is
# off for that role; widening it later is a one-line change but should be a deliberate
# decision because it exposes every doctype the role holds a DocPerm on.
ROLES = [
	(C.ROLE_DA, 0),
	(C.ROLE_SUPERVISOR, 1),
	(C.ROLE_EXECUTIVE, 1),
	(C.ROLE_ADMIN, 1),
	(C.ROLE_COMMS, 1),
]


# Portal sidebar (frontend lib/rbac.ts NAV_GROUPS, FSD 2.4): (route, label, roles), in
# order. Seeded into DA Portal Menu Item; admins edit the records after that.
_ALL = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN, C.ROLE_COMMS)
_OFFICERS = (C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)
PORTAL_MENU = (
	("/dashboard", "Dashboard", _ALL),
	("/farmers", "Farmers", (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE)),
	("/beneficiaries", "Beneficiaries", (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_ADMIN)),
	("/grievances", "Grievances", (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_ADMIN)),
	("/my-teams", "My Teams", (C.ROLE_DA,)),
	("/agents", "Agents", _OFFICERS),
	("/visits", "Visits", (C.ROLE_SUPERVISOR,)),
	("/assignments", "Assignments", (C.ROLE_SUPERVISOR,)),
	# Level 1 (the Woreda's Supervisor) and level 2 (the OAN Administrator) of a profile change
	("/approvals", "Approvals", (C.ROLE_SUPERVISOR, C.ROLE_ADMIN)),
	("/registry-sync", "Registry Sync", (C.ROLE_ADMIN,)),
	("/feedback", "Feedback", (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_ADMIN)),
	("/knowledge", "Knowledge", _ALL),
	("/broadcast", "Broadcast", (C.ROLE_DA, C.ROLE_COMMS, C.ROLE_ADMIN)),
	("/alerts", "Alerts", (C.ROLE_COMMS, C.ROLE_ADMIN, C.ROLE_SUPERVISOR)),
	("/performance", "Performance", (C.ROLE_DA, C.ROLE_SUPERVISOR)),
	("/surveys", "Surveys", (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)),
	("/admin/users", "Users", (C.ROLE_ADMIN,)),
	("/admin/reports", "Reports", _OFFICERS),
	("/admin/settings", "Settings", (C.ROLE_ADMIN,)),
)


def after_install():
	seed_roles()
	seed_not_assigned_kebele()
	seed_portal_menu()
	seed_profile_change_workflow()


def after_migrate():
	seed_roles()
	seed_not_assigned_kebele()
	seed_portal_menu()
	seed_profile_change_workflow()


def seed_profile_change_workflow():
	"""Create the Workflow of DA Profile Change Request when the site has none.

	A Workflow that exists is left as it is, so changes made in Desk survive migrate.
	"""
	from da_services.services.profile_lists import pin_open_requests
	from da_services.workflows.profile_change import ensure_workflow

	ensure_workflow()
	# Open certificate and photo requests from before requests recorded which file they were
	# sent with: once pinned, nothing more is done here.
	pin_open_requests()


def seed_portal_menu():
	"""Create the portal menu items that are missing (matched by route).

	An existing item is left as it is, so changes made in Desk survive migrate.
	"""
	existing = set(frappe.get_all("DA Portal Menu Item", pluck="route"))
	for sequence, (route, label, roles) in enumerate(PORTAL_MENU, start=1):
		if route in existing:
			continue
		frappe.get_doc(
			{
				"doctype": "DA Portal Menu Item",
				"route": route,
				"label": label,
				"sequence": sequence * 10,
				"enabled": 1,
				"roles": [{"role": role} for role in roles],
			}
		).insert(ignore_permissions=True)


def seed_not_assigned_kebele():
	"""Create the "Not Assigned" DA Kebele when it is missing.

	It has no Woreda, so it is inserted without the mandatory check. It is inactive:
	the geo master sync only deactivates active rows Master Data does not list, so an
	inactive row is left alone, and active-only pickers do not offer it.
	"""
	from da_services.services.constants import NOT_ASSIGNED_KEBELE

	if frappe.db.exists("DA Kebele", NOT_ASSIGNED_KEBELE):
		return
	frappe.get_doc(
		{
			"doctype": "DA Kebele",
			"kebele_code": NOT_ASSIGNED_KEBELE,
			"kebele_name": NOT_ASSIGNED_KEBELE,
			"is_active": 0,
		}
	).insert(ignore_permissions=True, ignore_mandatory=True)


def before_tests():
	"""Disable Frappe's user-creation throttle for this process.

	``User.before_insert`` rejects the 61st User created in a 60-minute window.
	The suite inserts one user per role fixture. On a fresh CI database, and on
	any rerun inside that window, later tests die in ``setUp`` with
	``ValidationError: Throttled``.

	``frappe.init`` reloads site config from disk, so writing
	``frappe.conf.throttle_user_limit`` does not survive the first test. Replace
	the checker itself for the rest of the process.
	"""
	from frappe.core.doctype.user import user as user_module

	user_module.throttle_user_creation = lambda: None


def seed_roles():
	"""Create the five DA Services roles if missing and enforce their Desk access.

	Another app on the bench may have created a role with the same name first
	(oan_auth_service seeds roles too, with Frappe's default desk_access = 1), so the
	spec-defined desk_access is applied to existing rows as well; nothing else on an
	existing role is touched.
	"""
	for role_name, desk_access in ROLES:
		if frappe.db.exists("Role", role_name):
			if frappe.db.get_value("Role", role_name, "desk_access") != desk_access:
				frappe.db.set_value("Role", role_name, "desk_access", desk_access, update_modified=False)
			continue
		role = frappe.new_doc("Role")
		role.role_name = role_name
		role.desk_access = desk_access
		role.is_custom = 0
		role.insert(ignore_permissions=True)
	frappe.db.commit()
