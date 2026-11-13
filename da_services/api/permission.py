"""Gate for the Desk apps screen entry registered in hooks.py."""

import frappe

from da_services.services import constants as C


def has_app_permission() -> bool:
	roles = set(frappe.get_roles())
	return bool(roles & (set(C.DA_SERVICES_ROLES) | C.UNRESTRICTED_ROLES))
