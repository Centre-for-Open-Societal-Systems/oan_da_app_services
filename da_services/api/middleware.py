"""JWT auth_hook configuration for this deployment.

The validation logic itself lives in oan_auth_service. This module exists to bind
it to da_services' own API namespace, exempt paths and revocation rule,
so the shared library never needs to know this app exists.
"""

import frappe

from da_services.services import constants as C

API_NAMESPACE = "/api/method/da_services."

EXEMPT_PATHS: list[str] = []


def check_da_assignment(user: str) -> str | None:
	"""Deny-by-default: reject authenticated users without an active DA RBAC Assignment.

	Returns a rejection reason string (consumed by oan_auth_service's middleware as a 401)
	or None to allow the request through.
	"""
	roles = set(frappe.get_roles(user))
	if roles & C.UNRESTRICTED_ROLES:
		return None
	if not (roles & set(C.DA_SERVICES_ROLES)):
		return "User holds no DA Services role"
	from da_services.utils.permissions import active_assignments

	if not active_assignments(user):
		return "No active DA RBAC assignment"
	return None


def register():
	"""Register the da_services RPC namespace with oan_auth_service."""
	try:
		from oan_auth_service.api.middleware import register_namespace

		register_namespace(API_NAMESPACE, EXEMPT_PATHS, revocation_check=check_da_assignment)
	except ImportError:
		pass


register()
