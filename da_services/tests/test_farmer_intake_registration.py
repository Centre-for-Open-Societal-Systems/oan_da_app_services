"""Farmer registration through the registry intake form (farmer_registry_register_mode = "intake").

Mapper and orchestration run against the mock client, whose form layout and
code lists are trimmed from the dev registry; the HTTP client is checked
against stubbed Staff API envelopes.
"""

from io import BytesIO
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from da_services.integrations.farmer_registry import intake
from da_services.integrations.farmer_registry.client import FarmerRegistryServerError
from da_services.integrations.farmer_registry.mock import MockFarmerRegistryClient
from da_services.services import constants as C

FORM = {
	"first_name": "Abebe",
	"father_name": "Kebede",
	"grandfather_name": "Tesfaye",
	"gender": "male",
	"date_of_birth": "1985-04-12",
	"phone": "+251911234567",
	"kebele": "ET04080101",
	"national_id": "1234567890123456",
	"location": "9.03 38.74 2350 5",
	"marital_status": "married",
	"education_level": "read_write",
	"income_sources": ["crop", "livestock"],
	"household": {
		"size": 2,
		"members": [
			{
				"first_name": "Almaz",
				"grandfather_name": "Bekele",
				"gender": "female",
				"date_of_birth": "1990-01-01",
				"relationship": "spouse",
			}
		],
	},
	"identity_document_id": "doc-00000123",
	"lands": [{"ownership": "owner", "area_hectare": 1.5}],
	"crops": [{"name": "teff", "planted_date": "2026-07-01", "season": "meher"}],
	"livestock": [{"animal": "cattle", "count": 3, "system": "mixed"}],
	"da_id": "DA-ID-001",
}


def _plan(form=None):
	client = MockFarmerRegistryClient()
	return intake.build_intake_sections(
		form or FORM,
		intake.load_layout(client),
		intake.load_code_lists(client),
		intake.resolve_geo_path(client, (form or FORM)["kebele"]),
	)


def _by_section(sections):
	return {s["section_id"]: s for s in sections}


