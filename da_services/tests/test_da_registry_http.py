"""DA Registry HTTP client against the staff API contract.

requests.post is stubbed with a small fake registry (Keycloak token endpoint plus
search_in_a_register, get_tab_records and create_change_request answering OpenG2P
envelopes shaped like the 31 Oct 2026 local-stack responses), so the client, mapper,
auth and error mapping are exercised without a live registry.
"""

import json
from unittest.mock import patch

import requests
from frappe.tests import IntegrationTestCase

from da_services.integrations.da_registry import http as http_mod
from da_services.integrations.da_registry.client import (
	DANotFound,
	DARegistryError,
	DARegistryUnavailable,
)
from da_services.integrations.da_registry.http import DEFAULT_REGISTER_ID, HttpDARegistryClient
from da_services.integrations.da_registry.schemas import ProfileUpdate
from da_services.services import constants as C

BASE = "https://staff-da.example"
TOKEN_URL = "https://kc.example/realms/staff/protocol/openid-connect/token"


def _record(da_id, internal_id, *, woreda="ET010101", status="ACTIVE", **extra):
	rec = {
		"internal_record_id": internal_id,
		"functional_record_id": da_id,
		"record_name": "Abebe Kebede Tesfaye",
		"record_status": status,
		"first_name": "Abebe",
		"middle_name": "Kebede",
		"last_name": "Tesfaye",
		"mobile_number": "0911000001",
		"email": "abebe@example.org",
		"education_level": "DIPLOMA",
		"specialisation": "CROP_PRODUCTION",
		"position": "DEVELOPMENT_AGENT",
		"joined_date": "2020-09-11",
		"geo_lowest_level_value_id": woreda + "01",
		"region_code": woreda[:4],
		"zone_code": woreda[:6],
		"woreda_code": woreda,
		"last_approved_at": "2026-10-30T08:07:31.627971",
	}
	rec.update(extra)
	return rec


class _Resp:
	def __init__(self, status, body, url="", text=""):
		self.status_code = status
		self._body = body
		self.url = url
		self.text = text
		self.headers = {}

	def json(self):
		if isinstance(self._body, Exception):
			raise self._body
		return self._body


def _ok(body):
	return {"response_header": {"response_status": "SUCCESS"}, "response_body": body}


def _err(message):
	return {
		"response_header": {"response_status": "ERROR", "response_error_message": message},
		"response_body": {},
	}


