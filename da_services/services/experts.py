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

from da_services.da_services.doctype.da_administrative_area.da_administrative_area import ancestors_of
from da_services.integrations.da_registry.client import DARegistryUnavailable, get_client
from da_services.integrations.da_registry.schemas import DAReference
from da_services.services import constants as C

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


def _scoped_das() -> list[DAReference]:
	"""Registry DAs (not scoped: restricting them is for later)."""
	try:
		return get_client().list_das()
	except DARegistryUnavailable:
		frappe.throw(_("The DA Registry is unavailable. Try again shortly."), frappe.ValidationError)


def _to_row(ref: DAReference) -> dict:
	return {f: getattr(ref, f, None) for f in FIELDS}


def list_experts(
	*,
	specialisation: str | None = None,
	q: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], dict]:
	"""Experts visible to the caller: active, with a specialisation.

	Returns (rows, meta) where meta carries total, paging and
	by_specialisation counts over the full (unpaged) result.
	"""
	seen: set[str] = set()
	experts: list[DAReference] = []
	for ref in _scoped_das():
		if ref.da_id in seen:
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

	experts.sort(key=lambda r: ((r.specialisation or "").casefold(), (r.full_name or "").casefold(), r.da_id))

	# Group case-insensitively ("Crop production" and "crop PRODUCTION" are one
	# bucket); the label shown is the first spelling seen in sort order.
	by_specialisation: dict[str, int] = {}
	labels: dict[str, str] = {}
	for r in experts:
		spec = (r.specialisation or "").strip()
		key = spec.casefold()
		labels.setdefault(key, spec)
		by_specialisation[key] = by_specialisation.get(key, 0) + 1

	total = len(experts)
	page = experts[start : start + limit]
	meta = {
		"total": total,
		"limit": limit,
		"start": start,
		"by_specialisation": {labels[k]: by_specialisation[k] for k in sorted(by_specialisation)},
	}
	return [_to_row(r) for r in page], meta
