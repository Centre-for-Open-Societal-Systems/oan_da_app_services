"""Read a DA from the registry (not scoped)."""

import frappe
from frappe import _

from da_services.integrations.da_registry.client import DANotFound, DARegistryUnavailable, get_client
from da_services.integrations.da_registry.schemas import DAReference
from da_services.services import constants as C


def get_da_reference(da_id: str) -> DAReference:
	da_id = (da_id or "").strip()
	if not da_id:
		frappe.throw(_("da_id is required"), frappe.ValidationError)

	try:
		ref = get_client().get_da(da_id)
	except DANotFound:
		frappe.throw(_("DA {0} was not found in the registry.").format(da_id), frappe.DoesNotExistError)
	except DARegistryUnavailable:
		frappe.throw(_("The DA Registry is unavailable. Try again shortly."), frappe.ValidationError)
	return ref
