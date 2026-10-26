"""Fill the new `zone` column on rows created before Zone joined the scope model.

Order of truth, cheapest first: the Woreda's parent in `DA Administrative Area`; for a
DA Farmer Link, the cached `DA Farmer Reference`; finally the DA Registry record of the
row's DA. Rows that none of these can resolve stay empty and keep being denied to a
Zone-only grant, exactly as before this column existed.

The whole backfill is one transaction: either every row that can be resolved is
updated, or nothing is (a half-filled column would make Zone grants see an
arbitrary subset). Progress and registry failures go to the migrate log so an
operator can see what was left empty and why.
"""

import frappe

from da_services.da_services.doctype.da_administrative_area.da_administrative_area import ancestors_of
from da_services.integrations.da_registry.client import DARegistryError, get_client

DOCTYPES = ("DA Visit", "DA Task", "DA Farmer Link", "DA Profile Change Request")
EMPTY = "ifnull(`zone`, '') = ''"


def execute():
	frappe.db.begin()
	try:
		for doctype in DOCTYPES:
			_from_master_data(doctype)
		_from_farmer_reference()
		for doctype in DOCTYPES:
			_from_registry(doctype)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		raise
	for doctype in DOCTYPES:
		left = frappe.db.sql(f"select count(*) from `tab{doctype}` where {EMPTY}")[0][0]
		if left:
			_log(f"{doctype}: {left} row(s) still have no zone (unknown Woreda and no registry answer)")


def _log(message: str) -> None:
	print(f"[backfill_zone] {message}")  # migrate output is the operator's log


def _rowcount() -> int:
	"""Rows touched by the last statement (frappe.db.sql returns () for UPDATE)."""
	return int(getattr(frappe.db._cursor, "rowcount", 0) or 0)


def _from_master_data(doctype: str) -> None:
	table = f"`tab{doctype}`"
	woredas = frappe.db.sql_list(
		f"select distinct `woreda` from {table} where {EMPTY} and ifnull(`woreda`, '') != ''"
	)
	updated = unresolved = 0
	for woreda in woredas:
		zone = ancestors_of(woreda).get("zone")
		if not zone:
			unresolved += 1
			continue
		frappe.db.sql(f"update {table} set `zone` = %s where `woreda` = %s and {EMPTY}", (zone, woreda))
		updated += _rowcount()
	_log(
		f"{doctype}: master data resolved {len(woredas) - unresolved}/{len(woredas)} Woreda(s), "
		f"{updated} row(s) updated"
	)


def _from_farmer_reference() -> None:
	frappe.db.sql(
		"""update `tabDA Farmer Link` l
		join `tabDA Farmer Reference` r on r.`farmer_id` = l.`farmer_id`
		set l.`zone` = r.`zone`
		where ifnull(l.`zone`, '') = '' and ifnull(r.`zone`, '') != ''"""
	)
	_log(f"DA Farmer Link: {_rowcount()} row(s) filled from DA Farmer Reference")


def _from_registry(doctype: str) -> None:
	table = f"`tab{doctype}`"
	da_ids = frappe.db.sql_list(
		f"select distinct `da_id` from {table} where {EMPTY} and ifnull(`da_id`, '') != ''"
	)
	if not da_ids:
		return
	client = get_client()
	updated = 0
	failed: dict[str, str] = {}
	for da_id in da_ids:
		try:
			zone = client.get_da(da_id).zone
		except DARegistryError as exc:
			failed[da_id] = f"{exc.__class__.__name__}: {exc}"[:200]
			continue
		if zone:
			frappe.db.sql(f"update {table} set `zone` = %s where `da_id` = %s and {EMPTY}", (zone, da_id))
			updated += _rowcount()
	_log(
		f"{doctype}: registry resolved {len(da_ids) - len(failed)}/{len(da_ids)} DA(s), {updated} row(s) updated"
	)
	if failed:
		# One Error Log entry per doctype, not one per DA: an outage would otherwise flood it.
		frappe.log_error(
			title=f"backfill_zone: {doctype} - {len(failed)} DA(s) not resolved from the registry",
			message="\n".join(f"{da_id}: {err}" for da_id, err in sorted(failed.items())),
		)
