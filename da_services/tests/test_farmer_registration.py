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
		self.assertEqual(payload["header"]["sender_id"], "farmer-partner")
		self.assertEqual(payload["header"]["signature"], "STAFF_AUTHENTICATED")
		self.assertIn("payload", payload["message"])

	def test_personal_and_location_mapping(self):
		inner = da_form_to_ingest_payload(SAMPLE_FORM)["message"]["payload"]
		personal = inner["basic_info"]["personal_info"]
		self.assertEqual(personal["first_name_english"], "Abebe")
		self.assertEqual(personal["father_name_english"], "Kebede")
		self.assertEqual(personal["primary_phone_number"], "+251911234567")
		self.assertEqual(inner["basic_info"]["locale_info"]["kebele"], "40801101001")
		self.assertEqual(inner["register_mnemonic"], "Farmer")

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

	def test_names_follow_the_registry_letters_only_rule(self):
		"""The registry rejects "suresh1" after creating a draft; catch it at the boundary instead."""
		from pydantic import ValidationError

		for bad in ({"first_name": "suresh1"}, {"father_name": "K3bede"}, {"grandfather_name": "Tes@faye"}):
			with self.assertRaises(ValidationError, msg=bad) as ctx:
				self._form(**bad)
			self.assertIn("letters only", str(ctx.exception))
		ok = self._form(first_name="DA-TEST Abebe", father_name="O'Connor", grandfather_name="Tesfaye Haile")
		self.assertEqual(ok.first_name, "DA-TEST Abebe")
		member = {
			"first_name": "Almaz1",
			"grandfather_name": "Haile",
			"gender": "female",
			"date_of_birth": "1990-01-01",
			"relationship": "spouse",
		}
		with self.assertRaises(ValidationError):
			self._form(household={"size": 2, "members": [member]})

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

	def setUp(self):
		# Idempotency is a local queue concern. The live registry rejects this
		# fixture payload, so the case stays on the mock client.
		self._farmer_backend = frappe.conf.get("farmer_registry_backend")
		frappe.conf.farmer_registry_backend = "mock"

	def tearDown(self):
		if self._farmer_backend is None:
			frappe.conf.pop("farmer_registry_backend", None)
		else:
			frappe.conf.farmer_registry_backend = self._farmer_backend

	def test_duplicate_submission_returns_first_event(self):
		import uuid
		from unittest.mock import patch

		from da_services.services import farmers as svc

		op_id = f"test-{uuid.uuid4().hex[:12]}"
		form = {**SAMPLE_FORM, "client_op_id": op_id}

		# Registration follows farmer_registry_backend. This test only checks idempotency.
		with patch(
			"da_services.services.farmers.get_farmer_registry_client",
			return_value=MockFarmerRegistryClient(),
		):
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


REGISTRY_REJECTED = {
	"success": False,
	"correlation_id": None,
	"status": "ERROR",
	"error_code": "SYS-ERR-001",
	"error_message": "UNEXPECTED_ERROR",
}


class TestRegisterRegistryRejection(IntegrationTestCase):
	"""A registry ERROR (e.g. SYS-ERR-001) is a failed registration, not a success."""

	def setUp(self):
		frappe.response.pop("http_status_code", None)
		# These cases exercise the ingest path; a site switched to intake must not change them.
		self._saved_mode = frappe.conf.get("farmer_registry_register_mode")
		frappe.conf.farmer_registry_register_mode = "ingest"

	def tearDown(self):
		frappe.response.pop("http_status_code", None)
		if self._saved_mode is None:
			frappe.conf.pop("farmer_registry_register_mode", None)
		else:
			frappe.conf.farmer_registry_register_mode = self._saved_mode

	def _client(self, result):
		from unittest.mock import MagicMock

		client = MagicMock()
		client.register_farmer.return_value = result
		return client

	def test_service_marks_event_failed_and_returns_registry_error(self):
		from unittest.mock import patch

		from da_services.services import farmers as svc

		with patch(
			"da_services.services.farmers.get_farmer_registry_client",
			return_value=self._client(REGISTRY_REJECTED),
		):
			result = svc.register_farmer(dict(SAMPLE_FORM), ctx=None)

		self.assertFalse(result["success"])
		self.assertEqual(result["error_code"], "SYS-ERR-001")
		self.assertEqual(result["error_message"], "UNEXPECTED_ERROR")
		event = frappe.db.get_value(
			"DA Integration Event", result["event_id"], ["status", "last_error"], as_dict=True
		)
		self.assertEqual(event.status, "Failed")
		self.assertEqual(event.last_error, "UNEXPECTED_ERROR")

	def test_route_returns_502_error_when_registry_rejects(self):
		from unittest.mock import patch

		from da_services.api.v1 import farmers as api

		frappe.local.form_dict = frappe._dict(first_name="Abebe", gender="male")
		with (
			patch.object(api, "require_roles", return_value=None),
			patch(
				"da_services.services.farmers.get_farmer_registry_client",
				return_value=self._client(REGISTRY_REJECTED),
			),
		):
			res = api.register_farmer()

		self.assertEqual(frappe.response.get("http_status_code"), 502)
		self.assertEqual(res["status"], "error")
		self.assertEqual(res["code"], "FARMER_REGISTRY_REJECTED")
		self.assertIn("UNEXPECTED_ERROR", res["message"])
		self.assertEqual(res["details"]["registry_error_code"], "SYS-ERR-001")
		self.assertTrue(res["details"]["event_id"])

	def test_route_still_succeeds_when_registry_accepts(self):
		from unittest.mock import patch

		from da_services.api.v1 import farmers as api

		frappe.local.form_dict = frappe._dict(first_name="Abebe", gender="male")
		with (
			patch.object(api, "require_roles", return_value=None),
			patch(
				"da_services.services.farmers.get_farmer_registry_client",
				return_value=MockFarmerRegistryClient(),
			),
		):
			res = api.register_farmer()

		self.assertIsNone(frappe.response.get("http_status_code"))
		self.assertEqual(res["status"], "success")
		self.assertTrue(res["data"]["correlation_id"])
