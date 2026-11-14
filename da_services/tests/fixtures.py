"""Shared test fixtures for DA Services tests."""

from __future__ import annotations

from collections.abc import Sequence

import frappe

from da_services.da_services.doctype.da_kebele_assignment.da_kebele_assignment import LIVE_STATES

# DocTypes whose JSON can be ahead of a site that has not been migrated yet.
# Integration tests reload them so the DB columns and Select options match the app.
_SCHEMA_DOCTYPES = (
	("da_profile_change_request", "DA Profile Change Request"),
	("da_integration_event", "DA Integration Event"),
	("da_survey_parameter", "DA Survey Parameter"),
)

# Kebele codes owned by the shared test admin-area fixture (safe to replace per DA).
_TEST_KEBELE_PREFIXES = ("ET04-W01", "ET03-W02", "Bako")


def clear_rbac_for_users(*users: str) -> None:
	"""Delete RBAC rows for the given test users only — never the whole table."""
	wanted = [u for u in users if u]
	if wanted:
		frappe.db.delete("DA RBAC Assignment", {"user": ("in", wanted)})


def clear_visits_for(
	*,
	owners: Sequence[str] = (),
	farmer_ids: Sequence[str] = (),
) -> None:
	"""Delete visits created by tests (owner and/or known test farmer ids)."""
	if owners:
		frappe.db.delete("DA Visit", {"owner": ("in", list(owners))})
	if farmer_ids:
		frappe.db.delete("DA Visit", {"farmer_id": ("in", list(farmer_ids))})


def clear_tasks_for(*, owners: Sequence[str] = (), farmer_ids: Sequence[str] = ()) -> None:
	if owners:
		frappe.db.delete("DA Task", {"owner": ("in", list(owners))})
	if farmer_ids:
		frappe.db.delete("DA Task", {"farmer_id": ("in", list(farmer_ids))})


def clear_notifications_for(*users: str) -> None:
	wanted = [u for u in users if u]
	if wanted:
		frappe.db.delete("DA Notification", {"user": ("in", wanted)})


def _is_test_fixture_kebele(kebele: str) -> bool:
	return any(
		kebele == p or kebele.startswith(f"{p}-") or kebele.startswith(p) for p in _TEST_KEBELE_PREFIXES
	)


def clear_test_kebele_assignments_for(*da_ids: str) -> None:
	"""Remove only ET04/ET03 fixture kebele rows for the given DAs (never other Woredas).

	Uses SQL delete: DocType ``on_trash`` forbids delete_doc (history / supersede).
	"""
	for da_id in da_ids:
		if not da_id:
			continue
		rows = frappe.get_all(
			"DA Kebele Assignment",
			filters={"da_id": da_id},
			fields=["name", "kebele"],
		)
		names = [row.name for row in rows if _is_test_fixture_kebele(row.kebele or "")]
		if names:
			frappe.db.delete("DA Kebele Assignment", {"name": ("in", names)})


def ensure_doctype_schema(*doctypes: str) -> None:
	"""Reload DocType JSON into the site so missing columns and Select options exist.

	Does not change application logic. Call from setUp when a test hits a field
	that the current site's table may not have yet.
	"""
	wanted = set(doctypes) if doctypes else {name for _file, name in _SCHEMA_DOCTYPES}
	for file_name, name in _SCHEMA_DOCTYPES:
		if name not in wanted:
			continue
		frappe.reload_doc("DA Services", "doctype", file_name, force=True)


# Administrative area hierarchy for tests
ADMINISTRATIVE_AREAS = [
	("ET04", "Region", None, "Oromia"),
	("ET04-Z01", "Zone", "ET04", "West Shewa"),
	("ET04-W01", "Woreda", "ET04-Z01", "Bako Tibe"),
	("ET04-W01-K01", "Kebele", "ET04-W01", "Bako 01"),
	("ET04-W01-K02", "Kebele", "ET04-W01", "Bako 02"),
	("ET04-W01-K03", "Kebele", "ET04-W01", "Dambi Gobu"),
	("ET04-W01-K04", "Kebele", "ET04-W01", "Dambi 04"),
	("ET04-W01-K05", "Kebele", "ET04-W01", "Kebele 05"),
	("ET04-W01-K06", "Kebele", "ET04-W01", "Kebele 06"),
	("ET04-W01-K07", "Kebele", "ET04-W01", "Gombo"),
	("ET03", "Region", None, "Amhara"),
	("ET03-Z02", "Zone", "ET03", "North Shewa"),
	("ET03-W02", "Woreda", "ET03-Z02", "Ankober"),
	("ET03-W02-K01", "Kebele", "ET03-W02", "Ankober 01"),
]


def ensure_administrative_area(code: str, level: str, parent: str | None, name_en: str):
	"""Create one administrative area when it is not already in the master."""
	if frappe.db.exists("DA Administrative Area", code):
		return
	doc = frappe.new_doc("DA Administrative Area")
	doc.code = code
	doc.level = level
	doc.parent_area = parent
	doc.name_en = name_en
	doc.active = True
	doc.insert(ignore_permissions=True, ignore_links=True)


