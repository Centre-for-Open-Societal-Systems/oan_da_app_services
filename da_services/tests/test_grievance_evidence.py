"""Grievance evidence: upload, list and open the documents attached to a filed grievance.

Three layers: the mapper and HTTP client against faked responses (no live service),
the in-memory client, and the DA Services routes over the in-memory client.
"""

import io
import json
import unittest
from unittest.mock import Mock, patch

import frappe
import frappe.api
from frappe.tests import IntegrationTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from da_services.api.router import ensure_routes_registered
from da_services.integrations.grievance.client import (
	GrievanceNotFound,
	GrievanceRejected,
	GrievanceServiceError,
	GrievanceServiceUnavailable,
	HttpGrievanceClient,
)
from da_services.integrations.grievance.mapper import to_attachment
from da_services.integrations.grievance.mock import (
	EVIDENCE_MAX_BYTES,
	EVIDENCE_MAX_FILES,
	MockGrievanceClient,
)
from da_services.integrations.grievance.schemas import EvidenceFile
from da_services.services import constants as C

TICKET = "GRV-000001"
OTHER_TICKET = "GRV-000002"
PDF = b"%PDF-1.4 evidence"
PNG = b"\x89PNG\r\n\x1a\n evidence"

SUPERVISOR = "grv-evd-supervisor@da.local"
DA_USER = "grv-evd-da@da.local"


class _Response:
	"""What the client reads off a requests.Response."""

	def __init__(self, status, body=None, *, content=b""):
		self.status_code = status
		self._body = body
		self.content = content
		self.text = json.dumps(body) if body is not None else ""

	def json(self):
		if self._body is None:
			raise ValueError("not JSON")
		return self._body


# ---------------------------------------------------------------------------
# 1. Mapper
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
		self.assertEqual(uploaded["scan_status"], "Pending")
		self.assertEqual(listed["attachment_id"], "GAT-00042")
		self.assertEqual(listed["scan_status"], "Clean")
		self.assertEqual(listed["uploaded_at"], "2026-11-15 09:59:00")
		self.assertEqual(listed["document_type"], "Photo")

	def test_who_uploaded_is_not_passed_on(self):
		row = to_attachment({"name": "GAT-1", "uploaded_by_user": "officer@example.com"})
		self.assertNotIn("officer@example.com", json.dumps(row))


# ---------------------------------------------------------------------------
# 2. HTTP client
# ---------------------------------------------------------------------------


