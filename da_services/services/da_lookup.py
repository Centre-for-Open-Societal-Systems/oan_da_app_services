"""Read a DA from the registry inside the caller's scope.

Kept out of the API layer so the scope rule can be tested directly (it raises) while
the endpoint wraps the result in the response envelope.
"""

import frappe
from frappe import _

from da_services.integrations.da_registry.client import DANotFound, DARegistryUnavailable, get_client
from da_services.integrations.da_registry.schemas import DAReference
from da_services.services import constants as C
from da_services.utils.permissions import ScopeContext, require_da_access, require_roles


def get_da_reference(da_id: str, ctx: ScopeContext | None = None) -> DAReference:
	ctx = ctx or require_roles(*C.DA_READ_ROLES)
	da_id = (da_id or "").strip()
	if not da_id:
		frappe.throw(_("da_id is required"), frappe.ValidationError)

	try:
		ref = get_client().get_da(da_id)
	except DANotFound:
		frappe.throw(_("DA {0} was not found in the registry.").format(da_id), frappe.DoesNotExistError)
	except DARegistryUnavailable:
		frappe.throw(_("The DA Registry is unavailable. Try again shortly."), frappe.ValidationError)

	# Scope is checked against the registry's own Region / Woreda for this DA, never
	# against anything the caller supplied.
	require_da_access(ref.da_id, ref.region, ref.woreda, ctx, zone=ref.zone)
	return ref