class TestIntakeMapping(IntegrationTestCase):
	def setUp(self):
		intake.clear_cache()

	def test_household_is_saved_first_and_empty_registers_are_skipped(self):
		ids = [s["section_id"] for s in _plan()]
		self.assertEqual(ids[0], "oan_household_information")
		self.assertNotIn("oan_farmer_farm_inputs_intake", ids)  # not on the DA form
		self.assertIn("oan_farmer_documents", ids)

	def test_membership_is_always_sent_and_defaults_to_no(self):
		"""The registry refuses to finalize without the three membership answers (30 Oct 2026)."""
		by_id = {s["section_id"]: s for s in _plan()}
		row = by_id["oan_farmer_membership"]["section_payload"][0]
		self.assertEqual(
			(
				row["is_primary_cooperative_member"],
				row["is_cooperative_union_member"],
				row["is_farmer_cluster_member"],
			),
			(False, False, False),
		)
		farmer_row = by_id["oan_farmer_name"]["section_payload"][0]
		self.assertEqual(row["link_internal_record_id"], farmer_row["internal_record_id"])

		member = _plan(
			{
				**FORM,
				"membership": {
					"is_primary_cooperative_member": True,
					"primary_cooperative_name": "Bako Coop",
				},
			}
		)
		row = {s["section_id"]: s for s in member}["oan_farmer_membership"]["section_payload"][0]
		self.assertTrue(row["is_primary_cooperative_member"])
		self.assertEqual(row["primary_cooperative_name"], "Bako Coop")
		self.assertNotIn("cooperative_union_name", row)

	def test_farmer_row_uses_registry_names_and_codes(self):
		farmer = _by_section(_plan())["oan_farmer_name"]["section_payload"][0]
		self.assertEqual(
			(farmer["first_name"], farmer["middle_name"], farmer["last_name"]), ("Abebe", "Kebede", "Tesfaye")
		)
		self.assertEqual(farmer["gender"], "MALE")
		self.assertEqual(farmer["marital_status"], "MARRIED")
		self.assertEqual(farmer["education_level"], "CAN_READ_AND_WRITE")  # ODK word via synonym
		self.assertEqual(farmer["income_sources"], ["SOI_CROP_PRODUCTION", "SOI_LIVESTOCK_PRODUCTION"])
		self.assertEqual(farmer["identity_document_type"], "IDDOC_FAYDA")
		self.assertEqual(farmer["foundational_id"], "1234567890123456")
		self.assertEqual(farmer["geo_lowest_level_value_id"], "ET04080101")
		self.assertEqual(
			[h["level_value_id"] for h in farmer["geo_code_hierarchy_json"]["hierarchy"]],
			["ET04", "ET0408", "ET040801", "ET04080101"],
		)
		self.assertEqual((farmer["latitude"], farmer["longitude"]), (9.03, 38.74))
		self.assertTrue(farmer["is_household_head"])

	def test_household_counts_match_members_and_farmer_is_head(self):
		sections = _by_section(_plan())
		household = sections["oan_household_information"]["section_payload"][0]
		members = sections["household_member_section"]["section_payload"]
		self.assertEqual(household["size_of_group"], 2)
		self.assertEqual((household["number_of_male_members"], household["number_of_female_members"]), (1, 1))
		self.assertFalse(household["other_land_owner"])
		head = members[0]
		self.assertEqual(
			(head["first_name"], head["is_head"], head["relationship_to_the_head"]), ("Abebe", True, "SELF")
		)
		self.assertEqual((members[1]["is_head"], members[1]["relationship_to_the_head"]), (False, "SPOUSE"))

	def test_rows_link_to_each_other(self):
		sections = _by_section(_plan())
		farmer = sections["oan_farmer_name"]["section_payload"][0]
		household = sections["oan_household_information"]["section_payload"][0]
		land = sections["oan_farmer_lands"]["section_payload"][0]
		self.assertEqual(farmer["link_internal_record_id"], household["internal_record_id"])
		self.assertEqual(household["link_internal_record_id"], farmer["internal_record_id"])
		for member in sections["household_member_section"]["section_payload"]:
			self.assertEqual(member["link_internal_record_id"], household["internal_record_id"])
		self.assertEqual(land["link_internal_record_id"], farmer["internal_record_id"])
		self.assertEqual(sections["oan_farmer_crops_intake"]["section_payload"][0]["commodity"], "CROP_TEFF")
		self.assertEqual(
			sections["oan_farmer_crops_intake"]["section_payload"][0]["link_internal_record_id"],
			land["internal_record_id"],
		)
		cattle = sections["oan_farmer_livestock_intake"]["section_payload"][0]
		self.assertEqual((cattle["livestock_type"], cattle["head_count"]), ("LSTK_CATTLE", 3))
		# Every Farmer section carries the same farmer row.
		self.assertEqual(sections["oan_farmer_location"]["section_payload"][0], farmer)

	def test_identity_document_rides_on_the_documents_section(self):
		sections = _by_section(_plan())
		self.assertEqual(
			sections["oan_farmer_documents"]["documents"],
			[{"document_id": "doc-00000123", "label": "identity_document_1"}],
		)
		self.assertNotIn("documents", sections["oan_farmer_name"])

	def test_phone_is_normalised(self):
		phone = _by_section(_plan({**FORM, "phone": "911234567"}))["oan_farmer_phones"]["section_payload"][0]
		self.assertEqual(
			(phone["phone_number"], phone["phone_type"], phone["is_primary"]),
			("0911234567", "PHONE_PRIMARY", True),
		)
		self.assertEqual(intake.normalize_phone("251911234567"), "+251911234567")

	def test_missing_fields_are_listed_together(self):
		form = {
			k: v
			for k, v in FORM.items()
			if k not in ("grandfather_name", "identity_document_id", "marital_status")
		}
		with self.assertRaises(intake.IntakeFormError) as cm:
			_plan(form)
		text = str(cm.exception)
		for word in ("grandfather_name", "identity_document_id", "marital_status"):
			self.assertIn(word, text)

	def test_household_size_must_match_members(self):
		with self.assertRaises(intake.IntakeFormError) as cm:
			_plan({**FORM, "household": {"size": 4, "members": []}})
		self.assertIn("size is 4 but 1", str(cm.exception))

	def test_crops_need_a_land(self):
		with self.assertRaises(intake.IntakeFormError) as cm:
			_plan({**FORM, "lands": []})
		self.assertIn("lands", str(cm.exception))

	def test_unknown_code_names_the_field_and_allowed_values(self):
		with self.assertRaises(intake.IntakeFormError) as cm:
			_plan({**FORM, "crops": [{"name": "coffee"}]})
		self.assertIn("crops[0].name: 'coffee'", str(cm.exception))
		self.assertIn("CROP_TEFF", str(cm.exception))

	def test_kebele_must_be_a_master_data_id_that_exists(self):
		client = MockFarmerRegistryClient()
		with self.assertRaises(intake.IntakeFormError):
			intake.resolve_geo_path(client, "40801101001")  # ODK P-code
		with self.assertRaises(intake.IntakeFormError):
			intake.resolve_geo_path(client, "ET04080199")  # not in Master Data


