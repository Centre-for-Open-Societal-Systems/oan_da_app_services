"""Farmer registration (ingest pipeline): mapper payload shape + mock client.

Covers the pure DA-form → ingest-envelope mapping and the mock client's
register_farmer / get_ingest_status, so the register path has coverage without
a live Farmer Registry.
"""

import frappe
from frappe.tests import IntegrationTestCase

from da_services.integrations.farmer_registry.mapper import da_form_to_ingest_payload
from da_services.integrations.farmer_registry.mock import MockFarmerRegistryClient

SAMPLE_FORM = {
	"first_name": "Abebe",
	"father_name": "Kebede",
	"grandfather_name": "Tesfaye",
	"gender": "male",
	"date_of_birth": "1985-04-12",
	"phone": "+251911234567",
	"region": "4",
	"woreda": "40801",
	"kebele": "40801101001",
	"national_id": "1234567890123456",
	"location": "9.0300 38.7400 2350 5",
	"lands": [{"ownership": "owner", "area_hectare": 1.5}],
	"crops": [{"name": "teff", "planted_date": "2026-07-01"}],
	"livestock": [{"animal": "cattle", "count": 3}],
	"da_id": "DA-ID-001",
}


class TestIngestMapper(IntegrationTestCase):
	def test_envelope_structure(self):
		payload = da_form_to_ingest_payload(SAMPLE_FORM)
		self.assertIn("header", payload)
		self.assertTrue(payload["header"]["message_id"].startswith("da-svc-"))
		# The Staff API attributes staff-submitted ODK messages to the registry's own
		# partner and expects the STAFF_AUTHENTICATED marker (registry team's collection).
		self.assertEqual(payload["header"]["sender_id"], "farmer-partner")
		self.assertEqual(payload["header"]["signature"], "STAFF_AUTHENTICATED")
		self.assertIn("payload", payload["message"])

	def test_personal_and_location_mapping(self):
		inner = da_form_to_ingest_payload(SAMPLE_FORM)["message"]["payload"]
		personal = inner["basic_info"]["personal_info"]
		self.assertEqual(personal["first_name_english"], "Abebe")
		self.assertEqual(personal["father_name_english"], "Kebede")
		self.assertEqual(personal["primary_phone_number"], "+251911234567")
		self.assertEqual(personal["phone_number"], "yes")
		# Master Data P-code widths, as the ODK form sends them (region 2, woreda 6, kebele 12)
		self.assertEqual(
			inner["basic_info"]["locale_info"], {"region": "04", "woreda": "040801", "kebele": "040801101001"}
		)
		self.assertEqual(inner["register_mnemonic"], "Farmer")

	def test_matches_registry_integration_collection_shape(self):
		"""Same keys the registry team's passing run submits: __id, phone flag, enumerator, codes."""
		form = {
			**SAMPLE_FORM,
			"client_op_id": "a7211a13-3534-4348-b6d0-4e6ee8a4cd2d",
			"enumerator_name": "Abebe K.",
		}
		env = da_form_to_ingest_payload(form)
		inner = env["message"]["payload"]
		self.assertEqual(env["header"]["message_id"], "da-svc-a7211a13-3534-4348-b6d0-4e6ee8a4cd2d")
		self.assertEqual(inner["__id"], "uuid:a7211a13-3534-4348-b6d0-4e6ee8a4cd2d")
		self.assertEqual(
			inner["survey_metadata"], {"enumerator_name": "Abebe K.", "enumerator_id": "DA-ID-001"}
		)
		no_phone = da_form_to_ingest_payload({"first_name": "Solo"})["message"]["payload"]
		self.assertEqual(no_phone["basic_info"]["personal_info"]["phone_number"], "no")
		self.assertNotIn("primary_phone_number", no_phone["basic_info"]["personal_info"])

	def test_repeat_sections(self):
		inner = da_form_to_ingest_payload(SAMPLE_FORM)["message"]["payload"]
		self.assertEqual(inner["land_info"]["land_info_repeat"][0]["total_land_area"], 1.5)
		self.assertEqual(inner["crop_information"]["crop_repeat"][0]["crop_name"], "teff")
		self.assertEqual(inner["livestock_info"]["livestock_repeat"][0]["num_animals"], 3)

	def test_optional_sections_omitted(self):
		minimal = da_form_to_ingest_payload({"first_name": "Solo"})
		inner = minimal["message"]["payload"]
		self.assertNotIn("land_info", inner)
		self.assertNotIn("national_id_section", inner)
		self.assertEqual(inner["basic_info"]["personal_info"]["first_name_english"], "Solo")