class TestHttpEvidenceClient(unittest.TestCase):
	BASE = "https://grievance.example"
	TICKET = "B-014-0007-6"

	def setUp(self):
		self._auth = patch(
			"da_services.integrations.grievance.client.frappe.get_request_header",
			side_effect=lambda name: "Bearer fe-sso-token" if name == "Authorization" else None,
		)
		self._auth.start()
		self.addCleanup(self._auth.stop)

	def _client(self):
		return HttpGrievanceClient(self.BASE, timeout=5)

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
		self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer fe-sso-token")
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
		self.assertEqual([r["scan_status"] for r in rows], ["Clean", "Infected"])
		self.assertEqual(rows[0]["size_bytes"], 17)

	@patch("da_services.integrations.grievance.client.requests.request")
	def test_view_returns_the_file_bytes(self, request):
		request.return_value = _Response(200, content=PDF)
		content = self._client().get_attachment_content("GAT-00001")

		self.assertEqual(request.call_args.args[1], f"{self.BASE}/v1/attachments/GAT-00001/view")
		self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer fe-sso-token")
		self.assertEqual(content, PDF)

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
		self.assertEqual(self.client.get_attachment_content(attachment_id), PDF)

	def test_refusals(self):
		with self.assertRaises(GrievanceNotFound):
			self.client.upload_attachments("GRV-999999", [EvidenceFile("a.pdf", PDF)])
		with self.assertRaises(GrievanceNotFound):
			self.client.list_attachments("GRV-999999")
		with self.assertRaises(GrievanceNotFound):
			self.client.get_attachment_content("GAT-99999")
		for bad in (
			EvidenceFile("notes.docx", b"word"),
			EvidenceFile("no-extension", b"data"),
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
		ten = [EvidenceFile(f"{i}.PDF", PDF) for i in range(EVIDENCE_MAX_FILES)]
		self.assertEqual(len(self.client.upload_attachments(TICKET, ten)), EVIDENCE_MAX_FILES)
		with self.assertRaises(GrievanceRejected):
			self.client.upload_attachments(TICKET, [EvidenceFile("eleventh.pdf", PDF)])
		# The limit is per grievance.
		self.assertEqual(len(self.client.upload_attachments(OTHER_TICKET, [EvidenceFile("a.pdf", PDF)])), 1)


# ---------------------------------------------------------------------------
# 4. API routes
# ---------------------------------------------------------------------------


def _user(email: str, role: str) -> None:
	"""A user holding `role`. The evidence routes check the role only, so no grant is needed."""
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	if role not in frappe.get_roles(email):
		frappe.get_doc("User", email).add_roles(role)


class TestGrievanceEvidenceAPI(IntegrationTestCase):
	BASE = "/api/v1/da-services/grievances"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		ensure_routes_registered()

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		MockGrievanceClient.reset()
		shared = MockGrievanceClient.shared()
		self._api_client_patch = patch("da_services.api.v1.grievance.get_client", return_value=shared)
		self._svc_client_patch = patch(
			"da_services.services.grievance_evidence.get_client", return_value=shared
		)
		self._api_client_patch.start()
		self._svc_client_patch.start()
		_user(SUPERVISOR, C.ROLE_SUPERVISOR)
		_user(DA_USER, C.ROLE_DA)

	def tearDown(self):
		self._api_client_patch.stop()
		self._svc_client_patch.stop()
		MockGrievanceClient.reset()
		frappe.set_user("Administrator")
		super().tearDown()

	def _request(self, path, method="GET", *, files=None, form=None, user=DA_USER):
		"""One request through the router. ``files`` is [(field, file name, bytes)]."""
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

		req = Request(EnvironBuilder(**kwargs).get_environ())
		frappe.local.request = req
		frappe.local.request_ip = "127.0.0.1"
		frappe.local.form_dict = frappe._dict({**dict(req.args), **(form or {})})
		frappe.local.response = frappe._dict({})
		return frappe.api.handle(req)

	def _upload(self, files, *, ticket=TICKET, form=None, user=DA_USER):
		return self._request(f"{self.BASE}/{ticket}/attachments", "POST", files=files, form=form, user=user)

	def _list(self, ticket=TICKET, user=DA_USER):
		return self._request(f"{self.BASE}/{ticket}/attachments", user=user)

	def _view(self, attachment_id, *, ticket=TICKET, query="", user=DA_USER):
		return self._request(f"{self.BASE}/{ticket}/attachments/{attachment_id}/view{query}", user=user)

	def _uploaded_id(self):
		return self._json(self._upload([("files", "receipt.pdf", PDF)]))["data"][0]["attachment_id"]

	@staticmethod
	def _json(res):
		return json.loads(res.get_data(as_text=True))

	# ------------------------------------------------------------------ upload

	def test_upload_one_document(self):
		res = self._upload([("files", "receipt.pdf", PDF)], form={"document_type": "Receipt"})
		self.assertEqual(res.status_code, 201)
		body = self._json(res)
		self.assertEqual(body["status"], "success")
		self.assertEqual(len(body["data"]), 1)
		row = body["data"][0]
		self.assertTrue(row["attachment_id"])
		self.assertEqual(row["file_name"], "receipt.pdf")
		self.assertEqual(row["mime_type"], "application/pdf")
		self.assertEqual(row["size_bytes"], len(PDF))
		self.assertEqual(row["document_type"], "Receipt")
		self.assertEqual(row["scan_status"], "Pending")

	def test_upload_several_documents_in_one_request(self):
		res = self._upload([("files", "receipt.pdf", PDF), ("files", "photo.PNG", PNG)], user=SUPERVISOR)
		self.assertEqual(res.status_code, 201)
		self.assertEqual([r["file_name"] for r in self._json(res)["data"]], ["receipt.pdf", "photo.PNG"])

	def test_the_field_name_file_is_accepted_too(self):
		res = self._upload([("file", "receipt.pdf", PDF)])
		self.assertEqual(res.status_code, 201)
		self.assertEqual(len(self._json(res)["data"]), 1)

	def test_upload_without_a_file_is_refused(self):
		res = self._upload([], form={"document_type": "Receipt"})
		self.assertEqual(res.status_code, 400)
		self.assertIn("files is required", self._json(res)["message"])

	def test_the_services_refusal_is_passed_on_and_nothing_is_stored(self):
		res = self._upload([("files", "receipt.pdf", PDF), ("files", "notes.docx", b"word")])
		self.assertEqual(res.status_code, 400)
		self.assertIn("notes.docx is not a file type we recognise", self._json(res)["message"])
		self.assertEqual(self._json(self._list())["data"], [])

	def test_the_eleventh_file_of_a_grievance_is_refused(self):
		ten = [("files", f"{i}.pdf", PDF) for i in range(EVIDENCE_MAX_FILES)]
		self.assertEqual(self._upload(ten).status_code, 201)
		res = self._upload([("files", "eleventh.pdf", PDF)])
		self.assertEqual(res.status_code, 400)
		self.assertIn("at most 10", self._json(res)["message"])
		self.assertEqual(len(self._json(self._list())["data"]), EVIDENCE_MAX_FILES)

	def test_upload_to_an_unknown_grievance_is_404(self):
		res = self._upload([("files", "receipt.pdf", PDF)], ticket="GRV-999999")
		self.assertEqual(res.status_code, 404)

	def test_upload_is_not_queued_when_the_service_is_down(self):
		queued = frappe.db.count("DA Integration Event")
		with patch.object(
			MockGrievanceClient, "upload_attachments", side_effect=GrievanceServiceUnavailable("test")
		):
			res = self._upload([("files", "receipt.pdf", PDF)])
		self.assertEqual(res.status_code, 503)
		self.assertEqual(self._json(res)["code"], "SERVICE_UNAVAILABLE")
		self.assertEqual(frappe.db.count("DA Integration Event"), queued)

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

	def test_list_shows_the_documents_with_their_scan_status(self):
		self._upload([("files", "receipt.pdf", PDF), ("files", "photo.png", PNG)])
		res = self._list(user=SUPERVISOR)
		self.assertEqual(res.status_code, 200)
		rows = self._json(res)["data"]
		self.assertEqual([r["file_name"] for r in rows], ["receipt.pdf", "photo.png"])
		self.assertEqual([r["scan_status"] for r in rows], ["Clean", "Clean"])

	def test_list_of_an_unknown_grievance_is_404(self):
		self.assertEqual(self._list(ticket="GRV-999999").status_code, 404)

	# ------------------------------------------------------------------ view

	def test_view_returns_the_file_inline(self):
		res = self._view(self._uploaded_id())
		self.assertEqual(res.status_code, 200)
		self.assertEqual(res.get_data(), PDF)
		self.assertEqual(res.mimetype, "application/pdf")
		disposition = res.headers["Content-Disposition"]
		self.assertTrue(disposition.startswith("inline"), disposition)
		self.assertIn("receipt.pdf", disposition)
		self.assertEqual(res.headers["X-Content-Type-Options"], "nosniff")
		self.assertIn("no-store", res.headers["Cache-Control"])

	def test_view_with_download_is_a_save_as(self):
		res = self._view(self._uploaded_id(), query="?download=1")
		self.assertEqual(res.status_code, 200)
		self.assertTrue(res.headers["Content-Disposition"].startswith("attachment"))

	def test_view_through_another_grievance_is_404(self):
		res = self._view(self._uploaded_id(), ticket=OTHER_TICKET)
		self.assertEqual(res.status_code, 404)
		self.assertNotEqual(res.get_data(), PDF)

	def test_the_services_scan_refusal_is_passed_on(self):
		client = Mock()
		client.list_attachments.return_value = [
			to_attachment({"name": "GAT-00001", "file_name": "a.pdf", "scan_status": "Pending"})
		]
		client.get_attachment_content.side_effect = GrievanceRejected(
			"a.pdf is not available: its scan status is Pending."
		)
		with patch("da_services.services.grievance_evidence.get_client", return_value=client):
			res = self._view("GAT-00001")
		self.assertEqual(res.status_code, 400)
		self.assertIn("scan status is Pending", self._json(res)["message"])