class TestIntakeSubmit(IntegrationTestCase):
	def setUp(self):
		intake.clear_cache()

	def test_sections_chain_one_draft_and_finalize(self):
		client = MagicMock(wraps=MockFarmerRegistryClient())
		drafts = []
		result = intake.submit_plan(client, _plan(), on_draft=drafts.append)
		self.assertTrue(result["success"])
		self.assertEqual(result["status"], "PENDING_APPROVAL")
		calls = [c.args[0] for c in client.save_intake_section.call_args_list]
		self.assertIsNone(calls[0]["submission_id"])
		self.assertTrue(all(c["submission_id"] == result["submission_id"] for c in calls[1:]))
		self.assertEqual(drafts, [result["submission_id"]])
		client.finalize_intake_submission.assert_called_once_with(result["submission_id"])

	def test_registry_rejection_reports_the_section(self):
		client = MockFarmerRegistryClient()
		real_save = client.save_intake_section

		def save(section):
			if section["section_id"] == "household_member_section":
				return {
					"success": False,
					"status": "ERROR",
					"error_code": "VAL-001",
					"error_message": "Household Members: no member is marked as the head.",
				}
			return real_save(section)

		client.save_intake_section = save
		result = intake.submit_plan(client, _plan())
		self.assertFalse(result["success"])
		self.assertEqual(result["failed_section"], "household_member_section")
		self.assertIn("no member is marked as the head", result["error_message"])
		self.assertTrue(result["submission_id"])  # draft exists, kept for follow-up