class FakeRegistry:
	"""Answers the calls HttpDARegistryClient makes; records every request."""

	def __init__(self, records, page_size=2):
		self.records = {r["internal_record_id"]: r for r in records}
		self.page_size = page_size
		self.calls = []  # (path, request_payload, pagination, headers, cookies)
		self.token_calls = 0
		self.fail_next = None  # (status, body) for the next staff call
		self.change_answer = None
		self.uploads = []

	def __call__(self, url, json=None, data=None, headers=None, cookies=None, timeout=None, files=None):
		if url == TOKEN_URL:
			self.token_calls += 1
			return _Resp(200, {"access_token": f"tok-{self.token_calls}", "expires_in": 300})
		path = url[len(BASE) :]
		if files:  # multipart document upload: no JSON envelope
			self.uploads.append((path, data, files, headers, cookies))
			name = files["documents"][0]
			return _Resp(
				200,
				_ok({"response_payload": {"documents": [{"document_id": "doc-1", "source_filename": name}]}}),
				url,
			)
		body = json["request_body"]
		self.calls.append((path, body["request_payload"], body.get("pagination_request"), headers, cookies))
		if self.fail_next:
			status, answer = self.fail_next
			self.fail_next = None
			return _Resp(status, answer, url)
		return _Resp(200, getattr(self, "_" + path.rsplit("/", 1)[-1])(body), url)

	def _search_in_a_register(self, body):
		p = body["pagination_request"]
		filters = json.loads(p["filter_by"]) if p["filter_by"] else {}
		statuses = filters.get("record_status", {}).get("in", ["ACTIVE"])
		rows = [
			r
			for r in self.records.values()
			if p["search_text"] in r["functional_record_id"]
			and r["record_status"] in statuses
			and all(r.get(f) == cond["eq"] for f, cond in filters.items() if "eq" in cond)
		]
		start = (p["current_page"] - 1) * self.page_size
		page = rows[start : start + self.page_size]
		pages = max(1, -(-len(rows) // self.page_size))
		return _ok(
			{
				"pagination_response": {"number_of_items": len(rows), "number_of_pages": pages},
				"response_payload": [
					{
						"internal_record_id": r["internal_record_id"],
						"functional_record_id": r["functional_record_id"],
					}
					for r in page
				],
			}
		)

	def _get_tab_records(self, body):
		rec = self.records.get(body["request_payload"]["subject_record_id"])
		if not rec:
			return _err("Record not found")
		return _ok(
			{
				"response_payload": [
					{"section_register_id": DEFAULT_REGISTER_ID, "is_list": False, "records": [rec]}
				]
			}
		)

	def _create_change_request(self, body):
		return self.change_answer or _ok(
			{"response_payload": {"change_request_id": "cr-1", "approval_status": "PENDING"}}
		)

	def _enqueue_import_file(self, body):
		return _ok({"response_payload": {"import_file_id": "imp-1"}})

	def _search_exceptions(self, body):
		row = {"source": "file_import", "fayda_id": "9311", "name": "X", "exception_type": "INVALID_KEBELE"}
		return _ok(
			{"pagination_response": {"number_of_items": 1, "number_of_pages": 1}, "response_payload": [row]}
		)


class TestHttpDARegistryClient(IntegrationTestCase):
	def setUp(self):
		http_mod._TOKEN_CACHE.clear()
		self.registry = FakeRegistry(
			[
				_record("DA-3821981536", "rec-1"),
				# partial search hit for "DA-382198153": must not be taken for DA-3821981536
				_record("DA-38219815360", "rec-2"),
				_record("DA-1111111111", "rec-3", woreda="ET040301"),
				_record("DA-2222222222", "rec-4", status="INACTIVE"),
				_record("TEMP-0001", "rec-5"),
			]
		)
		patcher = patch.object(http_mod.requests, "post", side_effect=self.registry)
		patcher.start()
		self.addCleanup(patcher.stop)
		self.client = HttpDARegistryClient(
			BASE, keycloak={"token_url": TOKEN_URL, "client_id": "da-services-backend", "client_secret": "s"}
		)

	# -- reads ---------------------------------------------------------------------------

	def test_get_da_matches_the_da_id_exactly_and_maps_the_record(self):
		ref = self.client.get_da("DA-3821981536")
		self.assertEqual(ref.da_id, "DA-3821981536")
		self.assertEqual(ref.full_name, "Abebe Kebede Tesfaye")
		self.assertEqual(ref.active_status, C.ACTIVE_STATUS_ACTIVE)
		self.assertEqual(ref.approval_status, "Published")
		self.assertEqual(
			(ref.region, ref.zone, ref.woreda, ref.kebele), ("ET01", "ET0101", "ET010101", "ET01010101")
		)
		self.assertEqual((ref.education_tier, ref.specialisation), ("Diploma", "Crop production"))
		self.assertEqual(
			(ref.phone, ref.email, ref.joined_at), ("0911000001", "abebe@example.org", "2020-09-11")
		)
		self.assertIsNone(ref.fayda_status)
		tab = [c for c in self.registry.calls if c[0] == "/register-data/get_tab_records"]
		self.assertEqual(tab[0][1]["subject_record_id"], "rec-1")
		self.assertEqual(tab[0][1]["tab_id"], "da_details_tab")

	def test_get_da_finds_inactive_records_as_out_of_service(self):
		ref = self.client.get_da("DA-2222222222")
		self.assertEqual(ref.active_status, C.ACTIVE_STATUS_SEPARATED)

	def test_unknown_da_raises_not_found(self):
		with self.assertRaises(DANotFound):
			self.client.get_da("DA-9999999999")
		self.assertFalse(self.client.validate_da_id("DA-9999999999"))

	def test_list_das_filters_by_woreda_code_and_walks_every_page(self):
		self.registry.records["rec-6"] = _record("DA-3333333333", "rec-6")
		refs = self.client.list_das(woreda="ET010101")
		self.assertEqual({r.da_id for r in refs}, {"DA-3821981536", "DA-38219815360", "DA-3333333333"})
		searches = [c for c in self.registry.calls if c[0] == "/register-data/search_in_a_register"]
		self.assertEqual([s[2]["current_page"] for s in searches], [1, 2])
		self.assertEqual(json.loads(searches[0][2]["filter_by"]), {"woreda_code": {"eq": "ET010101"}})

	def test_list_das_by_region_skips_temporary_ids(self):
		refs = self.client.list_das(region="ET01")
		self.assertNotIn("TEMP-0001", {r.da_id for r in refs})
		self.assertTrue(refs)

	# -- auth and transport --------------------------------------------------------------

	def test_every_call_sends_bearer_and_a_matching_csrf_pair(self):
		self.client.get_da("DA-3821981536")
		for path, _payload, _pg, headers, cookies in self.registry.calls:
			self.assertEqual(headers["Authorization"], "Bearer tok-1", path)
			self.assertEqual(headers["X-CSRF-Token"], cookies["X-CSRF-Token"], path)
		self.assertEqual(self.registry.token_calls, 1)  # cached across calls

	def test_expired_token_is_refreshed_once(self):
		self.client.get_da("DA-3821981536")
		self.registry.fail_next = (401, _err("Unauthorized"))
		self.client.get_da("DA-3821981536")
		self.assertEqual(self.registry.token_calls, 2)

	def test_registry_down_is_retryable(self):
		self.registry.fail_next = (503, {})
		with self.assertRaises(DARegistryUnavailable):
			self.client.get_da("DA-3821981536")
		with patch.object(http_mod.requests, "post", side_effect=requests.Timeout("slow")):
			with self.assertRaises(DARegistryUnavailable):
				self.client.get_da("DA-3821981536")

	def test_missing_role_is_not_retryable(self):
		self.registry.fail_next = (403, _err("Insufficient resource_access roles"))
		with self.assertRaises(DARegistryError) as ctx:
			self.client.get_da("DA-3821981536")
		self.assertNotIsInstance(ctx.exception, DARegistryUnavailable)
		self.assertIn("Insufficient resource_access roles", str(ctx.exception))

	# -- change requests -----------------------------------------------------------------

	def _update(self, changes, da_id="DA-3821981536"):
		return ProfileUpdate(da_id=da_id, changes=changes, approver="sup@da.local", reference="PCR-0001")

	def _change_requests(self):
		return [c[1] for c in self.registry.calls if c[0] == "/change-requests/create_change_request"]

	def test_contact_change_becomes_one_change_request(self):
		result = self.client.push_profile_update(
			self._update({"phone": "0914000777", "email": "new@example.org"})
		)
		self.assertTrue(result.accepted, result)
		self.assertEqual(result.external_reference, "cr-1")
		(sent,) = self._change_requests()
		self.assertEqual(sent["section_id"], "da_contact_section_04")
		self.assertEqual(sent["tab_id"], "da_details_tab")
		self.assertEqual(sent["section_register_id"], DEFAULT_REGISTER_ID)
		self.assertEqual(sent["internal_record_id"], "rec-1")
		self.assertEqual(
			sent["change_payload"],
			[
				{
					"internal_record_id": "rec-1",
					"edit_action": "UPDATE",
					"mobile_number": "0914000777",
					"email": "new@example.org",
				}
			],
		)

	def test_qualification_labels_are_sent_as_master_data_codes(self):
		self.client.push_profile_update(
			self._update({"education_tier": "Masters", "specialisation": "Natural resources"})
		)
		(sent,) = self._change_requests()
		self.assertEqual(sent["section_id"], "da_service_section_02")
		self.assertEqual(sent["change_payload"][0]["education_level"], "MASTERS")
		self.assertEqual(sent["change_payload"][0]["specialisation"], "NATURAL_RESOURCES")

	def test_identity_and_unknown_fields_never_reach_the_registry(self):
		for changes in (
			{"first_name": "X"},
			{"fayda_id": "1"},
			{"language": "am"},
			{"phone": "1", "specialisation": "x"},
		):
			result = self.client.push_profile_update(self._update(changes))
			self.assertFalse(result.accepted, changes)
			self.assertFalse(result.retryable, changes)
		self.assertEqual(self._change_requests(), [])

	def test_registry_refusal_is_reported_not_raised(self):
		self.registry.change_answer = _err(
			"REQ-VAL-000 A pending change request already exists for this record and section"
		)
		result = self.client.push_profile_update(self._update({"phone": "0914000777"}))
		self.assertFalse(result.accepted)
		self.assertIn("pending change request already exists", result.message)

	def test_unknown_da_is_not_accepted(self):
		result = self.client.push_profile_update(self._update({"phone": "0914000777"}, da_id="DA-9999999999"))
		self.assertFalse(result.accepted)
		self.assertEqual(self._change_requests(), [])


LOGIN_URL = "https://da-registry.example/api/login"
KC_ACTION = "https://kc.example/realms/staff/login-actions/authenticate?session_code=abc&amp;tab_id=t"
CALLBACK = "https://staff-iam.example/auth/callback?code=xyz"


def _jwt(exp):
	import base64

	body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
	return f"h.{body}.s"


class FakeSession:
	"""Staff portal sign-in: login page, Keycloak form post, IAM callback cookie."""

	def __init__(self, *, location=CALLBACK, status=302):
		self.cookies = requests.cookies.RequestsCookieJar()
		self.location = location
		self.status = status
		self.posted = None

	def get(self, url, **kw):
		if url == LOGIN_URL:
			return _Resp(200, None, url, text=f'<form id="kc-form-login" action="{KC_ACTION}" method="post">')
		if url == CALLBACK:
			self.cookies.set("X-Access-Token", _jwt(4102444800))
			return _Resp(302, None, url)
		raise AssertionError(url)

	def post(self, url, data=None, **kw):
		self.posted = (url, data)
		resp = _Resp(self.status, None, url)
		resp.headers = {"Location": self.location} if self.location else {}
		return resp


class TestStaffLogin(IntegrationTestCase):
	def setUp(self):
		http_mod._TOKEN_CACHE.clear()
		self.client = HttpDARegistryClient(
			BASE, keycloak={"login_url": LOGIN_URL, "username": "da-services-dev", "password": "pw"}
		)

	def test_staff_user_signs_in_through_the_portal(self):
		session = FakeSession()
		with patch.object(http_mod.requests, "Session", return_value=session):
			token = self.client._token()
			self.assertEqual(self.client._token(), token)  # cached
		self.assertEqual(token, _jwt(4102444800))
		url, data = session.posted
		self.assertEqual(url, KC_ACTION.replace("&amp;", "&"))
		self.assertEqual((data["username"], data["password"]), ("da-services-dev", "pw"))

	def test_wrong_password_is_a_configuration_error(self):
		with patch.object(http_mod.requests, "Session", return_value=FakeSession(status=200, location="")):
			with self.assertRaises(DARegistryError) as ctx:
				self.client._token()
		self.assertNotIsInstance(ctx.exception, DARegistryUnavailable)

	def test_pending_keycloak_action_is_reported(self):
		pending = "https://kc.example/realms/staff/login-actions/required-action?execution=VERIFY_PROFILE"
		with patch.object(http_mod.requests, "Session", return_value=FakeSession(location=pending)):
			with self.assertRaises(DARegistryError) as ctx:
				self.client._token()
		self.assertIn("required action", str(ctx.exception))

	def test_service_account_wins_when_both_are_configured(self):
		client = HttpDARegistryClient(
			BASE,
			keycloak={
				"token_url": TOKEN_URL,
				"client_id": "c",
				"client_secret": "s",
				"username": "u",
				"password": "p",
			},
		)
		self.assertFalse(client._staff_login)


class TestHttpAgentImport(IntegrationTestCase):
	def setUp(self):
		http_mod._TOKEN_CACHE.clear()
		self.registry = FakeRegistry([])
		patcher = patch.object(http_mod.requests, "post", side_effect=self.registry)
		patcher.start()
		self.addCleanup(patcher.stop)
		self.client = HttpDARegistryClient(
			BASE, keycloak={"token_url": TOKEN_URL, "client_id": "da-services-backend", "client_secret": "s"}
		)

	def test_file_is_uploaded_then_queued_for_the_da_register(self):
		result = self.client.import_agents_file("agents.csv", b"Salutation,Name\n")
		self.assertTrue(result.accepted)
		self.assertEqual(result.external_reference, "imp-1")
		self.assertEqual(result.raw["document_id"], "doc-1")
		((path, form, files, headers, cookies),) = self.registry.uploads
		self.assertEqual(path, "/documents/upload_documents")
		self.assertEqual(form, {"bucket": "import-files"})
		self.assertEqual(files["documents"], ("agents.csv", b"Salutation,Name\n"))
		self.assertNotIn("Content-Type", headers)  # requests sets the multipart boundary
		self.assertEqual(headers["X-CSRF-Token"], cookies["X-CSRF-Token"])
		((path, payload, *_),) = self.registry.calls
		self.assertEqual(path, "/input-mechanism-data/enqueue_import_file")
		self.assertEqual(
			payload,
			{
				"document_id": "doc-1",
				"data_model_id": "6f1c2d8e-4a7b-4c3e-9d2f-8b5a1e7c3fb2",
				"register_id": DEFAULT_REGISTER_ID,
				"intake_form_id": "6f1c2d8e-4a7b-4c3e-9d2f-8b5a1e7c3f91",
			},
		)

	def test_exceptions_list_reads_file_import_failures(self):
		out = self.client.list_import_exceptions(page=2, page_size=10)
		self.assertEqual(out["total"], 1)
		self.assertEqual(out["items"][0]["exception_type"], "INVALID_KEBELE")
		((path, payload, pagination, *_),) = self.registry.calls
		self.assertEqual(path, "/da/search_exceptions")
		self.assertEqual(payload["source"], "file_import")
		self.assertEqual(pagination, {"current_page": 2, "page_size": 10})
