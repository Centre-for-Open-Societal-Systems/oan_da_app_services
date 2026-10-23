"""Experts / My Team: the specialists a DA can reach for advice (T3).

An "expert" is an active Development Agent with a specialisation, read from the DA
Registry inside the caller's scope. The registry is the source of truth (mock until
the DA Registry contract lands); nothing is cached here.

Scope (deny, never widen): a Woreda grant lists that Woreda, a Zone grant the Woredas
in that Zone, a Region grant the Region only when no narrower grant exists, Admin
everything. The caller's own DA record is never listed.
"""

from __future__ import annotations

import frappe
from frappe import _

from da_services.integrations.da_registry.client import DARegistryUnavailable, get_client
from da_services.integrations.da_registry.schemas import DAReference
from da_services.services import constants as C
from da_services.utils.permissions import ScopeContext

FIELDS = (
	"da_id",
	"full_name",
	"specialisation",
	"phone",
	"email",
	"language",
	"kebele",
	"woreda",
	"zone",
	"region",
	"active_status",
)


def _scoped_das(ctx: ScopeContext) -> list[DAReference]:
	"""Registry DAs inside the caller's scope. Narrowest grant wins."""
	client = get_client()
	try:
		if ctx.unrestricted:
			return client.list_das()
		if ctx.woreda_scopes:
			rows: list[DAReference] = []
			for woreda in sorted(ctx.woreda_scopes):
				rows.extend(client.list_das(woreda=woreda))
			return rows
		if ctx.zone_scopes:
			# The registry has no zone filter: fetch the Region(s) and keep the Zone(s).
			regions = sorted(ctx.region_scopes) if ctx.region_scopes else [None]
			rows = []
			for region in regions:
				rows.extend(r for r in client.list_das(region=region) if r.zone in ctx.zone_scopes)
			return rows
		if ctx.region_scopes:
			rows = []
			for region in sorted(ctx.region_scopes):
				rows.extend(client.list_das(region=region))
			return rows
	except DARegistryUnavailable:
		frappe.throw(_("The DA Registry is unavailable. Try again shortly."), frappe.ValidationError)
	return []


def _to_row(ref: DAReference) -> dict:
	return {f: getattr(ref, f, None) for f in FIELDS}


def list_experts(
	ctx: ScopeContext,
	*,
	specialisation: str | None = None,
	q: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], dict]:
	"""Experts visible to the caller: active, with a specialisation, not the caller.

	Returns (rows, meta) where meta carries total, paging and
	by_specialisation counts over the full (unpaged) result.
	"""
	seen: set[str] = set()
	experts: list[DAReference] = []
	for ref in _scoped_das(ctx):
		if ref.da_id in seen or ref.da_id in ctx.da_ids:
			continue
		seen.add(ref.da_id)
		if ref.active_status != C.ACTIVE_STATUS_ACTIVE or not (ref.specialisation or "").strip():
			continue
		experts.append(ref)

	if specialisation:
		want = specialisation.strip().lower()
		experts = [r for r in experts if (r.specialisation or "").strip().lower() == want]

	if q:
		needle = q.strip().lower()
		experts = [
			r
			for r in experts
			if needle in (r.full_name or "").lower()
			or needle in (r.specialisation or "").lower()
			or needle in r.da_id.lower()
		]

	experts.sort(key=lambda r: ((r.specialisation or "").lower(), (r.full_name or "").lower(), r.da_id))

	by_specialisation: dict[str, int] = {}
	for r in experts:
		by_specialisation[r.specialisation] = by_specialisation.get(r.specialisation, 0) + 1

	total = len(experts)
	page = experts[start : start + limit]
	meta = {
		"total": total,
		"limit": limit,
		"start": start,
		"by_specialisation": dict(sorted(by_specialisation.items())),
	}
	return [_to_row(r) for r in page], meta
