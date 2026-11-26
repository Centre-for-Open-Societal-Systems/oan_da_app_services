"""Livestock registry: form description, intake validation, staff calls and routes.

HTTP calls are stubbed. Nothing in this module contacts the live registry.
The mock form is a trimmed copy of the dev "Livestock Intake" form.
"""

from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import Mock, patch

import frappe

from da_services.integrations.livestock_registry.client import (
	LivestockRegistryClientError,
	LivestockRegistryServerError,
)
from da_services.integrations.livestock_registry.http import HTTPLivestockRegistryClient
from da_services.integrations.livestock_registry.intake import (
	IntakeContractError,
	_canonicalize_choice,
	describe_form,
	geo_hierarchy,
	submit_with_metadata,
)
from da_services.integrations.livestock_registry.mock import (
	ANIMAL_REGISTER_ID,
	FIXTURE_FARMER_ID,
	FIXTURE_SUBJECT_ID,
	FIXTURE_SUBMISSION_ID,
	HEALTH_REGISTER_ID,
	MOCK_CATALOGS,
	MOCK_FIELDS,
	MOCK_FORM,
	REGISTER_ID,
)
from da_services.integrations.livestock_registry.schemas import LivestockSubmission

LOOKUP = "livestock_farmer_lookup"
LOCATION = "livestock_livestock_location_section_03"
ANIMALS = "livestock_animal_details_section_01"
HEALTH = "livestock_health_event_details_section_01"
STAFF = "https://staff-livestock.test"
MASTER = "https://master-data.test"


def _envelope(payload, *, status="SUCCESS", error="", pages=1):
	return {
		"response_header": {"response_status": status, "response_error_message": error},
		"response_body": {"response_payload": payload, "pagination_response": {"number_of_pages": pages}},
	}


class _Resp:
	def __init__(self, status_code, body=None):
		self.status_code = status_code
		self._body = body if body is not None else {}
		self.text = json.dumps(self._body)
		self.url = f"{STAFF}/x"

	def json(self):
		return self._body


_TEST_SSO = {"Authorization": "Bearer unused", "X-CSRF-Token": "test-csrf"}


def _client(**kwargs):
	kwargs.setdefault("register_id", REGISTER_ID)
	return HTTPLivestockRegistryClient(
		staff_url=STAFF,
		tokens=type("Tokens", (), {"get_token": lambda self: "cached-token"})(),
		**kwargs,
	)


def _patch_sso():
	return patch(
		"da_services.integrations.staff_registry.http.frappe.get_request_header",
		side_effect=_TEST_SSO.get,
	)


def _submit(requests, *, submission_id=None, subject_record_id=None, farmer_id=FIXTURE_FARMER_ID, find=None):
	saves = []

	def save(**kwargs):
		saves.append(kwargs)
		records = [dict(row) for row in kwargs["section_payload"]]
		if kwargs["section_register_id"] == REGISTER_ID:
			for row in records:
				row["internal_record_id"] = "subject-1"
		return {
			"submission_id": kwargs["submission_id"] or "sub-1",
			"section_payloads": [{"section_register_id": kwargs["section_register_id"], "records": records}],
		}

	result = submit_with_metadata(
		form=MOCK_FORM,
		fields_for=lambda register_id: MOCK_FIELDS[register_id],
		catalogs=MOCK_CATALOGS,
		requests=copy.deepcopy(requests),
		submission_id=submission_id,
		subject_record_id=subject_record_id,
		save=save,
		find_subject=find,
		farmer_id=farmer_id,
	)
	return result, saves


ANIMAL = {"ear_tag_id": "ET-1", "species": "CATTLE", "gender": "FEMALE"}