class TestRegisterFarmerIntakeMode(IntegrationTestCase):
	def setUp(self):
		intake.clear_cache()
		self._mode = frappe.conf.get("farmer_registry_register_mode")
		frappe.conf["farmer_registry_register_mode"] = "intake"
		frappe.response.pop("http_status_code", None)

	def tearDown(self):
		if self._mode is None:
			frappe.conf.pop("farmer_registry_register_mode", None)
		else:
			frappe.conf["farmer_registry_register_mode"] = self._mode
		frappe.response.pop("http_status_code", None)

	def _register(self, form, client=None):
		from da_services.services import farmers as svc

		with patch(
			"da_services.services.farmers.get_farmer_registry_client",
			return_value=client or MockFarmerRegistryClient(),
		):
			return svc.register_farmer(dict(form), ctx=None)

	def test_success_records_submission_on_the_event(self):
		result = self._register(FORM)
		self.assertTrue(result["success"])
		self.assertEqual(result["status"], "PENDING_APPROVAL")
		self.assertTrue(result["submission_id"].startswith("mock-sub-"))
		event = frappe.db.get_value(
			"DA Integration Event", result["event_id"], ["status", "external_reference"], as_dict=True
		)
		self.assertEqual((event.status, event.external_reference), ("Completed", result["submission_id"]))

	def test_bad_form_is_rejected_before_an_event_is_recorded(self):
		before = frappe.db.count("DA Integration Event", {"event_type": "Farmer Registration"})
		with self.assertRaises(frappe.ValidationError):
			self._register({**FORM, "identity_document_id": None})
		self.assertEqual(
			frappe.db.count("DA Integration Event", {"event_type": "Farmer Registration"}), before
		)

	def test_rejected_section_fails_the_event_and_deletes_the_draft(self):
		"""The half-filled draft would show up as a second farmer to registry staff; it is removed."""
		client = MockFarmerRegistryClient()
		real_save = client.save_intake_section
		client.save_intake_section = lambda s: (
			{"success": False, "status": "ERROR", "error_code": "V", "error_message": "Phone Number: bad"}
			if s["section_id"] == "oan_farmer_phones"
			else real_save(s)
		)
		result = self._register(FORM, client)
		self.assertFalse(result["success"])
		self.assertEqual(result["failed_section"], "oan_farmer_phones")
		event = frappe.db.get_value(
			"DA Integration Event",
			result["event_id"],
			["status", "last_error", "external_reference"],
			as_dict=True,
		)
		self.assertEqual(event.status, "Failed")
		self.assertTrue(event.external_reference.startswith("mock-sub-"))
		self.assertEqual(
			event.last_error,
			f"oan_farmer_phones: Phone Number: bad (draft {event.external_reference} deleted)",
		)
		self.assertEqual(client.deleted_submissions, {event.external_reference})

	def test_draft_delete_failure_is_noted_not_raised(self):
		client = MockFarmerRegistryClient()
		real_save = client.save_intake_section
		client.save_intake_section = lambda s: (
			{"success": False, "status": "ERROR", "error_code": "V", "error_message": "bad"}
			if s["section_id"] == "oan_farmer_phones"
			else real_save(s)
		)

		def boom(_id):
			raise FarmerRegistryServerError("registry down")

		client.delete_intake_submission = boom
		result = self._register(FORM, client)
		self.assertFalse(result["success"])
		last_error = frappe.db.get_value("DA Integration Event", result["event_id"], "last_error")
		self.assertIn("kept: registry down", last_error)

	def test_registration_details_route(self):
		from types import SimpleNamespace

		from da_services.api.v1 import farmers as api

		client = MockFarmerRegistryClient()
		submitted = self._register(FORM, client)
		admin = SimpleNamespace(unrestricted=True, has_any_role=lambda *r: False)
		with (
			patch.object(api, "require_roles", return_value=admin),
			patch("da_services.services.farmers.get_farmer_registry_client", return_value=client),
		):
			out = api.get_registration(submitted["submission_id"])
			self.assertEqual(out["status"], "success", out)
			data = out["data"]
			self.assertEqual(data["status"], "PENDING_APPROVAL")
			self.assertEqual(data["record_name"], "Abebe Tesfaye")
			self.assertEqual(data["event"]["event_id"], submitted["event_id"])
			self.assertEqual(data["event"]["status"], "Completed")
			self.assertEqual(
				[s["section_id"] for s in data["sections"]], ["oan_farmer_name", "oan_farmer_phones"]
			)
			self.assertNotIn("search_text", data["sections"][0]["records"][0])
			self.assertEqual(data["sections"][1]["records"][0]["phone_number"], "911234567")

			missing = api.get_registration("not-a-mock-submission")
			self.assertEqual((missing["status"], missing["code"]), ("error", "NOT_FOUND"))

		# A DA sees only their own submissions; an unknown one is 404, not a registry call.
		da = SimpleNamespace(unrestricted=False, has_any_role=lambda *r: C.ROLE_DA in r)
		with (
			patch.object(api, "require_roles", return_value=da),
			patch("da_services.services.farmers.get_farmer_registry_client", return_value=client),
		):
			not_ours = api.get_registration("mock-sub-someone-else")
			self.assertEqual((not_ours["status"], not_ours["code"]), ("error", "NOT_FOUND"))

	def test_route_returns_502_with_failed_section(self):
		from da_services.api.v1 import farmers as api

		client = MockFarmerRegistryClient()
		real_save = client.save_intake_section
		client.save_intake_section = lambda s: (
			{
				"success": False,
				"status": "ERROR",
				"error_code": "V",
				"error_message": "Household: Other Land Owner are empty",
			}
			if s["section_id"] == "oan_household_information"
			else real_save(s)
		)
		frappe.local.form_dict = frappe._dict(FORM)
		with (
			patch.object(api, "require_roles", return_value=None),
			patch("da_services.services.farmers.get_farmer_registry_client", return_value=client),
		):
			res = api.register_farmer()
		self.assertEqual(frappe.response.get("http_status_code"), 502)
		self.assertEqual(res["code"], "FARMER_REGISTRY_REJECTED")
		self.assertIn("Other Land Owner", res["message"])
		self.assertEqual(res["details"]["failed_section"], "oan_household_information")

	def test_unknown_mode_is_a_config_error(self):
		frappe.conf["farmer_registry_register_mode"] = "carrier-pigeon"
		with self.assertRaises(frappe.ValidationError):
			self._register(FORM)


