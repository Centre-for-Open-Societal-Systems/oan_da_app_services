"""My Team: the Development Agents posted with the signed-in DA.

A DA's team is the other DAs of the same Kebele who have not separated. The DA Registry
is searched by the Kebele the DA is posted in; a DA with no Kebele on record gets the
DAs of the Woreda. It does not use a reporting hierarchy. The registry is the source of
truth; nothing is stored here.
"""

from __future__ import annotations

import frappe
from frappe import _

from da_services.integrations.da_registry.client import DANotFound, DARegistryUnavailable, get_client
from da_services.integrations.da_registry.schemas import DAReference
from da_services.services import constants as C
from da_services.utils.permissions import ScopeContext

FIELDS = (
	"da_id",
	"full_name",
	"specialisation",
	"education_tier",
	"phone",
	"kebele",
	"kebele_name",
	"woreda",
	"woreda_name",
	"active_status",
	"joined_at",
)


def _to_row(ref: DAReference) -> dict:
	return {f: getattr(ref, f, None) for f in FIELDS}


def list_my_team(ctx: ScopeContext) -> list[dict]:
	"""The caller's own DA record first, then the team by name."""
	da_id = next(iter(sorted(ctx.da_ids)), None)
	if not da_id:
		frappe.throw(_("Your account is not linked to a DA Registry record."), frappe.ValidationError)

	client = get_client()
	try:
		me = client.get_da(da_id)
		if me.kebele:
			posted = client.list_das(kebele=me.kebele)
		elif me.woreda:
			posted = client.list_das(woreda=me.woreda)
		else:
			posted = []
	except DANotFound:
		frappe.throw(_("DA {0} was not found in the registry.").format(da_id), frappe.DoesNotExistError)
	except DARegistryUnavailable:
		frappe.throw(_("The DA Registry is unavailable. Try again shortly."), frappe.ValidationError)

	team = [ref for ref in posted if ref.da_id != me.da_id and ref.active_status != C.ACTIVE_STATUS_SEPARATED]
	team.sort(key=lambda r: ((r.full_name or "").casefold(), r.da_id))
	return [_to_row(me), *(_to_row(ref) for ref in team)]
