"""Bulk import of Development Agents into the DA Registry (OAN Administrator).

The Admin picks a CSV or XLSX file in the registry's DA import template (v3, the MoA
extension-staff columns). DA Services checks the file and every row before anything
leaves, so the Admin sees mistakes at once and nothing half-done reaches the registry.
A clean file is handed to the registry (upload, then queue). The registry processes it
in the background: each valid row becomes a submission on its two-stage approval and
gets a DA-ID once approved; a row it cannot take lands in its exceptions list.

DA Services keeps one DA Integration Event per import (who, which file, how many rows,
the registry's import file id). It never copies the DAs themselves: the registry is the
DA master.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

import frappe
from frappe import _
from frappe.utils import now_datetime

from da_services.integrations.da_registry.client import get_client
from da_services.services import constants as C
from da_services.utils.permissions import ScopeContext, require_roles

EVENT_TYPE = "Agent Import"
MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 2000  # the registry advises pacing large imports in batches of 1,000-2,000
EXTENSIONS = (".csv", ".xlsx")

# The registry's DA import template v3, in its column order.
TEMPLATE_COLUMNS = (
	"Salutation",
	"Name",
	"Father Name",
	"Grandfather Name",
	"Fayda ID",
	"Gender",
	"Marital Status",
	"Month of Birth (EC)",
	"Year of Birth (EC)",
	"Phone Number",
	"Telegram Number",
	"Alternate Number",
	"Email",
	"Education Level",
	"Specialisation",
	"Position",
	"Joining Date",
	"Region",
	"Zone",
	"Woreda",
	"Kebele",
)
# The registry refuses a file-import row without these; the other columns may be blank
# on an import, but a filled value must still be valid.
REQUIRED = ("Name", "Father Name", "Fayda ID", "Region", "Zone", "Woreda", "Kebele")
PHONE_COLUMNS = ("Phone Number", "Telegram Number", "Alternate Number")

_PHONE_RE = re.compile(r"^(?:\+251|0)?[79]\d{8}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_FAYDA_STRIP_RE = re.compile(r"[\s-]")


def _admin(ctx: ScopeContext | None) -> ScopeContext:
	return ctx or require_roles(*C.UNRESTRICTED_ROLES)


def template_csv() -> str:
	"""The empty template, for the "Download template (v3)" link."""
	out = io.StringIO()
	csv.writer(out).writerow(TEMPLATE_COLUMNS)
	return out.getvalue()


# -- reading -------------------------------------------------------------------------


def _cell(value) -> str:
	if value is None:
		return ""
	if isinstance(value, float) and value.is_integer():
		value = int(value)  # Excel keeps a 16-digit Fayda ID or a year as a number
	if isinstance(value, date):
		return value.isoformat()[:10]
	return str(value).strip()


def _read_rows(filename: str, content: bytes) -> tuple[list[str], list[list[str]]]:
	if filename.lower().endswith(".xlsx"):
		from openpyxl import load_workbook

		try:
			sheet = load_workbook(io.BytesIO(content), read_only=True, data_only=True).worksheets[0]
		except Exception as exc:
			frappe.throw(_("The file is not a readable XLSX workbook."), frappe.ValidationError, exc=exc)
		rows = [[_cell(v) for v in row] for row in sheet.iter_rows(values_only=True)]
	else:
		try:
			text = content.decode("utf-8-sig")
		except UnicodeDecodeError:
			frappe.throw(_("Save the CSV as UTF-8 and upload it again."), frappe.ValidationError)
		rows = [[c.strip() for c in row] for row in csv.reader(io.StringIO(text))]
	rows = [r for r in rows if any(r)]
	if not rows:
		frappe.throw(_("The file is empty."), frappe.ValidationError)
	return rows[0], rows[1:]


# -- checking ------------------------------------------------------------------------


def _check_row(row: dict, line: int, seen_fayda: dict) -> list[dict]:
	errors = []

	def bad(column, message):
		errors.append({"row": line, "column": column, "message": message})

	for column in REQUIRED:
		if not row.get(column):
			bad(column, _("{0} is required.").format(column))

	fayda = _FAYDA_STRIP_RE.sub("", row.get("Fayda ID") or "")
	if fayda:
		if not (fayda.isdigit() and len(fayda) == 16):
			bad("Fayda ID", _("Fayda ID must be 16 digits."))
		elif fayda in seen_fayda:
			bad("Fayda ID", _("Same Fayda ID as row {0}.").format(seen_fayda[fayda]))
		else:
			seen_fayda[fayda] = line

	for column in PHONE_COLUMNS:
		phone = re.sub(r"[\s-]", "", row.get(column) or "")
		if phone and not _PHONE_RE.match(phone):
			bad(column, _("Use an Ethiopian number such as 0911234567 or +251911234567."))

	email = row.get("Email")
	if email and not _EMAIL_RE.match(email):
		bad("Email", _("Not a valid email address."))

	month = row.get("Month of Birth (EC)")
	if month and not (month.isdigit() and 1 <= int(month) <= 13):
		bad("Month of Birth (EC)", _("Month must be 1 to 13 (Ethiopian calendar)."))

	year = row.get("Year of Birth (EC)")
	if year and not (year.isdigit() and len(year) == 4):
		bad("Year of Birth (EC)", _("Year must have 4 digits (Ethiopian calendar)."))

	joined = row.get("Joining Date")
	if joined:
		parsed = None
		for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
			try:
				parsed = datetime.strptime(joined, fmt).date()
				break
			except ValueError:
				continue
		if not parsed:
			bad("Joining Date", _("Use YYYY-MM-DD or DD/MM/YYYY."))
		elif parsed > date.today():
			bad("Joining Date", _("Joining date cannot be in the future."))
	return errors


def validate_file(filename: str, content: bytes) -> dict:
	"""Check the file and every row. Returns {"rows": n, "errors": [...]}; nothing is sent."""
	name = (filename or "").strip()
	if not name.lower().endswith(EXTENSIONS):
		frappe.throw(_("Upload a CSV or XLSX file."), frappe.ValidationError)
	if not content:
		frappe.throw(_("The file is empty."), frappe.ValidationError)
	if len(content) > MAX_BYTES:
		frappe.throw(_("The file is larger than 10 MB."), frappe.ValidationError)

	header, rows = _read_rows(name, content)
	header = [h.strip() for h in header]
	if tuple(header[: len(TEMPLATE_COLUMNS)]) != TEMPLATE_COLUMNS or any(header[len(TEMPLATE_COLUMNS) :]):
		missing = [c for c in TEMPLATE_COLUMNS if c not in header]
		extra = [c for c in header if c and c not in TEMPLATE_COLUMNS]
		frappe.throw(
			_("The columns do not match template v3. Missing: {0}. Unexpected: {1}.").format(
				", ".join(missing) or "-", ", ".join(extra) or "-"
			),
			frappe.ValidationError,
		)
	if not rows:
		frappe.throw(_("The file has a header but no agents."), frappe.ValidationError)
	if len(rows) > MAX_ROWS:
		frappe.throw(
			_("The file has {0} agents; import at most {1} at a time.").format(len(rows), MAX_ROWS),
			frappe.ValidationError,
		)

	errors, seen_fayda = [], {}
	for index, values in enumerate(rows, start=2):  # row 1 is the header
		row = dict(zip(TEMPLATE_COLUMNS, list(values) + [""] * len(TEMPLATE_COLUMNS), strict=False))
		errors.extend(_check_row(row, index, seen_fayda))
	return {"rows": len(rows), "errors": errors}


# -- importing -----------------------------------------------------------------------


def _serialize(doc) -> dict:
	payload = frappe.parse_json(doc.payload) if isinstance(doc.payload, str) else doc.payload or {}
	return {
		"name": doc.name,
		# Handing the file over is all DA Services does; the rows are the registry's.
		"status": "Queued" if doc.status == "Completed" else doc.status,
		"file_name": payload.get("file_name"),
		"rows": payload.get("rows"),
		"uploaded_by": payload.get("uploaded_by"),
		"registry_import_id": doc.external_reference,
		"registry_document_id": payload.get("document_id"),
		"error": doc.last_error,
		"created_at": str(doc.creation),
	}


def import_agents(filename: str, content: bytes, ctx: ScopeContext | None = None) -> dict:
	"""Validate, then hand the file to the registry. Row errors stop the import.

	Returns {"status": "Rejected", "errors": [...]} without contacting the registry, or
	the import record once the registry has queued the file. DARegistryUnavailable is
	raised to the caller (registry down: retry later).
	"""
	ctx = _admin(ctx)
	checked = validate_file(filename, content)
	if checked["errors"]:
		return {"status": "Rejected", "rows": checked["rows"], "errors": checked["errors"]}

	result = get_client().import_agents_file(filename, content)
	doc = frappe.get_doc(
		{
			"doctype": "DA Integration Event",
			"event_type": EVENT_TYPE,
			"status": "Completed" if result.accepted else "Failed",
			"reference_id": filename[:140],
			"external_reference": result.external_reference,
			"payload": {
				"file_name": filename,
				"rows": checked["rows"],
				"uploaded_by": ctx.user,
				"document_id": (result.raw or {}).get("document_id"),
			},
			"attempts": 1,
			"completed_at": now_datetime() if result.accepted else None,
			"last_error": None if result.accepted else (result.message or "")[:500],
		}
	)
	doc.insert(ignore_permissions=True)
	return {**_serialize(doc), "errors": []}


def list_imports(*, limit: int = 20, start: int = 0, ctx: ScopeContext | None = None) -> list[dict]:
	_admin(ctx)
	names = frappe.get_all(
		"DA Integration Event",
		filters={"event_type": EVENT_TYPE},
		pluck="name",
		order_by="creation desc",
		limit=max(1, min(int(limit or 20), 100)),
		start=max(0, int(start or 0)),
	)
	return [_serialize(frappe.get_doc("DA Integration Event", n)) for n in names]


def import_exceptions(*, page: int = 1, page_size: int = 50, ctx: ScopeContext | None = None) -> dict:
	"""Rows the registry could not import, with the reason, newest first.

	The registry does not record which file or row a failure came from: entries are
	identified by Fayda ID and name.
	"""
	_admin(ctx)
	page = max(1, int(page or 1))
	page_size = max(1, min(int(page_size or 50), 200))
	out = get_client().list_import_exceptions(page=page, page_size=page_size)
	return {"items": out["items"], "total": out["total"], "page": page, "page_size": page_size}