class TestMockRegister(IntegrationTestCase):
	def setUp(self):
		self.client = MockFarmerRegistryClient()

	def test_register_returns_correlation_id(self):
		payload = da_form_to_ingest_payload(SAMPLE_FORM)
		result = self.client.register_farmer(payload)
		self.assertTrue(result["success"])
		self.assertEqual(result["status"], "SUCCESS")
		self.assertTrue(result["correlation_id"].startswith("mock-corr-"))

	def test_register_rejects_bad_payload(self):
		from da_services.integrations.farmer_registry.client import FarmerRegistryError

		with self.assertRaises(FarmerRegistryError):
			self.client.register_farmer({"header": {}})

	def test_ingest_status(self):
		rows = self.client.get_ingest_status("mock-corr-x")
		self.assertEqual(rows[0]["ingestion_status"], "PROCESSED")


class TestRegistrationForm(IntegrationTestCase):
	"""Pydantic validation at the API boundary (review item 2)."""

	def _form(self, **over):
		from da_services.integrations.farmer_registry.schemas import FarmerRegistrationForm

		data = {**SAMPLE_FORM, **over}
		return FarmerRegistrationForm(**data)

	def test_valid_form_normalises(self):
		form = self._form(gender="M", date_of_birth="1985-04-12", ui_only_field="ignored")
		dumped = form.model_dump(mode="json", exclude_none=True)
		self.assertEqual(dumped["gender"], "male")
		self.assertEqual(dumped["date_of_birth"], "1985-04-12")
		self.assertNotIn("ui_only_field", dumped)

	def test_rejects_missing_first_name(self):
		from pydantic import ValidationError

		data = {k: v for k, v in SAMPLE_FORM.items() if k != "first_name"}
		with self.assertRaises(ValidationError):
			from da_services.integrations.farmer_registry.schemas import FarmerRegistrationForm

			FarmerRegistrationForm(**data)

	def test_rejects_bad_phone_kebele_and_location(self):
		from pydantic import ValidationError

		for bad in ({"phone": "abc"}, {"kebele": "Bako 01"}, {"location": "north of town"}, {"gender": "x"}):
			with self.assertRaises(ValidationError, msg=str(bad)):
				self._form(**bad)

	def test_client_op_id_format(self):
		from pydantic import ValidationError

		self._form(client_op_id="op-20261022-abc123")
		with self.assertRaises(ValidationError):
			self._form(client_op_id="bad id!")


class TestRegisterIdempotency(IntegrationTestCase):
	"""Same client_op_id twice → one DA Integration Event, second call flagged duplicate (review item 3)."""

	def test_duplicate_submission_returns_first_event(self):
		import uuid

		from da_services.services import farmers as svc

		op_id = f"test-{uuid.uuid4().hex[:12]}"
		form = {**SAMPLE_FORM, "client_op_id": op_id}

		first = svc.register_farmer(dict(form), ctx=None)
		second = svc.register_farmer(dict(form), ctx=None)

		self.assertFalse(first["duplicate"])
		self.assertTrue(second["duplicate"])
		self.assertEqual(first["event_id"], second["event_id"])
		self.assertEqual(first["message_id"], f"da-svc-{op_id}")
		self.assertEqual(second["status"], "DUPLICATE")

		events = frappe.get_all(
			"DA Integration Event",
			filters={"event_type": "Farmer Registration", "reference_id": f"da-svc-{op_id}"},
		)
		self.assertEqual(len(events), 1)

	def test_message_id_is_uuid_without_client_op_id(self):
		import uuid

		payload = da_form_to_ingest_payload(SAMPLE_FORM)
		suffix = payload["header"]["message_id"].removeprefix("da-svc-")
		uuid.UUID(suffix)  # raises if not a UUID