# Geo masters (DA Country / Region / Zone / Woreda / Kebele) with the same codes as the
# administrative areas above, plus the extra Zones / Woredas that RBAC scopes in tests use.
# DA Kebele Assignment.kebele and DA RBAC Assignment scopes link to these masters.
GEO_COUNTRY = ("ET", "Ethiopia")
GEO_EXTRA = [
	("ET04-Z02", "Zone", "ET04", "Zone 02"),
	("ET03-Z01", "Zone", "ET03", "Zone 01"),
	("ET04-W02", "Woreda", "ET04-Z01", "Woreda 02"),
	("ET04-W09", "Woreda", "ET04-Z02", "Woreda 09"),
	("ET03-W01", "Woreda", "ET03-Z01", "Woreda 01"),
	("ET04-W09-K01", "Kebele", "ET04-W09", "Kebele 09-01"),
]
# level -> (doctype, parent link field)
GEO_LEVELS = {
	"Country": ("DA Country", None),
	"Region": ("DA Region", "country"),
	"Zone": ("DA Zone", "region"),
	"Woreda": ("DA Woreda", "zone"),
	"Kebele": ("DA Kebele", "woreda"),
}


def ensure_geo_masters() -> None:
	"""Create the test geo master rows that are missing and reactivate inactive ones.

	Idempotent; test_geo_master_sync empties these tables, so call it from setUp.
	"""
	rows = [(GEO_COUNTRY[0], "Country", None, GEO_COUNTRY[1])]
	rows += [
		(code, level, parent or GEO_COUNTRY[0], name) for code, level, parent, name in ADMINISTRATIVE_AREAS
	]
	rows += GEO_EXTRA
	order = list(GEO_LEVELS)
	for code, level, parent, name_en in sorted(rows, key=lambda r: order.index(r[1])):
		doctype, parent_field = GEO_LEVELS[level]
		if frappe.db.exists(doctype, code):
			if not frappe.db.get_value(doctype, code, "is_active"):
				frappe.db.set_value(doctype, code, "is_active", 1, update_modified=False)
			continue
		prefix = level.lower()
		doc = {"doctype": doctype, f"{prefix}_code": code, f"{prefix}_name": name_en, "is_active": 1}
		if parent_field:
			doc[parent_field] = parent
		frappe.get_doc(doc).insert(ignore_permissions=True)


def set_da_registry_id(user: str, da_id: str) -> None:
	"""Give `user` the DA Registry ID `da_id`, taking it off any other user (it is unique)."""
	frappe.db.sql(
		"update `tabUser` set da_registry_id = null where da_registry_id = %s and name != %s",
		(da_id, user),
	)
	frappe.db.set_value("User", user, "da_registry_id", da_id, update_modified=False)


def ensure_da_user(da_id: str) -> str:
	"""The User that holds DA Registry ID `da_id`, for a DA Kebele Assignment.

	DA Kebele Assignment requires `user` and fetches `da_id` from its DA Registry ID.
	The user of the DA's latest RBAC assignment is preferred, so the posting belongs to
	the login the test acts as; then a user already holding the ID; else one is created.
	"""
	user = frappe.db.get_value("DA RBAC Assignment", {"da_id": da_id}, "user", order_by="creation desc")
	if not user:
		user = frappe.db.get_value("User", {"da_registry_id": da_id})
	if not user:
		user = f"{da_id.lower()}@da-test.local"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{"doctype": "User", "email": user, "first_name": da_id, "send_welcome_email": 0}
			).insert(ignore_permissions=True)
	set_da_registry_id(user, da_id)
	return user


def ensure_kebele_assignment(da_id: str, kebele: str, *, mode: str = "Automatic") -> None:
	"""Ensure one Effective posting for ``da_id`` → ``kebele`` without wiping other Woredas.

	Only removes/replaces live rows for this DA that already sit on the shared
	ET04/ET03 test fixture kebeles (or this exact kebele). Manual/SSO postings
	under other codes (e.g. ET01010101) are left alone.
	"""
	create_administrative_areas()
	existing = frappe.get_all(
		"DA Kebele Assignment",
		filters={"da_id": da_id, "state": ("in", list(LIVE_STATES))},
		fields=["name", "kebele", "state"],
	)
	names = [
		row.name for row in existing if row.kebele == kebele or _is_test_fixture_kebele(row.kebele or "")
	]
	if names:
		# on_trash refuses delete_doc; tests clear fixture rows via SQL.
		frappe.db.delete("DA Kebele Assignment", {"name": ("in", names)})
	frappe.get_doc(
		{
			"doctype": "DA Kebele Assignment",
			"user": ensure_da_user(da_id),
			"da_id": da_id,
			"kebele": kebele,
			"state": "Effective",
			"mode": mode,
			"effective_date": "2026-10-01",
		}
	).insert(ignore_permissions=True)


def create_administrative_areas():
	"""Create DA Administrative Area records needed for tests.

	This creates a minimal hierarchy of administrative areas:
	- Regions: ET04 (Oromia), ET03 (Amhara)
	- Zones: ET04-Z01, ET03-Z02
	- Woredas: ET04-W01, ET03-W02
	- Kebeles: Multiple kebeles under each woreda

	All test files should call this in setUp() to ensure
	administrative areas exist before creating assignments.
	"""
	for code, level, parent, name_en in ADMINISTRATIVE_AREAS:
		ensure_administrative_area(code, level, parent, name_en)
	ensure_geo_masters()


def cleanup_test_data():
	"""Clean up common test data.

	Call this in tearDown() to remove test records.
	"""
	doctypes_to_clean = [
		"DA Farmer Link",
		"DA Visit",
		"DA Visit Specialization",
		"DA Farmer Reference",
		"DA Kebele Assignment",
	]

	for doctype in doctypes_to_clean:
		frappe.db.delete(doctype, {"da_id": ["like", "%TEST%"]})
		frappe.db.delete(doctype, {"farmer_id": ["like", "%TEST%"]})
