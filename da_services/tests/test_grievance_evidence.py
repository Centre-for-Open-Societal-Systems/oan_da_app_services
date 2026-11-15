"""Grievance evidence: upload, list and open the documents attached to a filed grievance.

Three layers: the mapper and HTTP client against faked responses (no live service),
the in-memory client, and the DA Services routes over the in-memory client.
"""

import io
import json
import unittest
from unittest.mock import Mock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today

from da_services.integrations.grievance.auth import AgentTokenProvider
from da_services.integrations.grievance.client import (
	GrievanceNotFound,
	GrievanceRejected,
	GrievanceServiceError,
	GrievanceServiceUnavailable,
	HttpGrievanceClient,
)
from da_services.integrations.grievance.mapper import to_attachment
from da_services.integrations.grievance.mock import MockGrievanceClient
from da_services.integrations.grievance.schemas import (
	EVIDENCE_MAX_BYTES,
	EVIDENCE_MAX_FILES,
	EvidenceFile,
	evidence_extension,
)
from da_services.services import constants as C

TICKET = "GRV-000001"
OTHER_TICKET = "GRV-000002"
PDF = b"%PDF-1.4 evidence"
PNG = b"\x89PNG\r\n\x1a\n evidence"

SUPERVISOR = "grv-evd-supervisor@da.local"
DA_USER = "grv-evd-da@da.local"
COMMS_USER = "grv-evd-comms@da.local"


class _Response:
	"""What the client reads off a requests.Response."""

	def __init__(self, status, body=None, *, content=b"", headers=None):
		self.status_code = status
		self._body = body
		self.content = content
		self.text = json.dumps(body) if body is not None else ""
		self.headers = headers or ({"Content-Type": "application/json"} if body is not None else {})

	def json(self):
		if self._body is None:
			raise ValueError("not JSON")
		return self._body


# ---------------------------------------------------------------------------
# 1. Mapper and helpers
# ---------------------------------------------------------------------------


class TestEvidenceMapping(unittest.TestCase):
	def test_upload_row_and_list_row_get_the_same_shape(self):
		uploaded = to_attachment(
			{
				"attachment": "GAT-00042",
				"file_name": "evidence.jpg",
				"mime_type": "image/jpeg",
				"size_bytes": 1048576,
				"checksum_sha256": "4b22",
				"scan_status": "Pending",
				"timeline_entry": None,
			}
		)
		listed = to_attachment(
			{
				"name": "GAT-00042",
				"file_name": "evidence.jpg",
				"mime_type": "image/jpeg",
				"size_bytes": 1048576,
				"document_type": "Photo",
				"timeline_entry": None,
				"scan_status": "Clean",
				"scanned_at": "2026-11-15 10:00:00",
				"uploaded_by_user": "officer@example.com",
				"uploaded_by_submitter": None,
				"creation": "2026-11-15 09:59:00",
			}
		)
		self.assertEqual(uploaded.keys(), listed.keys())
		self.assertEqual(uploaded["attachment_id"], "GAT-00042")
		self.assertEqual(listed["attachment_id"], "GAT-00042")
		self.assertEqual(listed["uploaded_at"], "2026-11-15 09:59:00")
		self.assertEqual(listed["document_type"], "Photo")

	def test_only_a_clean_file_is_viewable(self):
		for status, viewable in (("Clean", True), ("Pending", False), ("Infected", False), (None, False)):
			self.assertEqual(to_attachment({"name": "GAT-1", "scan_status": status})["viewable"], viewable)

	def test_who_uploaded_is_not_passed_on(self):
		row = to_attachment({"name": "GAT-1", "uploaded_by_user": "officer@example.com"})
		self.assertNotIn("officer@example.com", json.dumps(row))

	def test_extension_is_lower_case_and_needs_a_name(self):
		self.assertEqual(evidence_extension("Receipt.PDF"), ".pdf")
		self.assertEqual(evidence_extension("photo.final.JPeG"), ".jpeg")
		self.assertEqual(evidence_extension("no-extension"), "")
		self.assertEqual(evidence_extension(".pdf"), "")
		self.assertEqual(evidence_extension(None), "")


# ---------------------------------------------------------------------------
# 2. HTTP client
# ---------------------------------------------------------------------------