class TestDescribeForm(unittest.TestCase):
	def setUp(self):
		self.form = describe_form(MOCK_FORM, MOCK_FIELDS, MOCK_CATALOGS)
		self.sections = {section["section_id"]: section for section in self.form["sections"]}

	def test_sections_keep_screen_order_and_tables_list_their_columns(self):
		self.assertEqual(
			[s["section_id"] for s in self.form["sections"]],
			[
				LOOKUP,
				"livestock_survey_personnel_section_02",
				LOCATION,
				ANIMALS,
				HEALTH,
				"livestock_retagging_details_section_01",
				"livestock_vaccination_details_section_01",
				"livestock_vital_event_details_section_01",
				"livestock_breeding_details_section_01",
			],
		)
		animals = self.sections[ANIMALS]
		self.assertTrue(animals["is_list"])
		self.assertEqual(animals["table"]["widget_id"], "ls_animal_details_table")
		self.assertTrue(animals["table"]["operations"]["add"])
		self.assertEqual(
			[field["field_name"] for field in animals["fields"]],
			["ear_tag_id", "species", "gender", "quantity", "date_of_birth", "age"],
		)
		farmer = next(tab for tab in self.form["screen_tabs"] if tab["key"] == "farmer")
		self.assertEqual(
			[section["section_id"] for section in farmer["sections"]],
			[LOOKUP, "livestock_survey_personnel_section_02", LOCATION],
		)
		self.assertEqual(self.sections[LOOKUP]["screen_section"], "identity")

	def test_fields_carry_what_the_frontend_renders(self):
		fields = {field["field_name"]: field for field in self.sections[ANIMALS]["fields"]}
		self.assertEqual(fields["species"]["widget"], "select")
		self.assertTrue(fields["species"]["required"])
		self.assertEqual(
			fields["species"]["choices"][0],
			{"value_id": "CATTLE", "value_display": "Cattle", "value_code": "CATTLE"},
		)
		self.assertEqual(fields["species"]["value_key"], "value_id")
		self.assertTrue(fields["species"]["catalog_loaded"])
		self.assertEqual(fields["gender"]["options"][1], {"label": "Female", "value": "FEMALE"})
		self.assertEqual(fields["quantity"]["data_type"], "integer")
		self.assertTrue(fields["age"]["readonly"])
		self.assertEqual(fields["age"]["age_from"], "date_of_birth")

		health = {field["field_name"]: field for field in self.sections[HEALTH]["fields"]}
		self.assertEqual(health["disease_type"]["rules"][0]["action"], "show")
		self.assertEqual(health["ear_tag_id"]["autofill"]["sourceWidgetId"], "ls_animal_details_table")

	def test_form_only_and_geo_fields_are_described(self):
		lookup = {field["field_name"]: field for field in self.sections[LOOKUP]["fields"]}
		self.assertIsNone(lookup["fayda_id"]["data_type"])
		self.assertIn("pattern", lookup["fayda_id"])
		geo = self.sections[LOCATION]["fields"][0]
		self.assertEqual(geo["field_name"], "geo_lowest_level_value_id")
		self.assertEqual(geo["writes"]["hierarchy"], "geo_code_hierarchy_json")
		self.assertNotIn("geo", geo)

	def test_a_catalog_that_was_not_loaded_is_marked(self):
		form = describe_form(MOCK_FORM, MOCK_FIELDS, {})
		animals = next(section for section in form["sections"] if section["section_id"] == ANIMALS)
		species = animals["fields"][1]
		self.assertIsNone(species["choices"])
		self.assertFalse(species["catalog_loaded"])


