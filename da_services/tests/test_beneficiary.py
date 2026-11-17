"""Beneficiary API (api/v1/beneficiary.py): Beneficiary Segment list and Farmer Beneficiary
list / create.

Scope comes from Frappe User Permissions on DA Woreda (the doctype links Region / Zone /
Woreda / Kebele), so frappe.get_list and doc.insert() decide what each user sees and may
link to:
  DA_W01   Development Agent, User Permission DA Woreda ET04-W01
  DA_W02   Development Agent, User Permission DA Woreda ET03-W02
  EXEC     Executive: no read on Farmer Beneficiary at all
"""

import frappe
from frappe.permissions import add_user_permission
from frappe.tests import IntegrationTestCase

from da_services.api.v1 import beneficiary as api
from da_services.services import constants as C

TAG = "BENTEST"
DA_W01 = "bentest-da-w01@da.local"
DA_W02 = "bentest-da-w02@da.local"
EXEC = "bentest-exec@da.local"
USERS = (DA_W01, DA_W02, EXEC)

# Geo masters Farmer Beneficiary links to: (doctype, code, parent link, parent).
GEO = (
	("DA Country", "ET", None, None),
	("DA Region", "ET04", "country", "ET"),
	("DA Region", "ET03", "country", "ET"),
	("DA Zone", "ET04-Z01", "region", "ET04"),
	("DA Zone", "ET03-Z02", "region", "ET03"),
	("DA Woreda", "ET04-W01", "zone", "ET04-Z01"),
	("DA Woreda", "ET03-W02", "zone", "ET03-Z02"),
	("DA Kebele", "ET04-W01-K01", "woreda", "ET04-W01"),
	("DA Kebele", "ET03-W02-K01", "woreda", "ET03-W02"),
)
LOC_W01 = {"region": "ET04", "zone": "ET04-Z01", "woreda": "ET04-W01", "kebele": "ET04-W01-K01"}
LOC_W02 = {"region": "ET03", "zone": "ET03-Z02", "woreda": "ET03-W02", "kebele": "ET03-W02-K01"}


def _user(email, role):
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = email.split("@")[0]
		u.send_welcome_email = 0
		u.insert(ignore_permissions=True, ignore_links=True)
	if role not in frappe.get_roles(email):
		frappe.get_doc("User", email).add_roles(role)


def _ensure_geo():
	"""Create the GEO rows that are missing and reactivate inactive ones (shared rows: not deleted)."""
	for doctype, code, parent_field, parent in GEO:
		if frappe.db.exists(doctype, code):
			frappe.db.set_value(doctype, code, "is_active", 1, update_modified=False)
			continue
		level = doctype.removeprefix("DA ").lower()
		doc = {"doctype": doctype, f"{level}_code": code, f"{level}_name": code, "is_active": 1}
		if parent_field:
			doc[parent_field] = parent
		frappe.get_doc(doc).insert(ignore_permissions=True)