class TestHttpEvidenceClient(unittest.TestCase):
	BASE = "https://grievance.example/api"
	TICKET = "B-014-0007-6"

	def _client(self):
		tokens = AgentTokenProvider(base_url=self.BASE, static_token="static-token")
		return HttpGrievanceClient(self.BASE, timeout=5, tokens=tokens)

	@patch("da_services.integrations.grievance.client.requests.request")
	def test_upload_sends_multipart_to_the_ticket(self, request):
		request.return_value = _Response(
			200,
			{
				"status": "success",
				"message": "2 document(s) uploaded and queued for scanning",
				"data": [
					{"attachment": "GAT-00001", "file_name": "a.pdf", "scan_status": "Pending"},
					{"attachment": "GAT-00002", "file_name": "b.png", "scan_status": "Pending"},
				],
			},
		)
		rows = self._client().upload_attachments(
			self.TICKET,
			[EvidenceFile("a.pdf", PDF, "application/pdf"), EvidenceFile("b.png", PNG, None)],
			document_type="Receipt",
		)

		self.assertEqual([r["attachment_id"] for r in rows], ["GAT-00001", "GAT-00002"])
		call = request.call_args
		self.assertEqual(call.args[0], "POST")
		self.assertEqual(call.args[1], f"{self.BASE}/v1/grievances/{self.TICKET}/attachments")
		self.assertEqual(
			call.kwargs["files"],
			[
				("files", ("a.pdf", PDF, "application/pdf")),
				("files", ("b.png", PNG, "application/octet-stream")),
			],
		)
		self.assertEqual(call.kwargs["data"], {"document_type": "Receipt"})
		self.assertIsNone(call.kwargs["json"])
		self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer static-token")
		# requests writes the multipart Content-Type, boundary included.
		self.assertNotIn("Content-Type", call.kwargs["headers"])
		self.assertGreaterEqual(call.kwargs["timeout"], 120)

	@patch("da_services.integrations.grievance.client.requests.request")
	def test_upload_without_a_document_type_sends_no_form_field(self, request):
		request.return_value = _Response(200, {"status": "success", "data": []})
		self._client().upload_attachments(self.TICKET, [EvidenceFile("a.pdf", PDF)])
		self.assertIsNone(request.call_args.kwargs["data"])

	@patch("da_services.integrations.grievance.client.requests.request")
	def test_upload_refusals_keep_their_kind(self, request):
		files = [EvidenceFile("a.pdf", PDF)]
		request.return_value = _Response(
			400, {"status": "error", "message": "A grievance may carry at most 10 attachments."}
		)
		with self.assertRaises(GrievanceRejected) as refused:
			self._client().upload_attachments(self.TICKET, files)
		self.assertIn("at most 10", str(refused.exception))

		request.return_value = _Response(404, {"status": "error", "message": "No such grievance."})
		with self.assertRaises(GrievanceNotFound):
			self._client().upload_attachments(self.TICKET, files)

		request.return_value = _Response(503, {"status": "error", "message": "down"})
		with self.assertRaises(GrievanceServiceUnavailable):
			self._client().upload_attachments(self.TICKET, files)

	@patch("da_services.integrations.grievance.client.requests.request")
	def test_list_maps_the_rows(self, request):
		request.return_value = _Response(
			200,
			{
				"status": "success",
				"data": [
					{"name": "GAT-00001", "file_name": "a.pdf", "scan_status": "Clean", "size_bytes": 17},
					{"name": "GAT-00002", "file_name": "b.png", "scan_status": "Infected"},
				],
			},
		)
		rows = self._client().list_attachments(self.TICKET)

		self.assertEqual(request.call_args.args[0], "GET")
		self.assertEqual(request.call_args.args[1], f"{self.BASE}/v1/grievances/{self.TICKET}/attachments")
		self.assertEqual([r["attachment_id"] for r in rows], ["GAT-00001", "GAT-00002"])
		self.assertEqual([r["viewable"] for r in rows], [True, False])
		self.assertEqual(rows[0]["size_bytes"], 17)

	@patch("da_services.integrations.grievance.client.requests.request")
	def test_view_returns_the_file_bytes_and_name(self, request):
		request.return_value = _Response(
			200,
			content=PDF,
			headers={
				"Content-Type": "application/pdf",
				"Content-Disposition": 'inline; filename="evidence receipt.pdf"',
			},
		)
		file = self._client().get_attachment_content("GAT-00001")

		self.assertEqual(request.call_args.args[1], f"{self.BASE}/v1/attachments/GAT-00001/view")
		self.assertEqual(file.content, PDF)
		self.assertEqual(file.mime_type, "application/pdf")
		self.assertEqual(file.file_name, "evidence receipt.pdf")

	@patch("da_services.integrations.grievance.client.requests.request")
	def test_view_of_a_withheld_file_is_a_refusal(self, request):
		request.return_value = _Response(
			417,
			{"status": "error", "message": "<b>a.pdf</b> is not available: its scan status is Pending."},
		)
		with self.assertRaises(GrievanceRejected) as refused:
			self._client().get_attachment_content("GAT-00001")
		self.assertIn("scan status is Pending", str(refused.exception))
		self.assertNotIn("<b>", str(refused.exception))

	@patch("da_services.integrations.grievance.client.requests.request")
	def test_view_never_passes_json_off_as_the_file(self, request):
		request.return_value = _Response(200, {"status": "success", "data": {"file_url": "/private/x"}})
		with self.assertRaises(GrievanceServiceError):
			self._client().get_attachment_content("GAT-00001")

	@patch("da_services.integrations.grievance.client.requests.request")
	def test_ids_are_escaped_in_the_path(self, request):
		request.return_value = _Response(200, {"status": "success", "data": []})
		self._client().list_attachments("../admin")
		self.assertEqual(request.call_args.args[1], f"{self.BASE}/v1/grievances/..%2Fadmin/attachments")


