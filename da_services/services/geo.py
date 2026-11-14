"""Thin reads against DA Country / Region / Zone / Woreda / Kebele (geo_master_sync).

Prefer Frappe Link fields, fetch_from, User Permissions, and get_all filters elsewhere.
Use this module only for repeated parent-chain and display-name lookups.
"""

from __future__ import annotations

import frappe
from frappe import _

LEVEL_DOCTYPE = {
	"region": "DA Region",
	"zone": "DA Zone",
	"woreda": "DA Woreda",
	"kebele": "DA Kebele",
}

NAME_FIELD = {
	"DA Region": "region_name",
	"DA Zone": "zone_name",
	"DA Woreda": "woreda_name",
	"DA Kebele": "kebele_name",
}

PARENT_FIELD = {
	"DA Zone": "region",
	"DA Woreda": "zone",
	"DA Kebele": "woreda",
}


def user_for_da_id(da_id: str | None) -> str | None:
	"""User name bound to a DA Registry id via User.da_registry_id."""
	if not da_id:
		return None
	return frappe.db.get_value("User", {"da_registry_id": da_id}, "name")


def require_user_for_da(da_id: str) -> str:
	user = user_for_da_id(da_id)
	if not user:
		frappe.throw(
			_("No User is bound to DA-ID {0} (set User.da_registry_id).").format(da_id),
			frappe.ValidationError,
		)
	return user


def display_name(doctype: str, name: str | None) -> str | None:
	"""Human label for a master row; None when unknown."""
	if not name:
		return None
	field = NAME_FIELD.get(doctype)
	if not field:
		return None
	return frappe.db.get_value(doctype, name, field)


def display_names(doctype: str, names: set[str] | list[str]) -> dict[str, str]:
	names = {n for n in names if n}
	if not names:
		return {}
	field = NAME_FIELD.get(doctype)
	if not field:
		return {}
	rows = frappe.get_all(doctype, filters={"name": ("in", list(names))}, fields=["name", field])
	return {r.name: r.get(field) for r in rows if r.get(field)}


def ancestors_of_kebele(kebele: str) -> dict:
	"""region / zone / woreda / kebele from Link parents."""
	woreda = frappe.db.get_value("DA Kebele", kebele, "woreda")
	zone = frappe.db.get_value("DA Woreda", woreda, "zone") if woreda else None
	region = frappe.db.get_value("DA Zone", zone, "region") if zone else None
	return {"kebele": kebele, "woreda": woreda, "zone": zone, "region": region}


def ancestors_of_woreda(woreda: str) -> dict:
	zone = frappe.db.get_value("DA Woreda", woreda, "zone")
	region = frappe.db.get_value("DA Zone", zone, "region") if zone else None
	return {"kebele": None, "woreda": woreda, "zone": zone, "region": region}


def ancestors_of_zone(zone: str) -> dict:
	region = frappe.db.get_value("DA Zone", zone, "region")
	return {"kebele": None, "woreda": None, "zone": zone, "region": region}


def require_active_master(doctype: str, name: str | None, label: str) -> dict:
	"""Active master row or ValidationError."""
	name = (name or "").strip() or None
	if not name:
		frappe.throw(_("{0} is required.").format(label), frappe.ValidationError)
	fields = ["name", "is_active", NAME_FIELD[doctype]]
	row = frappe.db.get_value(doctype, name, fields, as_dict=True)
	if not row:
		frappe.throw(
			_("{0} {1} was not found in the geography masters. Run geo_master_sync.").format(label, name),
			frappe.ValidationError,
		)
	if not row.is_active:
		frappe.throw(_("{0} {1} is no longer active.").format(label, name), frappe.ValidationError)
	return row


def link_if_exists(doctype: str, name: str | None) -> str | None:
	"""Return name when an active master row exists; else None (do not invent)."""
	if not name:
		return None
	active = frappe.db.get_value(doctype, name, "is_active")
	return name if active else None


def kebeles_for_woreda(woreda: str) -> list[dict]:
	return frappe.get_all(
		"DA Kebele",
		filters={"woreda": woreda, "is_active": 1},
		fields=["name as code", "kebele_name as name_en", "woreda"],
		order_by="kebele_name asc",
		limit_page_length=0,
	)


def list_masters(
	*,
	level: str | None = None,
	parent: str | None = None,
	q: str | None = None,
) -> list[dict]:
	"""Cascading master list for assignment UI (Region ΓåÆ Kebele)."""
	level_key = (level or "").strip().lower() or None
	if not level_key or level_key not in LEVEL_DOCTYPE:
		frappe.throw(_("level must be Region, Zone, Woreda or Kebele."), frappe.ValidationError)
	doctype = LEVEL_DOCTYPE[level_key]
	name_field = NAME_FIELD[doctype]
	filters: dict = {"is_active": 1}
	parent_field = PARENT_FIELD.get(doctype)
	if parent:
		if not parent_field:
			frappe.throw(_("Region has no parent filter."), frappe.ValidationError)
		filters[parent_field] = parent
	if q:
		needle = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")[:80]
		filters[name_field] = ("like", f"%{needle}%")
	fields = ["name as code", f"{name_field} as name_en"]
	if parent_field:
		fields.append(parent_field)
	rows = frappe.get_all(
		doctype,
		filters=filters,
		fields=fields,
		order_by=f"{name_field} asc",
		limit_page_length=0,
	)
	out = []
	for r in rows:
		item = {
			"code": r.code,
			"level": level_key.title(),
			"parent_area": r.get(parent_field) if parent_field else None,
			"name_en": r.name_en,
			"name_am": None,
			"boundary_ref": None,
		}
		out.append(item)
	return out


def masters_nonempty() -> bool:
	"""True when at least one active row exists at every geo level used by Links."""
	for doctype in ("DA Region", "DA Zone", "DA Woreda", "DA Kebele"):
		if not frappe.db.exists(doctype, {"is_active": 1}):
			return False
	return True
