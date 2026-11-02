"""Bulk agent import: file and row checks, hand-off to the DA Registry, import history."""

import csv
import io
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from openpyxl import Workbook
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from da_services.api.v1 import agent_import as api
from da_services.integrations.da_registry.client import DARegistryUnavailable
from da_services.integrations.da_registry.mock import MockDARegistryClient
from da_services.services import agent_import as svc
from da_services.services import constants as C

DA_USER = "import-da@da.local"


def _row(**over):
	row = {
		"Salutation": "Mr",
		"Name": "Tadesse",
		"Father Name": "Alemu",
		"Grandfather Name": "Bekele",
		"Fayda ID": "3512 4498 7712 0345",
		"Gender": "Male",
		"Marital Status": "Married",
		"Month of Birth (EC)": "9",
		"Year of Birth (EC)": "1990",
		"Phone Number": "+251912345678",
		"Telegram Number": "",
		"Alternate Number": "",
		"Email": "tadesse@example.org",
		"Education Level": "Degree",
		"Specialisation": "Crop production",
		"Position": "Development Agent",
		"Joining Date": "2020-09-11",
		"Region": "Oromia",
		"Zone": "West Shewa",
		"Woreda": "Bako Tibe",
		"Kebele": "Bako 01",
	}
	row.update(over)
	return [row[c] for c in svc.TEMPLATE_COLUMNS]


def _csv(*rows, header=svc.TEMPLATE_COLUMNS) -> bytes:
	out = io.StringIO()
	writer = csv.writer(out)
	writer.writerow(header)
	writer.writerows(rows)
	return out.getvalue().encode()


def _multipart(content: bytes, filename: str):
	builder = EnvironBuilder(method="POST", data={"file": (io.BytesIO(content), filename)})
	return Request(builder.get_environ())