class TestIntakeSubmit(unittest.TestCase):
	def test_a_screen_section_id_resolves_to_the_staff_section(self):
		result, saves = _submit(
			[
				{"section_id": "identity", "records": [{"fayda_id": "123456789012"}]},
				{
					"section_id": "survey-personnel",
					"records": [{"surveyor_name": "Abebe Kebede", "surveyor_mobile_number": "0911223344"}],
				},
				{
					"section_id": "vaccination",
					"records": [
						{
							"ear_tag_id": "ET-1",
							"species": "CATTLE",
							"vaccine_type": "VACCINE_TYPE_ANTHRAX_CATTLE",
							"vaccination_date": "2026-03-15",
						}
					],
				},
			]
		)
		self.assertEqual(
			[save["section_id"] for save in saves],
			[LOOKUP, "livestock_survey_personnel_section_02", "livestock_vaccination_details_section_01"],
		)
		self.assertEqual(result["subject_record_id"], "subject-1")
		self.assertEqual(saves[2]["section_payload"][0]["link_internal_record_id"], "subject-1")

	def test_main_section_is_saved_first_and_lists_are_linked_to_it(self):
		result, saves = _submit(
			[
				{"section_id": ANIMALS, "records": [ANIMAL]},
				{"section_id": LOOKUP, "records": [{"fayda_id": "123456789012"}]},
			]
		)
		self.assertEqual([save["section_id"] for save in saves], [LOOKUP, ANIMALS])
		self.assertEqual(
			saves[0]["section_payload"], [{"fayda_id": "123456789012", "farmer_id": FIXTURE_FARMER_ID}]
		)
		self.assertEqual(saves[1]["submission_id"], "sub-1")
		self.assertEqual(saves[1]["section_payload"][0]["link_internal_record_id"], "subject-1")
		self.assertEqual(result["subject_record_id"], "subject-1")

	def test_another_farmer_in_the_lookup_is_rejected(self):
		with self.assertRaisesRegex(IntakeContractError, "does not match"):
			_submit(
				[
					{
						"section_id": LOOKUP,
						"records": [{"farmer_id": "FR-000000000001", "fayda_id": "123456789012"}],
					}
				]
			)

	def test_list_section_needs_the_main_record(self):
		with self.assertRaisesRegex(IntakeContractError, "save the main Livestock section"):
			_submit([{"section_id": ANIMALS, "records": [ANIMAL]}])

	def test_list_section_on_a_draft_reads_the_main_record(self):
		find = Mock(return_value="subject-9")
		_result, saves = _submit(
			[{"section_id": ANIMALS, "records": [ANIMAL]}], submission_id="sub-1", find=find
		)
		find.assert_called_once_with("sub-1")
		self.assertEqual(saves[0]["section_payload"][0]["link_internal_record_id"], "subject-9")

	def test_required_fields_and_form_rules_are_checked_per_row(self):
		with self.assertRaisesRegex(IntakeContractError, r"ls_animal_details\[1\]\.species is required"):
			_submit([{"section_id": ANIMALS, "records": [ANIMAL, {"gender": "MALE"}]}], subject_record_id="s")
		with self.assertRaisesRegex(IntakeContractError, "disease_type is required"):
			_submit(
				[{"section_id": HEALTH, "records": [{"ear_tag_id": "ET-1", "event_type": "DISEASE"}]}],
				subject_record_id="s",
			)
		with self.assertRaisesRegex(IntakeContractError, "event_type is required"):
			_submit([{"section_id": HEALTH, "records": [{"ear_tag_id": "ET-1"}]}], subject_record_id="s")
		_result, saves = _submit(
			[{"section_id": HEALTH, "records": [{"ear_tag_id": "ET-1", "event_type": "INJURY"}]}],
			subject_record_id="s",
		)
		self.assertEqual(saves[0]["section_payload"][0]["event_type"], "INJURY")

	def test_labels_dates_and_numbers_are_stored_as_registry_values(self):
		_result, saves = _submit(
			[
				{
					"section_id": ANIMALS,
					"records": [
						{
							**ANIMAL,
							"species": "Goats",
							"gender": "Female",
							"quantity": "3",
							"date_of_birth": "14/05/2021",
						}
					],
				}
			],
			subject_record_id="s",
		)
		row = saves[0]["section_payload"][0]
		self.assertEqual(row["species"], "GOATS")
		self.assertEqual(row["gender"], "FEMALE")
		self.assertEqual(row["quantity"], 3)
		self.assertEqual(row["date_of_birth"], "2021-05-14")

	def test_widget_value_key_picks_the_stored_catalog_column(self):
		health = {
			"widget-data-source": {
				"params": {"attribute_id": "LIVESTOCK_HEALTH_STATUS"},
				"valueKey": "value_code",
			}
		}
		catalog = {
			"LIVESTOCK_HEALTH_STATUS": [
				{
					"value_id": "LIVESTOCK_HEALTH_STATUS_HEALTHY",
					"value_code": "HEALTHY",
					"value_display": "Healthy",
				}
			],
			"LIVESTOCK_SPECIES": [{"value_id": "CATTLE", "value_code": "CATTLE", "value_display": "Cattle"}],
		}
		for sent in ("HEALTHY", "Healthy", "LIVESTOCK_HEALTH_STATUS_HEALTHY"):
			self.assertEqual(_canonicalize_choice("health_status", health, sent, catalog), "HEALTHY")
		species = {
			"widget-data-source": {
				"params": {"attribute_id": "LIVESTOCK_SPECIES"},
				"valueKey": "value_id",
			}
		}
		self.assertEqual(_canonicalize_choice("species", species, "Cattle", catalog), "CATTLE")

	def test_bad_values_are_rejected(self):
		cases = [
			({"section_id": ANIMALS, "records": [{**ANIMAL, "species": "Dragon"}]}, "not an allowed value"),
			(
				{"section_id": ANIMALS, "records": [{**ANIMAL, "colour": "red"}]},
				"unknown Livestock field colour",
			),
			(
				{"section_id": ANIMALS, "records": [{**ANIMAL, "internal_record_id": "x"}]},
				"managed by the registry",
			),
			(
				{
					"section_id": HEALTH,
					"records": [{"ear_tag_id": "E", "event_type": "INJURY", "species": "X"}],
				},
				"species is not an allowed value",
			),
			({"section_id": LOOKUP, "records": [{"fayda_id": "12"}]}, "expected format"),
			({"section_id": "nope", "records": [{}]}, "missing or ambiguous"),
			(
				{
					"section_id": LOOKUP,
					"records": [{"fayda_id": "123456789012"}, {"fayda_id": "123456789012"}],
				},
				"one record",
			),
		]
		for request, message in cases:
			with self.subTest(request=request):
				with self.assertRaises(IntakeContractError) as caught:
					_submit([request], subject_record_id="s")
				self.assertIn(message, str(caught.exception))

	def test_read_only_age_from_date_of_birth_is_accepted(self):
		_result, saves = _submit(
			[{"section_id": ANIMALS, "records": [{**ANIMAL, "age": "5 years, 5 months"}]}],
			subject_record_id="s",
		)
		self.assertEqual(saves[0]["section_payload"][0]["age"], "5 years, 5 months")

	def test_events_copy_species_and_age_from_the_animal_with_the_same_ear_tag(self):
		_result, saves = _submit(
			[
				{"section_id": LOOKUP, "records": [{"fayda_id": "123456789012"}]},
				{"section_id": ANIMALS, "records": [{**ANIMAL, "age": "2 years"}]},
				{"section_id": HEALTH, "records": [{"ear_tag_id": "ET-1", "event_type": "INJURY"}]},
			]
		)
		event = saves[2]["section_payload"][0]
		self.assertEqual(event["species"], "CATTLE")
		self.assertEqual(event["age"], "2 years")

	def test_kebele_id_gets_its_place_hierarchy(self):
		_result, saves = _submit(
			[{"section_id": LOCATION, "records": [{"geo_lowest_level_value_id": "ET01010101"}]}]
		)
		row = saves[0]["section_payload"][0]
		self.assertEqual(row["geo_code_hierarchy_json"], geo_hierarchy("ET01010101"))
		self.assertEqual(
			[level["level_value_id"] for level in row["geo_code_hierarchy_json"]["hierarchy"]],
			["ET01", "ET0101", "ET010101", "ET01010101"],
		)
		self.assertIsNone(geo_hierarchy("Tigray"))
		with self.assertRaisesRegex(IntakeContractError, "geo_lowest_level_value_id is required"):
			_submit([{"section_id": LOCATION, "records": [{"latitude": "13.9"}]}])