def _segment(title, short_desc="", is_active=1):
	return (
		frappe.get_doc(
			{
				"doctype": "Beneficiary Segment",
				"title": f"{TAG} {title}",
				"short_desc": short_desc,
				"is_active": is_active,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _beneficiary(farmer_id, segment, loc):
	frappe.get_doc(
		{
			"doctype": "Farmer Beneficiary",
			"user": "Administrator",
			"farmer_id": f"{TAG}-{farmer_id}",
			"beneficiary_segment": segment,
			**loc,
		}
	).insert(ignore_permissions=True)


def _code(response: dict):
	"""'ok' for a success envelope, else the error code."""
	return response.get("code") if response.get("status") == "error" else "ok"


def _commit():
	"""A failed call rolls the request back (handle_api_errors), so commit the fixtures
	first or a later assertion would run against an empty table."""
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit


def _cleanup():
	frappe.set_user("Administrator")
	frappe.db.delete("Farmer Beneficiary", {"farmer_id": ("like", f"{TAG}%")})
	frappe.db.delete("Beneficiary Segment", {"title": ("like", f"{TAG}%")})
	frappe.db.delete("User Permission", {"user": ("in", USERS)})
	frappe.clear_cache()
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit


def _counts(response: dict) -> dict:
	return {r["title"]: r.get("farmer_beneficiary_count") for r in response["data"]}


class TestBeneficiaryAPI(IntegrationTestCase):
	def setUp(self):
		_cleanup()
		_ensure_geo()
		_user(DA_W01, C.ROLE_DA)
		_user(DA_W02, C.ROLE_DA)
		_user(EXEC, C.ROLE_EXECUTIVE)
		add_user_permission("DA Woreda", "ET04-W01", DA_W01, ignore_permissions=True)
		add_user_permission("DA Woreda", "ET03-W02", DA_W02, ignore_permissions=True)

		self.seg_a = _segment("Alpha", short_desc="smallholder maize")
		self.seg_b = _segment("Beta", short_desc="dairy")
		self.seg_off = _segment("Off", is_active=0)
		_beneficiary("F1", self.seg_a, LOC_W01)
		_beneficiary("F2", self.seg_a, LOC_W01)
		_beneficiary("F3", self.seg_a, LOC_W02)
		_beneficiary("F4", self.seg_b, LOC_W02)
		frappe.clear_cache()
		_commit()

	def tearDown(self):
		_cleanup()

	# -- Beneficiary Segment list -------------------------------------------------------

	def test_segments_lists_only_active_rows(self):
		res = api.list_beneficiary_segments(search=TAG)
		self.assertEqual(_code(res), "ok")
		names = {r["name"] for r in res["data"]}
		self.assertEqual(names, {self.seg_a, self.seg_b})
		self.assertEqual(res["meta"]["total"], 2)
		self.assertEqual(set(res["data"][0]), {"name", "title", "short_desc", "long_desc"})

	def test_segments_search_matches_title_and_short_desc(self):
		by_title = api.list_beneficiary_segments(search=f"{TAG} Beta")
		self.assertEqual([r["name"] for r in by_title["data"]], [self.seg_b])
		by_desc = api.list_beneficiary_segments(search="smallholder")
		self.assertIn(self.seg_a, [r["name"] for r in by_desc["data"]])
		self.assertNotIn(self.seg_b, [r["name"] for r in by_desc["data"]])
		self.assertEqual(by_title["meta"]["search"], f"{TAG} Beta")

	def test_segments_paginate_newest_first(self):
		first = api.list_beneficiary_segments(search=TAG, limit=1, start=0)
		second = api.list_beneficiary_segments(search=TAG, limit=1, start=1)
		self.assertEqual([r["name"] for r in first["data"]], [self.seg_b])
		self.assertEqual([r["name"] for r in second["data"]], [self.seg_a])
		self.assertEqual(first["meta"]["total"], 2)
		self.assertEqual((first["meta"]["limit"], second["meta"]["start"]), (1, 1))

	def test_segments_reject_bad_paging(self):
		for kwargs in ({"limit": "abc"}, {"limit": 0}, {"limit": 501}, {"start": -1}):
			with self.subTest(**kwargs):
				self.assertEqual(_code(api.list_beneficiary_segments(**kwargs)), "VALIDATION_ERROR")

	def test_segments_without_get_count_have_no_count(self):
		for value in (None, "false", "0"):
			with self.subTest(get_count=value):
				res = api.list_beneficiary_segments(search=TAG, get_count=value)
				self.assertFalse(res["meta"]["get_count"])
				self.assertNotIn("farmer_beneficiary_count", res["data"][0])

	def test_segments_get_count_counts_linked_beneficiaries(self):
		for value in ("true", "1", True):
			with self.subTest(get_count=value):
				res = api.list_beneficiary_segments(search=TAG, get_count=value)
				self.assertTrue(res["meta"]["get_count"])
				self.assertEqual(_counts(res), {f"{TAG} Alpha": 3, f"{TAG} Beta": 1})

	def test_segments_get_count_is_zero_for_an_unused_segment(self):
		seg_c = _segment("Gamma")
		res = api.list_beneficiary_segments(search=f"{TAG} Gamma", get_count="true")
		self.assertEqual([(r["name"], r["farmer_beneficiary_count"]) for r in res["data"]], [(seg_c, 0)])

	def test_segments_get_count_follows_user_permissions(self):
		frappe.set_user(DA_W01)
		self.assertEqual(
			_counts(api.list_beneficiary_segments(search=TAG, get_count="true")),
			{f"{TAG} Alpha": 2, f"{TAG} Beta": 0},
		)
		frappe.set_user(DA_W02)
		self.assertEqual(
			_counts(api.list_beneficiary_segments(search=TAG, get_count="true")),
			{f"{TAG} Alpha": 1, f"{TAG} Beta": 1},
		)

	def test_segments_get_count_is_null_without_read_on_farmer_beneficiary(self):
		frappe.set_user(EXEC)
		res = api.list_beneficiary_segments(search=TAG, get_count="true")
		self.assertEqual(_code(res), "ok")
		self.assertEqual(_counts(res), {f"{TAG} Alpha": None, f"{TAG} Beta": None})

	# -- Farmer Beneficiary list ----------------------------------------------------------

	def test_beneficiaries_list_and_filter(self):
		res = api.list_farmer_beneficiaries(beneficiary_segment=self.seg_a)
		self.assertEqual(_code(res), "ok")
		self.assertEqual({r["farmer_id"] for r in res["data"]}, {f"{TAG}-F1", f"{TAG}-F2", f"{TAG}-F3"})
		self.assertEqual(res["meta"]["total"], 3)
		self.assertEqual(res["meta"]["filters"], {"beneficiary_segment": self.seg_a})

		one = api.list_farmer_beneficiaries(farmer_id=f"{TAG}-F4")
		self.assertEqual([(r["farmer_id"], r["woreda"]) for r in one["data"]], [(f"{TAG}-F4", "ET03-W02")])

	def test_beneficiaries_list_paginates(self):
		res = api.list_farmer_beneficiaries(beneficiary_segment=self.seg_a, limit=2, start=0)
		self.assertEqual(len(res["data"]), 2)
		self.assertEqual(res["meta"]["total"], 3)
		rest = api.list_farmer_beneficiaries(beneficiary_segment=self.seg_a, limit=2, start=2)
		self.assertEqual(len(rest["data"]), 1)
		self.assertEqual(_code(api.list_farmer_beneficiaries(limit=0)), "VALIDATION_ERROR")

	def test_beneficiaries_list_follows_user_permissions(self):
		frappe.set_user(DA_W01)
		res = api.list_farmer_beneficiaries(beneficiary_segment=self.seg_a)
		self.assertEqual({r["farmer_id"] for r in res["data"]}, {f"{TAG}-F1", f"{TAG}-F2"})
		self.assertEqual(res["meta"]["total"], 2)

	def test_beneficiaries_list_needs_read_permission(self):
		frappe.set_user(EXEC)
		self.assertEqual(_code(api.list_farmer_beneficiaries()), "PERMISSION_DENIED")

	# -- Farmer Beneficiary create --------------------------------------------------------

	def test_create_sets_caller_as_user(self):
		frappe.set_user(DA_W01)
		res = api.create_farmer_beneficiary(farmer_id=f"{TAG}-NEW", beneficiary_segment=self.seg_b, **LOC_W01)
		self.assertEqual(_code(res), "ok")
		self.assertEqual(res["data"]["user"], DA_W01)
		self.assertEqual(res["data"]["beneficiary_segment"], self.seg_b)
		self.assertTrue(frappe.db.exists("Farmer Beneficiary", res["data"]["name"]))

	def test_create_lists_missing_fields(self):
		res = api.create_farmer_beneficiary(farmer_id=f"{TAG}-NEW", region="ET04")
		self.assertEqual(_code(res), "VALIDATION_ERROR")
		for field in ("zone", "woreda", "kebele", "beneficiary_segment"):
			self.assertIn(field, res["message"])

	def test_create_rejects_inactive_or_unknown_segment(self):
		for segment in (self.seg_off, "BS-NOPE"):
			with self.subTest(segment=segment):
				res = api.create_farmer_beneficiary(
					farmer_id=f"{TAG}-NEW", beneficiary_segment=segment, **LOC_W01
				)
				self.assertEqual(_code(res), "VALIDATION_ERROR")

	def test_create_rejects_a_duplicate(self):
		res = api.create_farmer_beneficiary(farmer_id=f"{TAG}-F1", beneficiary_segment=self.seg_a, **LOC_W01)
		self.assertEqual(_code(res), "DUPLICATE_ENTRY")

	def test_create_outside_the_users_woreda_is_refused(self):
		frappe.set_user(DA_W01)
		res = api.create_farmer_beneficiary(farmer_id=f"{TAG}-NEW", beneficiary_segment=self.seg_a, **LOC_W02)
		self.assertNotEqual(_code(res), "ok")
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists("Farmer Beneficiary", {"farmer_id": f"{TAG}-NEW"}))

	def test_create_needs_create_permission(self):
		frappe.set_user(EXEC)
		res = api.create_farmer_beneficiary(farmer_id=f"{TAG}-NEW", beneficiary_segment=self.seg_a, **LOC_W01)
		self.assertEqual(_code(res), "PERMISSION_DENIED")
