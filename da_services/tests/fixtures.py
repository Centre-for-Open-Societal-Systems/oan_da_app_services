"""Shared test fixtures for DA Services tests."""

import frappe

# DocTypes whose JSON can be ahead of a site that has not been migrated yet.
# Integration tests reload them so the DB columns and Select options match the app.
_SCHEMA_DOCTYPES = (
	("da_profile_change_request", "DA Profile Change Request"),
	("da_integration_event", "DA Integration Event"),
	("da_survey_parameter", "DA Survey Parameter"),
)


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


def kebele_chain(kebele: str) -> dict:
	"""Region / zone / woreda / kebele codes for a master Kebele name."""
	woreda = frappe.db.get_value("DA Kebele", kebele, "woreda")
	zone = frappe.db.get_value("DA Woreda", woreda, "zone")
	region = frappe.db.get_value("DA Zone", zone, "region")
	return {"kebele": kebele, "woreda": woreda, "zone": zone, "region": region}


def live_geo() -> dict:
	"""Active master chain for this site (do not invent Admin Area / dummy codes)."""
	row = frappe.get_all(
		"DA Kebele", filters={"is_active": 1}, fields=["name", "woreda"], order_by="name asc", limit=1
	)[0]
	primary = kebele_chain(row.name)
	siblings = frappe.get_all(
		"DA Kebele",
		filters={"woreda": primary["woreda"], "is_active": 1, "name": ("!=", primary["kebele"])},
		pluck="name",
		order_by="name asc",
		limit=1,
	)
	foreign = None
	for other_region in frappe.get_all(
		"DA Region", filters={"name": ("!=", primary["region"])}, pluck="name", order_by="name asc"
	):
		zones = frappe.get_all("DA Zone", filters={"region": other_region}, pluck="name", limit=1)
		if not zones:
			continue
		woredas = frappe.get_all("DA Woreda", filters={"zone": zones[0]}, pluck="name", limit=1)
		if not woredas:
			continue
		kebeles = frappe.get_all(
			"DA Kebele", filters={"woreda": woredas[0], "is_active": 1}, pluck="name", limit=1
		)
		if kebeles:
			foreign = kebele_chain(kebeles[0])
			break
	if foreign is None:
		cand = frappe.get_all(
			"DA Kebele",
			filters={"is_active": 1, "woreda": ("!=", primary["woreda"])},
			pluck="name",
			order_by="name asc",
			limit=1,
		)[0]
		foreign = kebele_chain(cand)
	return {
		**primary,
		"other_kebele": siblings[0] if siblings else primary["kebele"],
		"foreign_kebele": foreign["kebele"],
		"foreign_woreda": foreign["woreda"],
		"foreign_region": foreign["region"],
	}


def stamp_visit_geo(name: str, geo: dict) -> None:
	"""Overwrite visit posting codes with live master links (registry mock may use legacy codes)."""
	frappe.db.set_value(
		"DA Visit",
		name,
		{
			"region": geo["region"],
			"zone": geo["zone"],
			"woreda": geo["woreda"],
			"kebele": geo["kebele"],
		},
		update_modified=False,
	)


def ensure_kebele_assignment(da_id: str, kebele: str, *, mode: str = "Automatic") -> None:
	"""Replace this DA's assignments with one Effective posting for `kebele`.

	Visit and farmer lists for a Development Agent are scoped to Effective
	DA Kebele Assignment rows, so tests that read those lists need one.
	Uses ignore_links so the posting can reference a synced master row that
	tests already assume exists (do not invent Admin Area / master rows here).
	"""
	frappe.db.delete("DA Kebele Assignment", {"da_id": da_id})
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
	).insert(ignore_permissions=True, ignore_links=True)


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