# ---------------------------------------------------------------------------
# 3. In-memory client
# ---------------------------------------------------------------------------


class TestMockEvidence(unittest.TestCase):
	def setUp(self):
		self.client = MockGrievanceClient()

	def test_upload_is_pending_then_clean_on_the_next_list(self):
		created = self.client.upload_attachments(
			TICKET, [EvidenceFile("a.pdf", PDF)], document_type="Receipt"
		)
		self.assertEqual(created[0]["scan_status"], "Pending")
		self.assertEqual(created[0]["mime_type"], "application/pdf")
		self.assertEqual(created[0]["document_type"], "Receipt")

		listed = self.client.list_attachments(TICKET)
		self.assertEqual([r["attachment_id"] for r in listed], [created[0]["attachment_id"]])
		self.assertEqual(listed[0]["scan_status"], "Clean")
		self.assertNotIn("content", listed[0])

	def test_view_is_refused_until_the_scan_is_clean(self):
		attachment_id = self.client.upload_attachments(TICKET, [EvidenceFile("a.pdf", PDF)])[0][
			"attachment_id"
		]
		with self.assertRaises(GrievanceRejected):
			self.client.get_attachment_content(attachment_id)
		self.client.list_attachments(TICKET)
		self.assertEqual(self.client.get_attachment_content(attachment_id).content, PDF)

	def test_refusals(self):
		with self.assertRaises(GrievanceNotFound):
			self.client.upload_attachments("GRV-999999", [EvidenceFile("a.pdf", PDF)])
		with self.assertRaises(GrievanceNotFound):
			self.client.list_attachments("GRV-999999")
		with self.assertRaises(GrievanceNotFound):
			self.client.get_attachment_content("GAT-99999")
		for bad in (
			EvidenceFile("notes.docx", b"word"),
			EvidenceFile("empty.pdf", b""),
			EvidenceFile("big.pdf", b"x" * (EVIDENCE_MAX_BYTES + 1)),
		):
			with self.assertRaises(GrievanceRejected):
				self.client.upload_attachments(TICKET, [bad])
		self.assertEqual(self.client.list_attachments(TICKET), [])

	def test_one_bad_file_stores_nothing(self):
		with self.assertRaises(GrievanceRejected):
			self.client.upload_attachments(
				TICKET, [EvidenceFile("a.pdf", PDF), EvidenceFile("notes.docx", b"word")]
			)
		self.assertEqual(self.client.list_attachments(TICKET), [])

	def test_a_case_holds_ten_files(self):
		ten = [EvidenceFile(f"{i}.pdf", PDF) for i in range(EVIDENCE_MAX_FILES)]
		self.assertEqual(len(self.client.upload_attachments(TICKET, ten)), EVIDENCE_MAX_FILES)
		with self.assertRaises(GrievanceRejected):
			self.client.upload_attachments(TICKET, [EvidenceFile("eleventh.pdf", PDF)])
		# The limit is per grievance.
		self.assertEqual(len(self.client.upload_attachments(OTHER_TICKET, [EvidenceFile("a.pdf", PDF)])), 1)


