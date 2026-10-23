"""Fill the new `zone` column on rows created before Zone joined the scope model.

Order of truth, cheapest first: the Woreda's parent in `DA Administrative Area`; for a
DA Farmer Link, the cached `DA Farmer Reference`; finally the DA Registry record of the
row's DA. Rows that none of these can resolve stay empty and keep being denied to a
Zone-only grant, exactly as before this column existed.
"""

import frappe

from da_services.da_services.doctype.da_administrative_area.da_administrative_area import ancestors_of
from da_services.integrations.da_registry.client import DARegistryError, get_client

DOCTYPES = ("DA Visit", "DA Task", "DA Farmer Link", "DA Profile Change Request")
EMPTY = "ifnull(`zone`, '') = ''"


def execute():
	for doctype in DOCTYPES:
		_from_master_data(doctype)
	_from_farmer_reference()
	for doctype in DOCTYPES:
		_from_registry(doctype)


def _from_master_data(doctype: str) -> None:
	table = f"`tab{doctype}`"
	woredas = frappe.db.sql_list(
		f"select distinct `woreda` from {table} where {EMPTY} and ifnull(`woreda`, '') != ''"
	)
	for woreda in woredas:
		zone = ancestors_of(woreda).get("zone")
		if zone:
			frappe.db.sql(f"update {table} set `zone` = %s where `woreda` = %s and {EMPTY}", (zone, woreda))


def _from_farmer_reference() -> None:
	frappe.db.sql(
		"""update `tabDA Farmer Link` l
		join `tabDA Farmer Reference` r on r.`farmer_id` = l.`farmer_id`
		set l.`zone` = r.`zone`
		where ifnull(l.`zone`, '') = '' and ifnull(r.`zone`, '') != ''"""
	)


def _from_registry(doctype: str) -> None:
	table = f"`tab{doctype}`"
	da_ids = frappe.db.sql_list(
		f"select distinct `da_id` from {table} where {EMPTY} and ifnull(`da_id`, '') != ''"
	)
	if not da_ids:
		return
	client = get_client()
	for da_id in da_ids:
		try:
			zone = client.get_da(da_id).zone
		except DARegistryError:
			continue
		if zone:
			frappe.db.sql(f"update {table} set `zone` = %s where `da_id` = %s and {EMPTY}", (zone, da_id))
