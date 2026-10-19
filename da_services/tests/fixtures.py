"""Shared test fixtures for DA Services tests."""

import frappe

# Administrative area hierarchy for tests
ADMINISTRATIVE_AREAS = [
	("ET04", "Region", None, "Oromia"),
	("ET04-Z01", "Zone", "ET04", "West Shewa"),
	("ET04-W01", "Woreda", "ET04-Z01", "Bako Tibe"),
	("ET04-W01-K01", "Kebele", "ET04-W01", "Bako 01"),
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


def ensure_kebele_assignment(da_id: str, kebele: str, *, mode: str = "Automatic") -> None:
	"""Replace this DA's assignments with one Effective posting for `kebele`.

	Visit and farmer lists for a Development Agent are scoped to Effective
	DA Kebele Assignment rows, so tests that read those lists need one.
	"""
	create_administrative_areas()
	frappe.db.delete("DA Kebele Assignment", {"da_id": da_id})
	frappe.get_doc(
		{
			"doctype": "DA Kebele Assignment",
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