class TestHouseholdAndSocioMapping(IntegrationTestCase):
	"""Household head, PSNP, disability and socio-economic fields reach the registry sections."""

	def test_household_and_socio_sections(self):
		form = {
			**SAMPLE_FORM,
			"household_head": True,
			"is_psnp_user": False,
			"disabled": False,
			"marital_status": "married",
			"education_level": "can_read_and_write",
			"income_source": "crop_production",
		}
		inner = da_form_to_ingest_payload(form)["message"]["payload"]
		self.assertEqual(inner["household_questions"], {"household_head": "yes", "is_psnp_user": "no"})
		self.assertEqual(
			inner["socio_economic_data"],
			{
				"marital_status": "married",
				"education_level": "can_read_and_write",
				"income_source": "crop_production",
			},
		)
		self.assertEqual(inner["basic_info"]["personal_info"]["disability"], "no")

	def test_sections_omitted_when_not_provided(self):
		inner = da_form_to_ingest_payload(SAMPLE_FORM)["message"]["payload"]
		self.assertNotIn("household_questions", inner)
		self.assertNotIn("socio_economic_data", inner)
		self.assertNotIn("disability", inner["basic_info"]["personal_info"])

	def test_yes_no_accepts_strings_and_bools(self):
		from da_services.integrations.farmer_registry.mapper import _yes_no

		self.assertEqual(_yes_no(True), "yes")
		self.assertEqual(_yes_no("Yes"), "yes")
		self.assertEqual(_yes_no("1"), "yes")
		self.assertEqual(_yes_no(False), "no")
		self.assertEqual(_yes_no("no"), "no")

	def test_form_schema_accepts_household_fields(self):
		from da_services.integrations.farmer_registry.schemas import FarmerRegistrationForm

		form = FarmerRegistrationForm(**SAMPLE_FORM, household_head="yes", is_psnp_user="no", disabled=False)
		dumped = form.model_dump(mode="json", exclude_none=True)
		self.assertIs(dumped["household_head"], True)
		self.assertIs(dumped["is_psnp_user"], False)
		self.assertIs(dumped["disabled"], False)

	def test_form_boolean_formats(self):
		from pydantic import ValidationError

		from da_services.integrations.farmer_registry.schemas import FarmerRegistrationForm

		for raw, expected in (
			("YES", True),
			("true", True),
			(1, True),
			("No", False),
			("0", False),
			(False, False),
		):
			form = FarmerRegistrationForm(**SAMPLE_FORM, household_head=raw)
			self.assertIs(form.household_head, expected, raw)
		self.assertIsNone(FarmerRegistrationForm(**SAMPLE_FORM, disabled="").disabled)
		with self.assertRaises(ValidationError):
			FarmerRegistrationForm(**SAMPLE_FORM, is_psnp_user="maybe")

	def test_form_socio_codes_are_validated_and_normalised(self):
		from pydantic import ValidationError

		from da_services.integrations.farmer_registry.schemas import (
			EDUCATION_LEVELS,
			INCOME_SOURCES,
			MARITAL_STATUSES,
			FarmerRegistrationForm,
		)

		form = FarmerRegistrationForm(
			**SAMPLE_FORM,
			marital_status="Married",
			education_level="Can read and write",
			income_source="livestock-production",
		)
		self.assertEqual(form.marital_status, "married")
		self.assertEqual(form.education_level, "can_read_and_write")
		self.assertEqual(form.income_source, "livestock_production")
		self.assertIn("single", MARITAL_STATUSES)
		self.assertIn("higher_education", EDUCATION_LEVELS)
		self.assertIn("others", INCOME_SOURCES)
		# ODK form codes (what FARMER_ODK_MODEL was built for) are accepted as-is,
		# and income_source is a select_multiple: several codes, space-separated.
		odk = FarmerRegistrationForm(
			**SAMPLE_FORM,
			marital_status="single",
			education_level="read_write",
			income_source=["crop", "livestock"],
		)
		self.assertEqual((odk.education_level, odk.income_source), ("read_write", "crop livestock"))
		self.assertEqual(
			FarmerRegistrationForm(**SAMPLE_FORM, income_source="crop, other").income_source, "crop other"
		)
		for field, bad in (
			("marital_status", "engaged"),
			("education_level", "phd"),
			("income_source", "mining"),
		):
			with self.assertRaises(ValidationError, msg=field):
				FarmerRegistrationForm(**SAMPLE_FORM, **{field: bad})

	def test_read_schema_mirrors_registration_fields(self):
		"""What a DA registers can be read back under the same names (read/write symmetry)."""
		from da_services.integrations.farmer_registry.mapper import reference_dict_to_profile
		from da_services.integrations.farmer_registry.schemas import Farmer

		farmer = Farmer(farmer_id="f-1", marital_status="married", disabled=False, is_psnp_user=True)
		self.assertEqual(farmer.marital_status, "married")
		self.assertIs(farmer.disabled, False)
		profile = reference_dict_to_profile(
			{"farmer_id": "f-1", "marital_status": "married", "disabled": 0, "is_psnp_beneficiary": 1}, {}
		)
		self.assertEqual(profile["marital_status"], "married")
		self.assertIs(profile["disabled"], False)
		self.assertIs(profile["is_psnp_user"], True)