class TestAgentImport(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("DA Integration Event", {"event_type": svc.EVENT_TYPE})
		frappe.cache.delete_keys("rl:da:da_services.api.v1.agent_import")  # per-user rate limits
		MockDARegistryClient.reset()
		self.registry = MockDARegistryClient.shared()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request = None
		frappe.local.da_audit_pending = []  # denials inside a request are deferred; drop ours
		frappe.db.delete("DA Integration Event", {"event_type": svc.EVENT_TYPE})
		MockDARegistryClient.reset()

	# -- checks --------------------------------------------------------------------------

	def test_clean_file_is_valid(self):
		out = svc.validate_file("agents.csv", _csv(_row(), _row(**{"Fayda ID": "3512449877120346"})))
		self.assertEqual((out["rows"], out["errors"]), (2, []))

	def test_every_row_error_is_reported_with_row_and_column(self):
		content = _csv(
			_row(Name="", **{"Fayda ID": "3512449877120397"}),  # row 2
			_row(**{"Fayda ID": "12345"}),  # row 3
			_row(),  # row 4
			_row(),  # row 5: same Fayda ID as row 4
			_row(**{"Fayda ID": "3512449877120399", "Phone Number": "12345", "Month of Birth (EC)": "14"}),
			_row(**{"Fayda ID": "3512449877120398", "Joining Date": "2999-01-01", "Kebele": ""}),
		)
		errors = {(e["row"], e["column"]) for e in svc.validate_file("agents.csv", content)["errors"]}
		self.assertEqual(
			errors,
			{
				(2, "Name"),
				(3, "Fayda ID"),
				(5, "Fayda ID"),
				(6, "Phone Number"),
				(6, "Month of Birth (EC)"),
				(7, "Joining Date"),
				(7, "Kebele"),
			},
		)

	def test_row_numbers_follow_the_file_when_it_has_blank_lines(self):
		content = _csv(_row(), [], _row(**{"Fayda ID": "12345"}))  # the bad agent is on line 4
		out = svc.validate_file("agents.csv", content)
		self.assertEqual(out["rows"], 2)
		self.assertEqual({(e["row"], e["column"]) for e in out["errors"]}, {(4, "Fayda ID")})

	def test_file_shape_is_checked(self):
		cases = {
			("agents.pdf", _csv(_row())): "CSV or XLSX",
			("agents.csv", b""): "empty",
			("agents.csv", _csv(header=svc.TEMPLATE_COLUMNS)): "no agents",
			("agents.csv", _csv(_row()[:-1], header=svc.TEMPLATE_COLUMNS[:-1])): "Missing: Kebele",
			("agents.csv", b"\xff\xfe" + "x".encode("utf-16")): "UTF-8",
		}
		for (filename, content), message in cases.items():
			with self.assertRaises(frappe.ValidationError, msg=filename) as ctx:
				svc.validate_file(filename, content)
			self.assertIn(message, str(ctx.exception))
		with patch.object(svc, "MAX_ROWS", 1), self.assertRaises(frappe.ValidationError):
			svc.validate_file("agents.csv", _csv(_row(), _row(**{"Fayda ID": "3512449877120346"})))

	def test_xlsx_numbers_are_read_as_text(self):
		book = Workbook()
		sheet = book.active
		sheet.append(list(svc.TEMPLATE_COLUMNS))
		row = _row()
		row[svc.TEMPLATE_COLUMNS.index("Fayda ID")] = 3512449877120345  # Excel keeps it as a number
		row[svc.TEMPLATE_COLUMNS.index("Year of Birth (EC)")] = 1990.0
		sheet.append(row)
		buf = io.BytesIO()
		book.save(buf)
		self.assertEqual(svc.validate_file("agents.xlsx", buf.getvalue())["errors"], [])

	# -- import --------------------------------------------------------------------------

	def test_clean_file_goes_to_the_registry_and_is_recorded(self):
		content = _csv(_row())
		out = svc.import_agents("agents.csv", content)
		self.assertEqual(
			(out["status"], out["rows"], out["registry_import_id"]), ("Queued", 1, "MOCK-IMPORT-1")
		)
		self.assertEqual(self.registry.imports, [("agents.csv", content)])
		(listed,) = svc.list_imports()
		self.assertEqual((listed["file_name"], listed["uploaded_by"]), ("agents.csv", "Administrator"))

	def test_a_file_with_row_errors_never_reaches_the_registry(self):
		out = svc.import_agents("agents.csv", _csv(_row(Name="")))
		self.assertEqual(out["status"], "Rejected")
		self.assertEqual(self.registry.imports, [])
		self.assertEqual(svc.list_imports(), [])

	def test_import_over_multipart_api(self):
		frappe.local.request = _multipart(_csv(_row()), "agents.csv")
		out = api.import_agents()
		self.assertEqual(out["status"], "success", out)
		self.assertEqual(out["data"]["status"], "Queued")

		frappe.local.request = _multipart(_csv(_row(Name="")), "agents.csv")
		out = api.import_agents()
		self.assertEqual((out["status"], out["code"]), ("error", "VALIDATION_ERROR"))
		self.assertEqual(out["details"]["errors"][0]["column"], "Name")
		self.assertEqual(frappe.response.get("http_status_code"), 400)

		frappe.local.request = None
		self.assertEqual(api.import_agents()["status"], "error")  # no file

	def test_validate_endpoint_sends_nothing(self):
		frappe.local.request = _multipart(_csv(_row()), "agents.csv")
		out = api.validate()
		self.assertTrue(out["data"]["valid"], out)
		self.assertEqual(self.registry.imports, [])

	def test_registry_down_is_503_and_not_recorded(self):
		frappe.local.request = _multipart(_csv(_row()), "agents.csv")
		with patch.object(self.registry, "import_agents_file", side_effect=DARegistryUnavailable("down")):
			out = api.import_agents()
		self.assertEqual((out["status"], out["code"]), ("error", "SERVICE_UNAVAILABLE"))
		self.assertEqual(frappe.response.get("http_status_code"), 503)
		self.assertEqual(svc.list_imports(), [])

	def test_exceptions_come_from_the_registry(self):
		self.registry.import_exceptions = [{"fayda_id": "9311", "exception_type": "DUPLICATE_IDENTITY"}]
		out = api.exceptions()
		self.assertEqual(out["status"], "success", out)
		self.assertEqual(out["meta"]["total"], 1)
		self.assertEqual(out["data"][0]["exception_type"], "DUPLICATE_IDENTITY")

	def test_template_lists_the_v3_columns(self):
		out = api.template()["data"]
		self.assertEqual(len(out["columns"]), 21)
		self.assertTrue(out["csv"].startswith("Salutation,Name,Father Name"))

	def test_only_an_administrator_can_import(self):
		if not frappe.db.exists("User", DA_USER):
			u = frappe.new_doc("User")
			u.email = DA_USER
			u.first_name = "Import DA"
			u.send_welcome_email = 0
			u.insert(ignore_permissions=True)
			u.add_roles(C.ROLE_DA)
		frappe.set_user(DA_USER)
		frappe.local.request = _multipart(_csv(_row()), "agents.csv")
		for call in (api.template, api.validate, api.import_agents, api.list_imports, api.exceptions):
			self.assertEqual(call()["status"], "error", call.__name__)
		self.assertEqual(self.registry.imports, [])
