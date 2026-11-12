"""Fill Crop Sown list/tab defaults on DA Services Settings when blank.

DocType field defaults apply on new Singles only. This copies those same
meta defaults into an existing Single after migrate.
"""

from __future__ import annotations

import frappe


def _field_default(fieldname: str) -> str:
	field = frappe.get_meta("DA Services Settings").get_field(fieldname)
	return str(field.default).strip() if field and field.default else ""


def execute():
	if not frappe.db.exists("DocType", "DA Services Settings"):
		return
	for fieldname in ("crop_list_sources", "crop_register_tab_ids"):
		if (frappe.db.get_single_value("DA Services Settings", fieldname) or "").strip():
			continue
		default = _field_default(fieldname)
		if default:
			frappe.db.set_single_value("DA Services Settings", fieldname, default)
