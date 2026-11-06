"""Workbook layout for the survey XLSX export (FR-09d Stage 5).

Pure tests over ``build_export_rows`` / ``build_export_xlsx``: they need no survey
DocTypes, so they run before the survey tables are migrated.
"""

from __future__ import annotations

import io
import unittest

from openpyxl import load_workbook

from da_services.services.surveys import EXPORT_BASE_COLUMNS, build_export_rows, build_export_xlsx

PARAMETERS = [
	{"id": "p1", "prompt": "Consent"},
	{"id": "p4", "prompt": "How useful was the advice?"},
	{"id": "p5", "prompt": "Which crops?"},
]

RESPONSE = {
	"name": "DA-SRR-2026-0000001",
	"survey": "DA-SRV-2026-0000001",
	"template_version": "FSS-T1-v1.0",
	"farmer_id": "farmer-uuid-001",
	"farmer_reg_id": "FR-000101",
	"da_id": "DA-000001",
	"consent": "granted",
	"sync_state": "Synced",
	"started_at": "2026-11-06 09:00:00",
	"completed_at": None,
	"region": "ET04",
	"zone": "ET04-Z01",
	"woreda": "ET04-W01",
	"kebele": "Bako 01",
	"answers": {"p1": "granted", "p4": "4", "p5": ["Maize", "Teff"]},
}


class TestSurveyExportWorkbook(unittest.TestCase):
	def test_headers_are_base_columns_then_parameters(self):
		rows = build_export_rows(PARAMETERS, [])
		self.assertEqual(rows, [list(EXPORT_BASE_COLUMNS) + [
			"p1: Consent",
			"p4: How useful was the advice?",
			"p5: Which crops?",
		]])

	def test_response_row_uses_stored_farmer_location_and_answers(self):
		rows = build_export_rows(PARAMETERS, [RESPONSE])
		self.assertEqual(len(rows), 2)
		header = rows[0]
		row = rows[1]
		self.assertEqual(row[header.index("Farmer ID")], "farmer-uuid-001")
		self.assertEqual(row[header.index("Farmer Registry ID")], "FR-000101")
		self.assertEqual(row[header.index("Collector DA ID")], "DA-000001")
		self.assertEqual(row[header.index("Kebele")], "Bako 01")
		self.assertEqual(row[header.index("p4: How useful was the advice?")], "4")
		self.assertEqual(row[header.index("p5: Which crops?")], "Maize, Teff")

	def test_workbook_opens_with_expected_sheet_and_rows(self):
		content = build_export_xlsx(PARAMETERS, [RESPONSE])
		self.assertIsInstance(content, bytes)
		workbook = load_workbook(filename=io.BytesIO(content))
		sheet = workbook.active
		self.assertEqual(sheet.max_row, 2)
		self.assertEqual(sheet.max_column, len(EXPORT_BASE_COLUMNS) + len(PARAMETERS))
		self.assertEqual(sheet.cell(row=1, column=1).value, "Response ID")
		self.assertEqual(sheet.cell(row=2, column=1).value, "DA-SRR-2026-0000001")

	def test_empty_export_is_headers_only(self):
		content = build_export_xlsx(PARAMETERS, [])
		workbook = load_workbook(filename=io.BytesIO(content))
		self.assertEqual(workbook.active.max_row, 1)
		self.assertEqual(workbook.active.max_column, len(EXPORT_BASE_COLUMNS) + len(PARAMETERS))

	def test_missing_answers_are_empty_cells(self):
		rows = build_export_rows(
			[{"id": "p4", "prompt": "Rating"}],
			[{**RESPONSE, "answers": {}}],
		)
		self.assertEqual(rows[1][-1], "")
