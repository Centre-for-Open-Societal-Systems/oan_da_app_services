"""Farmer Registry HTTP client against the real Staff API contract (read path).

requests.post is stubbed with canned OpenG2P envelopes shaped like the
registry's SearchResultData / RecordData / RegisterTabRecordData, so the
client, mappers and error mapping are exercised without a live registry.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from da_services.integrations.farmer_registry.client import (
	FarmerRegistryClientError,
	FarmerRegistryError,
	FarmerRegistryServerError,
)
from da_services.integrations.farmer_registry.mapper import (
	merge_tab_records,
	registry_record_to_farmer,
	search_row_to_list_item,
	updates_to_change_requests,
)

REGISTER_ID = "a1a4d25a-1cd4-4356-abac-985a0b3c6bcd"
CONF_KEYS = (
	"farmer_registry_url",
	"farmer_registry_api_key",
	"farmer_registry_keycloak",
	"farmer_registry_ingest",
	"farmer_registry_backend",
)


def _envelope(payload, *, status="SUCCESS", error=None, total=None):
	return {
		"response_header": {
			"request_id": "r",
			"response_status": status,
			"response_error_code": "E" if error else None,
			"response_error_message": error,
			"response_timestamp": "2026-10-22T10:00:00",
		},
		"response_body": {
			"pagination_response": {
				"number_of_items": total if total is not None else 0,
				"number_of_pages": 1,
			},
			"response_payload": payload,
		},
	}


class _Resp:
	def __init__(self, body, status_code=200, url="https://registry.test/x"):
		self._body = body
		self.status_code = status_code
		self.url = url
		self.text = str(body)

	def json(self):
		if isinstance(self._body, Exception):
			raise self._body
		return self._body


SUBJECT = {
	"internal_record_id": "frm-1",
	"functional_record_id": "FR-000101",
	"record_name": "Abebe Kebede Tesfaye",
	"first_name": "Abebe",
	"middle_name": "Kebede",
	"last_name": "Tesfaye",
	"state": "APPROVED",
	"created_at": "2026-06-01T10:30:00",
	"last_approved_at": "2026-06-02T09:00:00",
	"documents": [],
}
FARMER_TAB = [
	{
		"section_register_id": REGISTER_ID,
		"is_list": False,
		"records": [{"birth_date": "1985-04-12", "gender": "MALE"}],
	},
	{
		"section_register_id": REGISTER_ID,
		"is_list": False,
		"records": [
			{"source_of_income": "CROP_PRODUCTION", "education_level": "PRIMARY", "is_psnp_user": False}
		],
	},
	{
		"section_register_id": "phone-register",
		"is_list": True,
		"records": [
			{"phone_number": "922000000", "phone_type": "SECONDARY", "is_primary": False},
			{"phone_number": "911234567", "phone_type": "PRIMARY", "is_primary": True},
		],
	},
]
LOCATION_TAB = [
	{
		"section_register_id": REGISTER_ID,
		"is_list": False,
		"records": [
			{
				"region_name": "Oromia",
				"zone_name": "West Shewa",
				"woreda_name": "Bako Tibe",
				"kebele_name": "Bako 01",
			}
		],
	}
]
HOUSEHOLD_TAB = [
	{
		"section_register_id": REGISTER_ID,
		"is_list": False,
		"records": [{"is_household_head": True, "size_of_group": 5}],
	}
]
LAND_TAB = [
	{
		"section_register_id": "land-register",
		"is_list": True,
		"records": [
			{
				"land_id": "LND-1",
				"area_in_hectare": 1.5,
				"land_ownership_type": "OWNER",
				"certificate_provided": True,
			},
			{
				"land_id": "LND-2",
				"area_in_hectare": 0.75,
				"land_ownership_type": "TENANT",
				"certificate_provided": False,
			},
		],
	}
]
SEARCH_ROWS = [
	{
		"internal_record_id": "frm-1",
		"functional_record_id": "FR-000101",
		"record_name": "Abebe Kebede",
		"created_at": "2026-06-01T10:30:00",
		"display_fields": [
			{"field_name": "woreda_name", "value": "Bako Tibe", "order": 1},
			{"field_name": "kebele_name", "value": "Bako 01", "order": 2},
			{"field_name": "state", "value": "APPROVED", "order": 3},
		],
	},
	{
		"internal_record_id": "frm-2",
		"functional_record_id": "FR-000102",
		"record_name": "Almaz Tesfaye",
		"display_fields": [{"field_name": "kebele_name", "value": "Bako 02", "order": 2}],
	},
]


def _route(url, json=None, **_):
	"""Fake Staff API: answer by path."""
	if url.endswith("/register-data/get_subject_record"):
		rid = json["request_body"]["request_payload"]["subject_record_id"]
		if rid == "missing":
			return _Resp(_envelope(None, status="ERROR", error="Record not found"))
		return _Resp(_envelope(SUBJECT))
	if url.endswith("/register-data/get_tab_records"):
		tab = json["request_body"]["request_payload"]["tab_id"]
		if tab in getattr(_route, "failing_tabs", ()):
			return _Resp({"detail": "gateway"}, 503)
		return _Resp(
			_envelope(
				{
					"farmer_farmer_tab": FARMER_TAB,
					"farmer_location_tab": LOCATION_TAB,
					"farmer_household_link_tab": HOUSEHOLD_TAB,
					"farmer_land_tab": LAND_TAB,
				}.get(tab, [])
			)
		)
	if url.endswith("/register-data/search_in_a_register"):
		_route.last_search = json
		return _Resp(_envelope(SEARCH_ROWS, total=2))
	if url.endswith("/change-requests/create_change_request"):
		_route.last_change = json
		return _Resp(_envelope({"change_request_id": "CR-77"}))
	if url.endswith("/forbidden"):
		return _Resp(
			_envelope(None, status="ERROR", error="Forbidden. Insufficient resource_access roles."), 403
		)
	if url.endswith("/down"):
		return _Resp({"detail": "gateway"}, 503)
	raise AssertionError(f"unexpected url {url}")


class TestHttpReadPath(IntegrationTestCase):
	def setUp(self):
		self._saved = {k: frappe.conf.get(k) for k in CONF_KEYS}
		frappe.conf.update(
			{
				"farmer_registry_url": "https://registry.test",
				"farmer_registry_api_key": "static-test-token",
				"farmer_registry_keycloak": {},
				"farmer_registry_ingest": {"register_id": REGISTER_ID, "data_model": "FARMER_ODK_MODEL"},
			}
		)
		from da_services.integrations.farmer_registry.http import HTTPFarmerRegistryClient

		self.client = HTTPFarmerRegistryClient()

	def tearDown(self):
		for k, v in self._saved.items():
			if v is None:
				frappe.conf.pop(k, None)
			else:
				frappe.conf[k] = v

	# -- config -----------------------------------------------------------

	def test_config_fails_fast_without_auth(self):
		from da_services.integrations.farmer_registry.http import HTTPFarmerRegistryClient

		frappe.conf.update({"farmer_registry_api_key": None, "farmer_registry_keycloak": {}})
		with self.assertRaises(FarmerRegistryError):
			HTTPFarmerRegistryClient()

	def test_config_fails_fast_on_partial_keycloak(self):
		from da_services.integrations.farmer_registry.http import HTTPFarmerRegistryClient

		frappe.conf.update({"farmer_registry_keycloak": {"token_url": "https://kc/token", "client_id": "x"}})
		with self.assertRaises(FarmerRegistryError):
			HTTPFarmerRegistryClient()

	# -- get_farmer ---------------------------------------------------------

	@patch("da_services.integrations.farmer_registry.http.requests.post", side_effect=_route)
	def test_get_farmer_merges_identity_and_tabs(self, _post):
		farmer = self.client.get_farmer("frm-1")
		self.assertEqual(farmer.farmer_id, "frm-1")
		self.assertEqual(farmer.reg_id, "FR-000101")
		self.assertEqual(farmer.given_name, "Abebe")
		self.assertEqual(farmer.father_first_name, "Kebede")
		self.assertEqual(farmer.family_name, "Tesfaye")
		self.assertEqual(farmer.phone, "911234567")  # primary wins over the first row
		self.assertTrue(farmer.has_personal_phone)
		self.assertEqual(str(farmer.birth_date), "1985-04-12")
		self.assertEqual(farmer.gender, "male")
		self.assertEqual(farmer.kebele_name, "Bako 01")
		self.assertEqual(farmer.woreda_name, "Bako Tibe")
		self.assertTrue(farmer.is_household_head)
		self.assertEqual(farmer.household_size, 5)
		self.assertEqual(farmer.source_of_income, "CROP_PRODUCTION")
		self.assertEqual(farmer.state, "APPROVED")
		self.assertEqual(farmer.registration_date.isoformat(), "2026-06-02T09:00:00")

	@patch("da_services.integrations.farmer_registry.http.requests.post", side_effect=_route)
	def test_get_farmer_not_found_returns_none(self, _post):
		self.assertIsNone(self.client.get_farmer("missing"))

	@patch("da_services.integrations.farmer_registry.http.requests.post", side_effect=_route)
	def test_get_farmer_reports_sections_it_could_not_fetch(self, _post):
		"""A tab failure yields a partial record that says so, not a silently blank one."""
		self.assertEqual(self.client.get_farmer("frm-1").incomplete_sections, [])
		_route.failing_tabs = ("farmer_household_link_tab", "farmer_location_tab")
		try:
			farmer = self.client.get_farmer("frm-1")
		finally:
			_route.failing_tabs = ()
		self.assertEqual(farmer.farmer_id, "frm-1")
		self.assertEqual(farmer.given_name, "Abebe")  # identity row still came back
		self.assertEqual(farmer.incomplete_sections, ["location", "household"])
		self.assertIsNone(farmer.kebele_name)
		self.assertFalse(farmer.is_household_head)

	@patch("da_services.integrations.farmer_registry.http.requests.post", side_effect=_route)
	def test_requests_carry_token_csrf_and_envelope(self, post):
		self.client.get_farmer("frm-1")
		_, kwargs = post.call_args_list[0]
		self.assertEqual(kwargs["headers"]["Authorization"], "Bearer static-test-token")
		self.assertEqual(kwargs["headers"]["X-CSRF-Token"], kwargs["cookies"]["X-CSRF-Token"])
		env = kwargs["json"]
		self.assertEqual(env["request_header"]["sender_app_mnemonic"], "DA_SERVICES")
		self.assertEqual(env["request_body"]["request_payload"]["subject_register_id"], REGISTER_ID)

	# -- list_farmers -------------------------------------------------------

	@patch("da_services.integrations.farmer_registry.http.requests.post", side_effect=_route)
	def test_list_farmers_filters_and_paginates(self, _post):
		items, total = self.client.list_farmers(woreda="Bako Tibe", state="APPROVED", limit=20, offset=40)
		self.assertEqual(total, 2)
		self.assertEqual([i.farmer_id for i in items], ["frm-1", "frm-2"])
		self.assertEqual(items[0].kebele, "Bako 01")
		self.assertEqual(items[0].state, "APPROVED")
		self.assertEqual(items[1].state, "UNKNOWN")
		pag = _route.last_search["request_body"]["pagination_request"]
		self.assertEqual(pag["current_page"], 3)
		self.assertEqual(pag["page_size"], 20)
		self.assertEqual(pag["filter_by"], {"woreda_name": "Bako Tibe", "state": "APPROVED"})

	@patch("da_services.integrations.farmer_registry.http.requests.post", side_effect=_route)
	def test_list_farmers_narrows_by_kebele_client_side(self, _post):
		items, _ = self.client.list_farmers(kebele="Bako 02")
		self.assertEqual([i.farmer_id for i in items], ["frm-2"])

	# -- plots --------------------------------------------------------------

	@patch("da_services.integrations.farmer_registry.http.requests.post", side_effect=_route)
	def test_get_farmer_plots(self, _post):
		plots = self.client.get_farmer_plots("frm-1")
		self.assertEqual([p.plot_id for p in plots], ["LND-1", "LND-2"])
		self.assertEqual(plots[0].area_in_hectare, 1.5)
		self.assertTrue(plots[0].certificate_provided)
		self.assertEqual(plots[1].land_ownership_type, "TENANT")

	# -- update -------------------------------------------------------------

	@patch("da_services.integrations.farmer_registry.http.requests.post", side_effect=_route)
	def test_update_farmer_creates_change_request(self, _post):
		result = self.client.update_farmer(
			"frm-1", {"income_source": "LIVESTOCK", "education_level": "SECONDARY"}
		)
		self.assertTrue(result["success"])
		self.assertFalse(result["conflict"])
		self.assertEqual(result["version"], "CR-77")
		payload = _route.last_change["request_body"]["request_payload"]
		self.assertEqual(payload["internal_record_id"], "frm-1")
		self.assertEqual(payload["section_id"], "farmer_farmer_socio_economic_and_health_section_04")
		self.assertEqual(payload["tab_id"], "farmer_farmer_tab")
		self.assertEqual(
			payload["change_payload"], {"source_of_income": "LIVESTOCK", "education_level": "SECONDARY"}
		)

	def test_update_farmer_rejects_unsupported_fields(self):
		with self.assertRaises(FarmerRegistryClientError) as cm:
			self.client.update_farmer("frm-1", {"phone": "911", "income_source": "LIVESTOCK"})
		self.assertIn("phone", str(cm.exception))

	# -- error mapping ------------------------------------------------------

	@patch("da_services.integrations.farmer_registry.http.requests.post", side_effect=_route)
	def test_4xx_is_client_error(self, _post):
		with self.assertRaises(FarmerRegistryClientError) as cm:
			self.client._staff_post("/forbidden", {}, action="probe")
		self.assertEqual(cm.exception.status_code, 403)
		self.assertIn("Insufficient resource_access roles", str(cm.exception))

	@patch("da_services.integrations.farmer_registry.http.requests.post", side_effect=_route)
	def test_5xx_is_server_error(self, _post):
		with self.assertRaises(FarmerRegistryServerError):
			self.client._staff_post("/down", {}, action="probe")


class TestReadMappers(IntegrationTestCase):
	def test_merge_keeps_identity_row_and_collects_phones(self):
		record = dict(SUBJECT)
		merge_tab_records(record, FARMER_TAB)
		self.assertEqual(record["first_name"], "Abebe")
		self.assertEqual(record["gender"], "MALE")
		self.assertEqual(len(record["_phones"]), 2)
		self.assertNotIn("documents", record.get("_lists", {}))

	def test_record_to_farmer_tolerates_missing_tabs(self):
		farmer = registry_record_to_farmer(dict(SUBJECT), "frm-1")
		self.assertEqual(farmer.record_name, "Abebe Kebede Tesfaye")
		self.assertIsNone(farmer.phone)
		self.assertFalse(farmer.has_personal_phone)
		self.assertEqual(farmer.state, "APPROVED")

	def test_search_row_without_display_fields(self):
		item = search_row_to_list_item({"internal_record_id": "x"})
		self.assertEqual(item.name, "x")
		self.assertEqual(item.state, "UNKNOWN")

	def test_updates_split_by_section(self):
		groups, unsupported = updates_to_change_requests(
			{"income_source": "A", "marital_status": "B", "phone": "1", "client_op_id": "op"}
		)
		self.assertEqual(unsupported, ["phone"])
		self.assertEqual(len(groups), 1)
		self.assertEqual(groups[0]["payload"], {"source_of_income": "A", "marital_status": "B"})