class TestStaffAuth(unittest.TestCase):
	def test_forwards_the_callers_sso_headers(self):
		seen = {}
		request_headers = {"Authorization": "Bearer sso-token", "X-CSRF-Token": "sso-csrf"}

		def post(url, **kwargs):
			seen.update(kwargs)
			return _Resp(200, _envelope({"submission_id": "sub-1"}))

		client = HTTPLivestockRegistryClient(staff_url=STAFF, register_id=REGISTER_ID)
		with (
			patch(
				"da_services.integrations.staff_registry.http.frappe.get_request_header",
				side_effect=request_headers.get,
			),
			patch("da_services.integrations.staff_registry.http.requests.post", post),
		):
			client.get_submission("sub-1")

		self.assertEqual(seen["headers"]["Authorization"], "Bearer sso-token")
		self.assertEqual(seen["headers"]["X-CSRF-Token"], "sso-csrf")
		self.assertEqual(seen["cookies"], {"X-CSRF-Token": "sso-csrf"})

	def test_missing_sso_header_is_a_401_and_nothing_is_sent(self):
		for request_headers in ({"Authorization": "Bearer t"}, {"X-CSRF-Token": "c"}, {}):
			with self.subTest(headers=request_headers):
				post = Mock()
				with (
					patch(
						"da_services.integrations.staff_registry.http.frappe.get_request_header",
						side_effect=request_headers.get,
					),
					patch("da_services.integrations.staff_registry.http.requests.post", post),
					self.assertRaises(LivestockRegistryClientError) as caught,
				):
					HTTPLivestockRegistryClient(staff_url=STAFF, register_id=REGISTER_ID).get_submission(
						"sub-1"
					)
				self.assertEqual(caught.exception.status_code, 401)
				post.assert_not_called()

	def test_token_provider_overrides_bearer_but_keeps_fe_csrf(self):
		request_headers = {"Authorization": "Bearer sso-token", "X-CSRF-Token": "sso-csrf"}
		with patch(
			"da_services.integrations.staff_registry.http.frappe.get_request_header",
			side_effect=request_headers.get,
		):
			headers, cookies = _client()._transport.auth_and_csrf_headers()
		self.assertEqual(headers["Authorization"], "Bearer cached-token")
		self.assertEqual(headers["X-CSRF-Token"], "sso-csrf")
		self.assertEqual(cookies, {"X-CSRF-Token": "sso-csrf"})


