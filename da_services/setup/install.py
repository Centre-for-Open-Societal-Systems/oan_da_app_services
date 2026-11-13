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


def after_install():
	seed_roles()


def after_migrate():
	seed_roles()


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
