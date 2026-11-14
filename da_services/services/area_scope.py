"""Geography filters shared by list APIs (DA Region / Zone / Woreda / Kebele masters)."""

from __future__ import annotations

import frappe
from frappe import _

from da_services.services import constants as C
from da_services.services import geo
from da_services.utils.permissions import ScopeContext


def _code(value: str | None) -> str | None:
	return (value or "").strip() or None


def _assigned_kebeles(da_ids) -> set[str]:
	if not da_ids:
		return set()
	return set(
		frappe.get_all(
			"DA Kebele Assignment",
			filters={"da_id": ("in", list(da_ids)), "state": "Effective"},
			pluck="kebele",
		)
	)


def _check_supplied(supplied: str | None, resolved: str | None, label: str) -> None:
	if supplied and supplied != resolved:
		frappe.throw(
			_("{0} {1} does not match the selected geography.").format(label, supplied),
			frappe.ValidationError,
		)


def resolve_area_filters(
	ctx: ScopeContext,
	*,
	region: str | None = None,
	zone: str | None = None,
	woreda: str | None = None,
	kebele: str | None = None,
) -> dict:
	"""Validate a selected area against geo masters and return canonical filters.

	Unknown or inactive masters are validation errors. A caller may only narrow to an
	area inside their live scope. A DA may select a Kebele only when it is one of their
	Effective Kebele assignments.
	"""
	region = _code(region)
	zone = _code(zone)
	woreda = _code(woreda)
	kebele = _code(kebele)

	resolved = {"region": None, "zone": None, "woreda": None, "kebele": None}
	selected_level = None
	selected_code = None
	selected_name = None

	if kebele:
		row = geo.require_active_master("DA Kebele", kebele, _("Kebele"))
		resolved.update(geo.ancestors_of_kebele(row.name))
		selected_level, selected_code = "kebele", row.name
		selected_name = row.kebele_name
	elif woreda:
		row = geo.require_active_master("DA Woreda", woreda, _("Woreda"))
		resolved.update(geo.ancestors_of_woreda(row.name))
		selected_level, selected_code = "woreda", row.name
	elif zone:
		row = geo.require_active_master("DA Zone", zone, _("Zone"))
		resolved.update(geo.ancestors_of_zone(row.name))
		selected_level, selected_code = "zone", row.name
	elif region:
		row = geo.require_active_master("DA Region", region, _("Region"))
		resolved["region"] = row.name
		selected_level, selected_code = "region", row.name

	if region:
		_check_supplied(region, resolved["region"], _("Region"))
	if zone:
		_check_supplied(zone, resolved["zone"], _("Zone"))
	if woreda:
		_check_supplied(woreda, resolved["woreda"], _("Woreda"))
	if kebele:
		_check_supplied(kebele, resolved["kebele"], _("Kebele"))

	if not selected_level:
		return {}

	if not ctx.unrestricted:
		if not ctx.covers(resolved["region"], resolved["woreda"], resolved["zone"]):
			frappe.throw(
				_("{0} {1} is outside your scope.").format(_(selected_level.title()), selected_code),
				frappe.PermissionError,
			)
		if (
			selected_level == "kebele"
			and ctx.has_any_role(C.ROLE_DA)
			and selected_code not in _assigned_kebeles(ctx.da_ids)
		):
			frappe.throw(
				_("Kebele {0} is not in your assigned Kebeles.").format(selected_code),
				frappe.PermissionError,
			)

	out = {key: value for key, value in resolved.items() if value}
	if selected_level == "kebele":
		values = [selected_code]
		if selected_name and selected_name not in values:
			values.append(selected_name)
		out["kebele"] = ("in", values) if len(values) > 1 else selected_code
	return out