# ---------------------------------------------------------------------------
# 4. API routes
# ---------------------------------------------------------------------------


def _user(email, roles=None):
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email = email
		u.first_name = email.split("@")[0]
		u.send_welcome_email = 0
		u.insert(ignore_permissions=True, ignore_links=True)
	existing = set(frappe.get_roles(email))
	for role in roles or []:
		if role not in existing:
			frappe.get_doc("User", email).add_roles(role)
	return email


def _grant(user, role, **kw):
	doc = frappe.new_doc("DA RBAC Assignment")
	doc.user = user
	doc.role = role
	doc.effective_from = today()
	for k, v in kw.items():
		setattr(doc, k, v)
	doc.insert(ignore_permissions=True, ignore_links=True)
	return doc


class TestGrievanceEvidenceAPI(IntegrationTestCase):
	BASE = "/api/v1/da-services/grievances"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from da_services.api.router import ensure_routes_registered

		ensure_routes_registered()

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		# These tests use the in-memory client. A site configured for http must not leak in.
		self._grievance_backend = frappe.conf.get("grievance_backend")
		frappe.conf.grievance_backend = "mock"
		MockGrievanceClient.reset()
		frappe.db.delete("DA RBAC Assignment")
		frappe.db.delete("DA Integration Event")
		_user(SUPERVISOR, [C.ROLE_SUPERVISOR])
		_user(DA_USER, [C.ROLE_DA])
		_user(COMMS_USER, [C.ROLE_COMMS])
		_grant(SUPERVISOR, C.ROLE_SUPERVISOR, region_scope="ET04", woreda_scope="ET04-W01")
		_grant(DA_USER, C.ROLE_DA, da_id="DA-000001", region_scope="ET04", woreda_scope="ET04-W01")
		_grant(COMMS_USER, C.ROLE_COMMS, region_scope="ET04")

	def tearDown(self):
		if self._grievance_backend is None:
			frappe.conf.pop("grievance_backend", None)
		else:
			frappe.conf.grievance_backend = self._grievance_backend
		MockGrievanceClient.reset()
		frappe.set_user("Administrator")
		super().tearDown()

	def _request(self, path, method="GET", *, files=None, form=None, user=DA_USER):
		"""One request through the router. ``files`` is [(field, file name, bytes)]."""
		import frappe.api
		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request as WerkzeugRequest

		frappe.set_user(user)
		path, _, query_string = path.partition("?")
		kwargs = {"path": path, "method": method, "base_url": "http://testsite.localhost"}
		if query_string:
			kwargs["query_string"] = query_string
		if files is not None:
			data: dict = dict(form or {})
			for field, name, content in files:
				data.setdefault(field, []).append((io.BytesIO(content), name))
			kwargs["data"] = data
			kwargs["content_type"] = "multipart/form-data"

		req = WerkzeugRequest(EnvironBuilder(**kwargs).get_environ())
		frappe.local.request = req
		frappe.local.request_ip = "127.0.0.1"
		frappe.local.form_dict = frappe._dict({**dict(req.args), **(form or {})})
		frappe.local.response = frappe._dict({})
		return frappe.api.handle(req)

	def _upload(self, files, *, ticket=TICKET, form=None, user=DA_USER):
		return self._request(f"{self.BASE}/{ticket}/attachments", "POST", files=files, form=form, user=user)

	def _list(self, ticket=TICKET, user=DA_USER):
		return self._request(f"{self.BASE}/{ticket}/attachments", user=user)

	@staticmethod
	def _json(res):
		return json.loads(res.get_data(as_text=True))

	# ------------------------------------------------------------------ upload

	def test_upload_one_document(self):
		res = self._upload([("files", "receipt.pdf", PDF)], form={"document_type": "Receipt"})
		self.assertEqual(res.status_code, 201)
		body = self._json(res)
		self.assertEqual(body["status"], "success")
		self.assertEqual(body["data"]["ticket_id"], TICKET)
		self.assertEqual(body["data"]["count"], 1)
		row = body["data"]["attachments"][0]
		self.assertTrue(row["attachment_id"])
		self.assertEqual(row["file_name"], "receipt.pdf")
		self.assertEqual(row["mime_type"], "application/pdf")
		self.assertEqual(row["size_bytes"], len(PDF))
		self.assertEqual(row["document_type"], "Receipt")
		self.assertEqual(row["scan_status"], "Pending")
		self.assertFalse(row["viewable"])

	def test_upload_several_documents_in_one_request(self):
		res = self._upload([("files", "receipt.pdf", PDF), ("files", "photo.PNG", PNG)], user=SUPERVISOR)
		self.assertEqual(res.status_code, 201)
		names = [r["file_name"] for r in self._json(res)["data"]["attachments"]]
		self.assertEqual(names, ["receipt.pdf", "photo.PNG"])

	def test_the_field_name_file_is_accepted_too(self):
		res = self._upload([("file", "receipt.pdf", PDF)])
		self.assertEqual(res.status_code, 201)
		self.assertEqual(self._json(res)["data"]["count"], 1)

	def test_upload_without_a_file_is_refused(self):
		res = self._upload([], form={"document_type": "Receipt"})
		self.assertEqual(res.status_code, 400)
		body = self._json(res)
		self.assertEqual(body["code"], "VALIDATION_ERROR")
		self.assertIn("files", body["details"])

	def test_a_refused_file_is_named_and_nothing_is_stored(self):
		res = self._upload(
			[
				("files", "receipt.pdf", PDF),
				("files", "notes.docx", b"word"),
				("files", "empty.png", b""),
				("files", "big.pdf", b"x" * (EVIDENCE_MAX_BYTES + 1)),
			]
		)
		self.assertEqual(res.status_code, 400)
		details = self._json(res)["details"]
		self.assertEqual(set(details), {"notes.docx", "empty.png", "big.pdf"})
		self.assertIn("JPG, PNG, PDF and MP3", details["notes.docx"])
		self.assertIn("empty", details["empty.png"])
		self.assertIn("10 MB", details["big.pdf"])
		self.assertEqual(self._json(self._list())["data"], [])

	def test_more_than_ten_files_in_one_request_is_refused(self):
		files = [("files", f"{i}.pdf", PDF) for i in range(EVIDENCE_MAX_FILES + 1)]
		res = self._upload(files)
		self.assertEqual(res.status_code, 400)
		self.assertEqual(self._json(self._list())["data"], [])

	def test_the_eleventh_file_of_a_grievance_is_refused(self):
		ten = [("files", f"{i}.pdf", PDF) for i in range(EVIDENCE_MAX_FILES)]
		self.assertEqual(self._upload(ten).status_code, 201)
		res = self._upload([("files", "eleventh.pdf", PDF)])
		self.assertEqual(res.status_code, 400)
		self.assertIn("at most 10", self._json(res)["message"])
		self.assertEqual(self._json(self._list())["meta"]["remaining"], 0)

	def test_upload_to_an_unknown_grievance_is_404(self):
		res = self._upload([("files", "receipt.pdf", PDF)], ticket="GRV-999999")
		self.assertEqual(res.status_code, 404)

	def test_upload_needs_a_grievance_role(self):
		for user in (COMMS_USER, "Guest"):
			res = self._upload([("files", "receipt.pdf", PDF)], user=user)
			self.assertIn(res.status_code, (401, 403), user)
		self.assertEqual(self._json(self._list())["data"], [])

	def test_upload_is_not_queued_when_the_service_is_down(self):
		with patch(
			"da_services.integrations.grievance.mock.MockGrievanceClient.upload_attachments",
			side_effect=GrievanceServiceUnavailable("test"),
		):
			res = self._upload([("files", "receipt.pdf", PDF)])
		self.assertEqual(res.status_code, 503)
		self.assertEqual(self._json(res)["code"], "SERVICE_UNAVAILABLE")
		self.assertEqual(frappe.db.count("DA Integration Event"), 0)

	def test_an_upstream_fault_is_bad_gateway_without_the_cause(self):
		client = Mock()
		client.upload_attachments.side_effect = GrievanceServiceError("IAM login refused: HTTP 401")
		with patch("da_services.api.v1.grievance.get_client", return_value=client):
			res = self._upload([("files", "receipt.pdf", PDF)])
		self.assertEqual(res.status_code, 502)
		body = self._json(res)
		self.assertEqual(body["code"], "BAD_GATEWAY")
		self.assertNotIn("IAM", body["message"])

	# ------------------------------------------------------------------ list

	def test_list_shows_the_documents_and_how_many_more_fit(self):
		self._upload([("files", "receipt.pdf", PDF), ("files", "photo.png", PNG)])
		res = self._list(user=SUPERVISOR)
		self.assertEqual(res.status_code, 200)
		body = self._json(res)
		self.assertEqual([r["file_name"] for r in body["data"]], ["receipt.pdf", "photo.png"])
		self.assertTrue(all(r["viewable"] for r in body["data"]))
		self.assertEqual(body["meta"]["count"], 2)
		self.assertEqual(body["meta"]["max"], EVIDENCE_MAX_FILES)
		self.assertEqual(body["meta"]["remaining"], EVIDENCE_MAX_FILES - 2)

	def test_list_of_an_unknown_grievance_is_404(self):
		self.assertEqual(self._list(ticket="GRV-999999").status_code, 404)

	def test_list_needs_a_grievance_role(self):
		self.assertIn(self._list(user=COMMS_USER).status_code, (401, 403))

	# ------------------------------------------------------------------ view

	def _uploaded_id(self, name="receipt.pdf", content=PDF, ticket=TICKET):
		res = self._upload([("files", name, content)], ticket=ticket)
		return self._json(res)["data"]["attachments"][0]["attachment_id"]

	def test_view_returns_the_file_inline(self):
		attachment_id = self._uploaded_id()
		res = self._request(f"{self.BASE}/{TICKET}/attachments/{attachment_id}/view")
		self.assertEqual(res.status_code, 200)
		self.assertEqual(res.get_data(), PDF)
		self.assertEqual(res.mimetype, "application/pdf")
		disposition = res.headers["Content-Disposition"]
		self.assertTrue(disposition.startswith("inline"), disposition)
		self.assertIn("receipt.pdf", disposition)
		self.assertEqual(res.headers["X-Content-Type-Options"], "nosniff")
		self.assertIn("no-store", res.headers["Cache-Control"])

	def test_view_with_download_is_a_save_as(self):
		attachment_id = self._uploaded_id()
		res = self._request(f"{self.BASE}/{TICKET}/attachments/{attachment_id}/view?download=1")
		self.assertEqual(res.status_code, 200)
		self.assertTrue(res.headers["Content-Disposition"].startswith("attachment"))

	def test_view_through_another_grievance_is_404(self):
		attachment_id = self._uploaded_id()
		res = self._request(f"{self.BASE}/{OTHER_TICKET}/attachments/{attachment_id}/view")
		self.assertEqual(res.status_code, 404)
		self.assertNotEqual(res.get_data(), PDF)

	def test_view_of_an_unscanned_or_infected_file_is_refused(self):
		for status, code in (("Pending", "ATTACHMENT_NOT_READY"), ("Infected", "ATTACHMENT_BLOCKED")):
			client = Mock()
			client.list_attachments.return_value = [
				to_attachment({"name": "GAT-00001", "file_name": "a.pdf", "scan_status": status})
			]
			with patch("da_services.api.v1.grievance.get_client", return_value=client):
				res = self._request(f"{self.BASE}/{TICKET}/attachments/GAT-00001/view")
			self.assertEqual(res.status_code, 409, status)
			self.assertEqual(self._json(res)["code"], code)
			client.get_attachment_content.assert_not_called()

	def test_view_needs_a_grievance_role(self):
		attachment_id = self._uploaded_id()
		res = self._request(f"{self.BASE}/{TICKET}/attachments/{attachment_id}/view", user=COMMS_USER)
		self.assertIn(res.status_code, (401, 403))
		self.assertNotEqual(res.get_data(), PDF)