class TestRegistrationDocumentUpload(IntegrationTestCase):
	def tearDown(self):
		frappe.local.request = None

	def _upload(self, data):
		from da_services.api.v1 import farmers as api

		builder = EnvironBuilder(
			method="POST", path="/api/v1/da-services/farmers/registrations/documents", data=data
		)
		frappe.local.request = Request(builder.get_environ())
		with (
			patch.object(api, "require_roles", return_value=None),
			patch(
				"da_services.services.farmers.get_farmer_registry_client",
				return_value=MockFarmerRegistryClient(),
			),
		):
			return api.upload_registration_document()

	def test_pdf_upload_returns_document_id(self):
		out = self._upload({"file": (BytesIO(b"%PDF-1.4 test"), "fayda.pdf", "application/pdf")})
		self.assertEqual(out["status"], "success", out)
		self.assertTrue(out["data"]["document_id"].startswith("mock-doc-"))

	def test_file_name_is_made_registry_safe(self):
		"""Phone photos arrive as "WhatsApp Image 2026-10-29 at 7.46.44 PM.jpeg"; the registry wants one dot."""
		from da_services.services.farmers import registry_document_name

		self.assertEqual(
			registry_document_name("WhatsApp Image 2026-10-29 at 7.46.44 PM.jpeg", "image/jpeg"),
			"WhatsApp-Image-2026-10-29-at-7-46-44-PM.jpg",
		)
		self.assertEqual(registry_document_name("fayda.pdf", "application/pdf"), "fayda.pdf")
		self.assertEqual(registry_document_name("", "image/png"), "identity-document.png")
		self.assertEqual(registry_document_name("C:\\Users\\x\\my id..png", "image/png"), "my-id.png")
		out = self._upload(
			{"file": (BytesIO(b"\xff\xd8\xff"), "WhatsApp Image 2026-10-29 at 7.46.44 PM.jpeg", "image/jpeg")}
		)
		self.assertEqual(out["status"], "success", out)
		self.assertEqual(out["data"]["file_name"], "WhatsApp-Image-2026-10-29-at-7-46-44-PM.jpg")

	def test_other_file_types_are_rejected(self):
		out = self._upload({"file": (BytesIO(b"MZ"), "id.exe", "application/octet-stream")})
		self.assertEqual(out["status"], "error")

	def test_missing_file_is_rejected(self):
		self.assertEqual(self._upload({})["status"], "error")


# ---------------------------------------------------------------------------
# HTTP client against stubbed Staff API / Master Data envelopes
# ---------------------------------------------------------------------------

CONF_KEYS = (
	"farmer_registry_url",
	"farmer_registry_master_data_url",
	"farmer_registry_api_key",
	"farmer_registry_keycloak",
	"farmer_registry_ingest",
)


