"""Administrative-area filters shared by list APIs."""

from __future__ import annotations

import frappe
from frappe import _

from da_services.da_services.doctype.da_administrative_area.da_administrative_area import ancestors_of
from da_services.services import constants as C
from da_services.utils.permissions import ScopeContext

AREA_DOCTYPE = "DA Administrative Area"
LEVELS = ("Region", "Zone", "Woreda", "Kebele")


def _code(value: str | None) -> str | None:
	return (value or "").strip().upper() or None


def _area_row(code: str, label: str) -> dict:
	row = frappe.db.get_value(
		AREA_DOCTYPE,
		code,
		["name", "level", "parent_area", "active", "name_en"],
		as_dict=True,
	)
	if not row:
		frappe.throw(
			_("{0} {1} was not found in the administrative areas.").format(label, code),
			frappe.ValidationError,
		)
	if not row.active:
		frappe.throw(
			_("{0} {1} is no longer active.").format(label, code),
			frappe.ValidationError,
		)
	return row


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
			_("{0} {1} does not match the selected administrative area.").format(label, supplied),
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
	"""Validate a selected area and return canonical filters it implies.

	Unknown or inactive areas are validation errors. A caller may only narrow to an
	area inside their live scope: selecting another Woreda/Kebele is a permission
	error, not an empty result. A DA may select a Woreda only through their Woreda
	grant and a Kebele only when it is one of their Effective Kebele assignments.
	"""
	region = _code(region)
	zone = _code(zone)
	woreda = _code(woreda)
	kebele = _code(kebele)

	resolved = {"region": None, "zone": None, "woreda": None, "kebele": None}
	selected_level = None
	selected_code = None
	selected_name_en = None

	if kebele:
		row = _area_row(kebele, _("Kebele"))
		if row.level != "Kebele":
			frappe.throw(_("{0} is not a Kebele.").format(row.name), frappe.ValidationError)
		resolved.update(ancestors_of(row.name))
		selected_level, selected_code = "kebele", row.name
		selected_name_en = row.name_en
	elif woreda:
		row = _area_row(woreda, _("Woreda"))
		if row.level != "Woreda":
			frappe.throw(_("{0} is not a Woreda.").format(row.name), frappe.ValidationError)
		resolved.update(ancestors_of(row.name))
		selected_level, selected_code = "woreda", row.name
	elif zone:
		row = _area_row(zone, _("Zone"))
		if row.level != "Zone":
			frappe.throw(_("{0} is not a Zone.").format(row.name), frappe.ValidationError)
		resolved.update(ancestors_of(row.name))
		selected_level, selected_code = "zone", row.name
	elif region:
		row = _area_row(region, _("Region"))
		if row.level != "Region":
			frappe.throw(_("{0} is not a Region.").format(row.name), frappe.ValidationError)
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
		if selected_name_en and selected_name_en not in values:
			values.append(selected_name_en)
		out["kebele"] = ("in", values) if len(values) > 1 else selected_code
	return out
