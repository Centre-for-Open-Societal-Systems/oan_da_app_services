"""Advisor directory: add, list and read one (services/advisors.py, DA Advisor)."""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from da_services.services import advisors as svc

GOOD = {
	"name": "Dr. Hanna Girma",
	"expertise": "Agronomy",
	"unit": "Crop Development Directorate",
	"region": "Sidama",
	"woreda": "Test Woreda Hawassa",
	"phone": "+251 91 623 0458",
	"email": "hanna.g@moa.gov.et",
	"status": "Active",
	"availability": "Mon-Fri, 08:30-17:00",
}


class TestAdvisors(IntegrationTestCase):
	def test_create_returns_the_form_fields_and_stores_a_row(self):
		out = svc.create_advisor(**GOOD)
		self.assertTrue(out["id"].startswith("ADV-"))
		for key, value in GOOD.items():
			self.assertEqual(out[key], value)
		self.assertEqual(len(out["updated_at"]), 19)
		row = frappe.db.get_value(
			"DA Advisor",
			out["id"],
			["full_name", "expertise_area", "moa_unit", "mobile_number"],
			as_dict=True,
		)
		self.assertEqual(row.full_name, "Dr. Hanna Girma")
		self.assertEqual(row.expertise_area, "Agronomy")
		self.assertEqual(row.moa_unit, "Crop Development Directorate")
		self.assertEqual(row.mobile_number, "+251 91 623 0458")

	def test_create_trims_text(self):
		out = svc.create_advisor(**{**GOOD, "name": "  Dr. Hanna Girma  ", "email": " hanna.g@moa.gov.et "})
		self.assertEqual(out["name"], "Dr. Hanna Girma")
		self.assertEqual(out["email"], "hanna.g@moa.gov.et")

	def test_status_and_availability_defaults(self):
		body = {k: v for k, v in GOOD.items() if k not in ("status", "availability")}
		active = svc.create_advisor(**body)
		self.assertEqual(active["status"], "Active")
		self.assertIn("08:30", active["availability"])
		on_leave = svc.create_advisor(**{**body, "status": "On leave"})
		self.assertEqual(on_leave["availability"], "Not taking requests")

	def test_create_rejects_missing_and_invalid_fields(self):
		for field in ("name", "expertise", "unit", "region", "woreda", "phone", "email"):
			with self.assertRaises(frappe.ValidationError, msg=field):
				svc.create_advisor(**{**GOOD, field: "  "})
		for change in (
			{"name": "Ab"},
			{"expertise": "Astrology"},
			{"status": "Retired"},
			{"phone": "0912345678"},
			{"phone": "+251 91 234 567"},
			{"email": "someone@"},
		):
			with self.assertRaises(frappe.ValidationError, msg=str(change)):
				svc.create_advisor(**{**GOOD, **change})

	def test_list_filters_search_and_pages(self):
		first = svc.create_advisor(**{**GOOD, "woreda": "Test Woreda Listing"})
		second = svc.create_advisor(
			**{
				**GOOD,
				"woreda": "Test Woreda Listing",
				"name": "Meseret Haile",
				"expertise": "Irrigation",
				"status": "On leave",
				"region": "Amhara",
			}
		)
		# Rows of the other tests in this class are still in the transaction, so this test uses its own Woreda.
		woreda = "Test Woreda Listing"

		rows, total = svc.list_advisors(woreda=woreda)
		self.assertEqual(total, 2)
		self.assertEqual({r["id"] for r in rows}, {first["id"], second["id"]})

		rows, total = svc.list_advisors(woreda=woreda, expertise="Irrigation")
		self.assertEqual([r["id"] for r in rows], [second["id"]])
		rows, total = svc.list_advisors(woreda=woreda, status="On leave")
		self.assertEqual([r["id"] for r in rows], [second["id"]])
		rows, total = svc.list_advisors(woreda=woreda, region="Sidama")
		self.assertEqual([r["id"] for r in rows], [first["id"]])
		rows, total = svc.list_advisors(woreda=woreda, q="meseret")
		self.assertEqual([r["id"] for r in rows], [second["id"]])
		rows, total = svc.list_advisors(q=first["id"])
		self.assertEqual([r["id"] for r in rows], [first["id"]])

		page_one, total = svc.list_advisors(woreda=woreda, limit=1)
		page_two, _ = svc.list_advisors(woreda=woreda, limit=1, start=1)
		self.assertEqual(total, 2)
		self.assertEqual(len(page_one), 1)
		self.assertNotEqual(page_one[0]["id"], page_two[0]["id"])

	def test_list_rejects_bad_paging_and_filters(self):
		for kwargs in (
			{"limit": 0},
			{"limit": 501},
			{"start": -1},
			{"expertise": "Astrology"},
			{"status": "Retired"},
		):
			with self.assertRaises(frappe.ValidationError, msg=str(kwargs)):
				svc.list_advisors(**kwargs)
		with self.assertRaises(frappe.ValidationError):
			svc.int_param("limit", "abc", 50)
		self.assertEqual(svc.int_param("limit", "", 50), 50)
		self.assertEqual(svc.int_param("limit", "25", 50), 25)

	def test_get_advisor(self):
		made = svc.create_advisor(**GOOD)
		got = svc.get_advisor(made["id"])
		self.assertEqual(got["name"], made["name"])
		self.assertEqual(got["phone"], made["phone"])
		with self.assertRaises(frappe.DoesNotExistError):
			svc.get_advisor("ADV-NOPE")

	def test_da_sees_only_advisors_of_their_woreda(self):
		from types import SimpleNamespace

		from da_services.services import constants as C

		# Only the code -> name lookup matters here, so the Zone / Region chain is not built.
		area = frappe.get_doc(
			{
				"doctype": "DA Administrative Area",
				"code": "TST9-W01",
				"level": "Woreda",
				"name_en": "Test Scope Woreda",
			}
		)
		area.flags.ignore_validate = True
		area.insert(ignore_permissions=True)
		by_name = svc.create_advisor(**{**GOOD, "woreda": "Test Scope Woreda"})
		by_code = svc.create_advisor(**{**GOOD, "woreda": "TST9-W01"})
		elsewhere = svc.create_advisor(**{**GOOD, "woreda": "Test Other Woreda"})

		def ctx(*roles, woredas=(), unrestricted=False):
			return SimpleNamespace(
				unrestricted=unrestricted,
				woreda_scopes=frozenset(woredas),
				has_any_role=lambda *asked: bool(set(asked) & set(roles)),
			)

		da = ctx(C.ROLE_DA, woredas=["TST9-W01"])
		rows, total = svc.list_advisors(da, limit=500)
		self.assertEqual({r["id"] for r in rows}, {by_name["id"], by_code["id"]})
		self.assertEqual(total, 2)
		# A requested Woreda narrows the scope but never widens it.
		self.assertEqual(svc.list_advisors(da, woreda="Test Other Woreda"), ([], 0))
		self.assertEqual([r["id"] for r in svc.list_advisors(da, woreda="TST9-W01")[0]], [by_code["id"]])
		self.assertEqual(svc.get_advisor(by_name["id"], da)["id"], by_name["id"])
		with self.assertRaises(frappe.DoesNotExistError):
			svc.get_advisor(elsewhere["id"], da)
		# A DA with no Woreda sees nothing.
		self.assertEqual(svc.list_advisors(ctx(C.ROLE_DA)), ([], 0))

		# Officers and the Administrator see the whole directory.
		for officer in (
			ctx(C.ROLE_SUPERVISOR, woredas=["TST9-W01"]),
			ctx(C.ROLE_DA, C.ROLE_SUPERVISOR, woredas=["TST9-W01"]),
			ctx(C.ROLE_ADMIN, unrestricted=True),
		):
			ids = {r["id"] for r in svc.list_advisors(officer, limit=500)[0]}
			self.assertTrue({by_name["id"], by_code["id"], elsewhere["id"]} <= ids)
			self.assertEqual(svc.get_advisor(elsewhere["id"], officer)["id"], elsewhere["id"])