def _envelope(payload, status="SUCCESS", error=None):
	return {
		"response_header": {
			"response_status": status,
			"response_error_code": "E" if error else None,
			"response_error_message": error,
		},
		"response_body": {"response_payload": payload, "pagination_response": {}},
	}


class _Resp:
	def __init__(self, body, status_code=200):
		self._body, self.status_code, self.url, self.text = body, status_code, "https://x", str(body)

	def json(self):
		return self._body


class TestHttpIntake(IntegrationTestCase):
	def setUp(self):
		self._saved = {k: frappe.conf.get(k) for k in CONF_KEYS}
		frappe.conf.update(
			{
				"farmer_registry_url": "https://registry.test",
				"farmer_registry_master_data_url": "https://master.test",
				"farmer_registry_api_key": "static-test-token",
				"farmer_registry_keycloak": {},
				"farmer_registry_ingest": {"register_id": "reg-farmer", "intake_form_id": "form-1"},
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

	@patch("da_services.integrations.farmer_registry.http.requests.post")
	def test_save_and_finalize_use_the_staff_envelope(self, post):
		post.side_effect = [
			_Resp(_envelope({"submission_id": "sub-1"})),
			_Resp(
				_envelope({"submission_id": "sub-1", "draft_status": "FINAL", "approval_status": "PENDING"})
			),
		]
		saved = self.client.save_intake_section(
			{"submission_id": None, "section_id": "s1", "section_payload": [{}]}
		)
		self.assertEqual((saved["success"], saved["submission_id"]), (True, "sub-1"))
		url, kwargs = post.call_args_list[0].args[0], post.call_args_list[0].kwargs
		self.assertEqual(url, "https://registry.test/intake-form-data/save_intake_form_submission")
		self.assertEqual(kwargs["json"]["request_body"]["request_payload"]["section_id"], "s1")
		self.assertEqual(kwargs["headers"]["X-CSRF-Token"], kwargs["cookies"]["X-CSRF-Token"])

		final = self.client.finalize_intake_submission("sub-1")
		self.assertEqual((final["draft_status"], final["approval_status"]), ("FINAL", "PENDING"))

	@patch("da_services.integrations.farmer_registry.http.requests.post")
	def test_validation_error_comes_back_readable(self, post):
		post.return_value = _Resp(
			_envelope(None, status="ERROR", error="Household: Other Land Owner are empty.")
		)
		saved = self.client.save_intake_section({"section_id": "s1"})
		self.assertFalse(saved["success"])
		self.assertEqual(saved["error_message"], "Household: Other Land Owner are empty.")

	@patch("da_services.integrations.farmer_registry.http.requests.post")
	def test_upload_is_multipart_without_json_content_type(self, post):
		post.return_value = _Resp(_envelope([{"document_id": "doc-9"}]))
		self.assertEqual(self.client.upload_document("id.pdf", b"%PDF", "application/pdf"), "doc-9")
		kwargs = post.call_args.kwargs
		self.assertEqual(kwargs["files"]["documents"], ("id.pdf", b"%PDF", "application/pdf"))
		self.assertNotIn("Content-Type", kwargs["headers"])
		self.assertNotIn("json", kwargs)

	@patch("da_services.integrations.farmer_registry.http.requests.post")
	def test_master_data_calls_go_to_master_data_host(self, post):
		post.return_value = _Resp(_envelope({"attribute_values": [{"value_id": "MALE"}]}))
		self.assertEqual(self.client.get_attribute_values("GENDER"), [{"value_id": "MALE"}])
		self.assertEqual(post.call_args.args[0], "https://master.test/attributes/get_attribute_values")

	def test_master_data_url_is_required_for_intake(self):
		from da_services.integrations.farmer_registry.client import FarmerRegistryError

		frappe.conf["farmer_registry_master_data_url"] = None
		from da_services.integrations.farmer_registry.http import HTTPFarmerRegistryClient

		with self.assertRaises(FarmerRegistryError):
			HTTPFarmerRegistryClient().get_geo_levels()