def _staff(responses: dict):
	"""requests.post stub: path -> payload (or list of payloads for pages). Records every call."""
	calls = []

	def post(url, **kwargs):
		path = url.split(".test", 1)[1]
		body = kwargs["json"]
		calls.append((path, body))
		answer = responses[path]
		if isinstance(answer, _Resp):
			return answer
		if callable(answer):
			answer = answer(body)
		return _Resp(200, answer if "response_header" in answer else _envelope(answer))

	return post, calls


_REGISTERS = [
	{"register_id": "reg-farmer", "register_mnemonic": "Farmer"},
	{"register_id": REGISTER_ID, "register_mnemonic": "Livestock"},
]
_FORMS = [
	{"form_id": "form-other", "register_id": "reg-farmer", "form_mnemonic": "Livestock Intake"},
	{"form_id": "form-livestock", "register_id": REGISTER_ID, "form_mnemonic": "Livestock Intake"},
]


class TestHttpCalls(unittest.TestCase):
	def setUp(self):
		self._sso = _patch_sso()
		self._sso.start()
		self.addCleanup(self._sso.stop)

	def _run(self, responses, call, **client_kwargs):
		post, calls = _staff(responses)
		with patch("da_services.integrations.staff_registry.http.requests.post", post):
			result = call(_client(**client_kwargs))
		return result, calls

	def test_describe_renders_and_reads_every_catalog_page(self):
		def catalog(body):
			page = body["request_body"]["pagination_request"]["current_page"]
			attribute = body["request_body"]["request_payload"]["attribute_id"]
			rows = MOCK_CATALOGS.get(attribute, [])
			return _envelope({"attribute_values": rows[page - 1 : page]}, pages=max(len(rows), 1))

		responses = {
			"/intake-form-metadata/get_all_intake_forms": _FORMS,
			"/intake-form-metadata/render_intake_form": MOCK_FORM,
			"/register-metadata/get_register_fields": lambda body: {
				"fields": MOCK_FIELDS[body["request_body"]["request_payload"]["register_id"]]
			},
			"/attributes/get_attribute_values": catalog,
		}
		described, calls = self._run(
			responses, lambda client: client.describe_intake_form(), master_data_url=MASTER
		)
		render = [body for path, body in calls if path == "/intake-form-metadata/render_intake_form"]
		self.assertEqual(render[0]["request_body"]["request_payload"], {"form_id": "form-livestock"})
		animals = next(section for section in described["sections"] if section["section_id"] == ANIMALS)
		species = animals["fields"][1]
		self.assertEqual([row["value_id"] for row in species["choices"]], ["CATTLE", "GOATS"])
		catalog_calls = [path for path, _body in calls if path == "/attributes/get_attribute_values"]
		self.assertEqual(
			len(catalog_calls), 5
		)  # species 2 pages (once for both tables), event type 2, disease 1

	def test_save_posts_the_staff_payload(self):
		saved, calls = self._run(
			{"/intake-form-data/save_intake_form_submission": {"submission_id": "sub-1"}},
			lambda client: client.save_section(
				section_id=LOOKUP,
				section_register_id=REGISTER_ID,
				section_payload=[{"fayda_id": "1"}],
				form_id="form-livestock",
				register_id=REGISTER_ID,
			),
		)
		self.assertEqual(saved["submission_id"], "sub-1")
		payload = calls[0][1]["request_body"]["request_payload"]
		self.assertEqual(
			payload,
			{
				"submission_id": None,
				"section_id": LOOKUP,
				"section_payload": [{"fayda_id": "1"}],
				"section_register_id": REGISTER_ID,
				"form_id": "form-livestock",
				"register_id": REGISTER_ID,
			},
		)

	def test_finalize_and_read_submission(self):
		finalized, calls = self._run(
			{
				"/intake-form-data/finalize_intake_form_submission": {
					"submission_id": "sub-1",
					"draft_status": "FINAL",
				}
			},
			lambda client: client.finalize_submission("sub-1"),
		)
		self.assertEqual(calls[0][1]["request_body"]["request_payload"], {"submission_id": "sub-1"})
		self.assertEqual(finalized["draft_status"], "FINAL")

		submission, _calls = self._run(
			{
				"/intake-form-data/get_intake_form_submission": {
					"submission_id": "sub-1",
					"record_name": FIXTURE_FARMER_ID,
					"section_payloads": [
						{"section_id": LOOKUP, "section_register_id": REGISTER_ID, "records": [{}]}
					],
				}
			},
			lambda client: client.get_submission("sub-1"),
		)
		self.assertEqual(submission.record_name, FIXTURE_FARMER_ID)
		self.assertEqual(submission.sections[0].section_id, LOOKUP)

	def test_list_submission_source_is_one_search(self):
		rows, calls = self._run(
			{
				"/intake-form-data/search_in_intake_form_submissions": [
					{"submission_id": "sub-1", "record_name": FIXTURE_FARMER_ID},
					{"submission_id": "sub-2", "record_name": "FR-000000000001"},
				]
			},
			lambda client: client.list_for_farmer(FIXTURE_FARMER_ID, source="submission"),
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["submission_id"], "sub-1")
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0][0], "/intake-form-data/search_in_intake_form_submissions")
		self.assertNotIn("get_all_registers", "".join(path for path, _ in calls))
		self.assertNotIn("get_section_records", "".join(path for path, _ in calls))

	def test_list_register_source_is_one_search(self):
		rows, calls = self._run(
			{
				"/register-data/search_in_a_register": [
					{
						"internal_record_id": "rec-1",
						"display_fields": [{"field_name": "farmer_id", "value": FIXTURE_FARMER_ID}],
					},
					{
						"internal_record_id": "rec-2",
						"display_fields": [{"field_name": "farmer_id", "value": "FR-x"}],
					},
				]
			},
			lambda client: client.list_for_farmer(FIXTURE_FARMER_ID, source="register"),
		)
		self.assertEqual([row["internal_record_id"] for row in rows], ["rec-1"])
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0][0], "/register-data/search_in_a_register")

	def test_register_tab_is_one_get_tab_records(self):
		detail, calls = self._run(
			{"/register-data/get_tab_records": [{"ear_tag_id": "ET-1", "species": "CATTLE"}]},
			lambda client: client.get_register_tab("rec-1", "livestock"),
		)
		self.assertEqual(detail["tab_id"], "livestock")
		self.assertEqual(detail["tabs"][0]["records"][0]["ear_tag_id"], "ET-1")
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0][0], "/register-data/get_tab_records")
		payload = calls[0][1]["request_body"]["request_payload"]
		self.assertEqual(
			payload,
			{
				"subject_register_id": REGISTER_ID,
				"subject_record_id": "rec-1",
				"tab_id": "livestock",
			},
		)

	def test_errors_are_typed(self):
		with self.assertRaises(LivestockRegistryClientError):
			self._run(
				{
					"/intake-form-data/get_intake_form_submission": _envelope(
						None, status="ERROR", error="bad"
					)
				},
				lambda client: client.get_submission("sub-1"),
			)
		with self.assertRaises(LivestockRegistryServerError):
			self._run(
				{"/intake-form-data/get_intake_form_submission": _Resp(502, {})},
				lambda client: client.get_submission("sub-1"),
			)
		with self.assertRaisesRegex(LivestockRegistryClientError, "unsupported Livestock list source"):
			_client().list_for_farmer(FIXTURE_FARMER_ID, source="both")


class TestRoutes(unittest.TestCase):
	def _call(self, func, *args, body=None, form_dict=None, client=None):
		previous = frappe.local.form_dict
		frappe.local.form_dict = frappe._dict(form_dict or {})
		try:
			with (
				patch(
					"da_services.api.v1.livestock.farmers.get_farmer_profile",
					side_effect=frappe.DoesNotExistError,
				),
				patch("da_services.api.v1.livestock.get_livestock_registry_client", return_value=client),
				patch("da_services.api.v1.livestock._request_body", return_value=body or {}),
			):
				return func(*args)
		finally:
			frappe.local.form_dict = previous

	def test_save_sends_sections_with_the_farmer_id_of_the_path(self):
		from da_services.api.v1.livestock import save_farmer_livestock

		client = Mock()
		client.submit_sections.return_value = {"submission_id": "sub-1", "subject_record_id": "s-1"}
		body = {"sections": [{"section_id": LOOKUP, "records": [{"fayda_id": "123456789012"}]}]}
		result = self._call(save_farmer_livestock, FIXTURE_FARMER_ID, body=body, client=client)

		(sections,) = client.submit_sections.call_args.args
		self.assertEqual(sections[0]["section_id"], LOOKUP)
		self.assertEqual(client.submit_sections.call_args.kwargs["farmer_id"], FIXTURE_FARMER_ID)
		self.assertIsNone(client.submit_sections.call_args.kwargs["submission_id"])
		client.get_submission.assert_not_called()
		self.assertEqual(result["data"]["submission_id"], "sub-1")
		self.assertEqual(result["data"]["farmer_id"], FIXTURE_FARMER_ID)

	def _assert_refused(self, result, status):
		self.assertEqual(result["status"], "error")
		self.assertEqual(frappe.local.response.pop("http_status_code", None), status)

	def test_another_farmers_draft_is_refused(self):
		from da_services.api.v1.livestock import finalize_farmer_livestock, save_farmer_livestock

		client = Mock()
		client.get_submission.return_value = LivestockSubmission(
			submission_id="sub-1", record_name="FR-000000000001"
		)
		self._assert_refused(
			self._call(finalize_farmer_livestock, FIXTURE_FARMER_ID, "sub-1", client=client), 403
		)
		self._assert_refused(
			self._call(
				save_farmer_livestock,
				FIXTURE_FARMER_ID,
				body={"submission_id": "sub-1", "section_id": ANIMALS, "records": [ANIMAL]},
				client=client,
			),
			403,
		)
		client.finalize_submission.assert_not_called()
		client.submit_sections.assert_not_called()

	def test_finalize_own_draft(self):
		from da_services.api.v1.livestock import finalize_farmer_livestock

		client = Mock()
		client.get_submission.return_value = LivestockSubmission(
			submission_id="sub-1", record_name=FIXTURE_FARMER_ID
		)
		client.finalize_submission.return_value = {"submission_id": "sub-1", "approval_status": "PENDING"}
		result = self._call(finalize_farmer_livestock, FIXTURE_FARMER_ID, "sub-1", client=client)
		self.assertEqual(result["data"]["approval_status"], "PENDING")

	def test_list_requires_source_and_returns_search_hits(self):
		from da_services.api.v1.livestock import get_farmer_livestock

		client = Mock()
		client.list_for_farmer.return_value = [{"submission_id": FIXTURE_SUBMISSION_ID}]
		listed = self._call(
			get_farmer_livestock,
			FIXTURE_FARMER_ID,
			form_dict={"source": "submission"},
			client=client,
		)
		client.list_for_farmer.assert_called_once_with(FIXTURE_FARMER_ID, source="submission")
		self.assertEqual(listed["data"]["source"], "submission")
		self.assertEqual(listed["data"]["livestock"][0]["submission_id"], FIXTURE_SUBMISSION_ID)

	def test_list_requires_source(self):
		from da_services.api.v1.livestock import get_farmer_livestock

		result = self._call(get_farmer_livestock, FIXTURE_FARMER_ID, form_dict={}, client=Mock())
		self._assert_refused(result, 400)
		self.assertIn("source", result["message"].lower())

	def test_register_detail_requires_tab_id(self):
		from da_services.api.v1.livestock import get_farmer_livestock

		result = self._call(
			get_farmer_livestock,
			FIXTURE_FARMER_ID,
			form_dict={"internal_record_id": "rec-1"},
			client=Mock(),
		)
		self._assert_refused(result, 400)
		self.assertIn("tab_id", result["message"].lower())

	def test_register_tab_route(self):
		from da_services.api.v1.livestock import get_farmer_livestock

		client = Mock()
		client.get_register_tab.return_value = {
			"internal_record_id": "rec-1",
			"register_id": REGISTER_ID,
			"tab_id": "livestock",
			"tabs": [{"tab_id": "livestock", "records": [{"ear_tag_id": "ET-1"}]}],
		}
		result = self._call(
			get_farmer_livestock,
			FIXTURE_FARMER_ID,
			form_dict={"internal_record_id": "rec-1", "tab_id": "livestock"},
			client=client,
		)
		client.get_register_tab.assert_called_once_with("rec-1", "livestock")
		self.assertEqual(result["data"]["tab_id"], "livestock")

	def test_one_submission(self):
		from da_services.api.v1.livestock import get_farmer_livestock

		client = Mock()
		client.get_submission.return_value = LivestockSubmission(
			submission_id=FIXTURE_SUBMISSION_ID,
			record_name=FIXTURE_FARMER_ID,
			sections=[],
		)
		one = self._call(
			get_farmer_livestock,
			FIXTURE_FARMER_ID,
			form_dict={"submission_id": FIXTURE_SUBMISSION_ID},
			client=client,
		)
		self.assertEqual(one["data"]["submission_id"], FIXTURE_SUBMISSION_ID)

	def test_internal_id_without_a_farmer_id_is_rejected(self):
		from da_services.api.v1.livestock import get_farmer_livestock

		result = self._call(get_farmer_livestock, "not-a-cached-farmer", client=Mock())
		self._assert_refused(result, 400)

	def test_intake_form_route_returns_the_described_form(self):
		from da_services.api.v1.livestock import get_livestock_intake_form

		client = Mock()
		client.describe_intake_form.return_value = describe_form(MOCK_FORM, MOCK_FIELDS, MOCK_CATALOGS)
		result = self._call(get_livestock_intake_form, client=client)
		sections = {section["section_id"]: section for section in result["data"]["sections"]}
		self.assertEqual(sections[HEALTH]["section_register_id"], HEALTH_REGISTER_ID)
		self.assertEqual(sections[ANIMALS]["section_register_id"], ANIMAL_REGISTER_ID)
		self.assertEqual(
			[tab["key"] for tab in result["data"]["screen_tabs"]],
			["farmer", "livestock", "health", "breeding"],
		)


if __name__ == "__main__":
	unittest.main()
