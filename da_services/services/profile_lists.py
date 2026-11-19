"""Profile changes on the DA record's lists and files (DA Registry #66).

Four kinds of change request, each approved like any other profile change (Woreda
Supervisor, then OAN Administrator, see services/profile.py) and only then sent to the
registry with the Administrator's token:

- ``Education records``: add, change or remove an Education & Professional record.
- ``Certificates``: add a certificate to a record, replace one, or remove one.
- ``Family members``: add, change or remove a family member / dependent.
- ``Photo``: set or remove the DA's photo.

A file (certificate, photo) stays in DA Services, private, until the Administrator
approves; it is then uploaded to the registry's document store and only the document id
that upload returned is sent (the registry trusts any id it is given). The registry
keeps the file and its name only; a certificate's title, issuer and dates stay in DA
Services (``DA Certificate``).

``changes`` of such a request is ``{key: entry}`` with one key per record ("record:<id>",
"record:new", "certificates:<record id>", "family:<id>", "family:new", "photo"), so the
one-open-request-per-field rule of services/profile.py becomes one per record.
"""

from __future__ import annotations

import io
import re
from datetime import date

import frappe
from frappe import _

from da_services.integrations.da_registry.client import DARegistryClient
from da_services.integrations.da_registry.mapper import (
	DEPENDENT_REGISTER,
	QUALIFICATION_REGISTER,
	SECTION_DEPENDENT,
	SECTION_IDENTITY,
	SECTION_QUALIFICATION,
)
from da_services.integrations.da_registry.schemas import DA_PLACEHOLDER, DAReference, SectionChange
from da_services.services import profile_fields as pf

SECTION_RECORDS = "Education records"
SECTION_CERTIFICATES = "Certificates"
SECTION_FAMILY = "Family members"
SECTION_PHOTO = "Photo"
SECTIONS = (SECTION_RECORDS, SECTION_CERTIFICATES, SECTION_FAMILY, SECTION_PHOTO)
FILE_SECTIONS = (SECTION_CERTIFICATES, SECTION_PHOTO)
ACTIONS = ("add", "update", "delete")
MAX_CERTIFICATES = 3  # per Education & Professional record, as the registry allows
# The registry attaches a certificate or a photo of up to 2 MB (its portal refuses a larger
# photo; 18 Nov 2026). Its upload endpoint takes larger files, so the size is checked here,
# before any upload.
MAX_CERTIFICATE_BYTES = 2 * 1024 * 1024
MAX_PHOTO_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = max(MAX_CERTIFICATE_BYTES, MAX_PHOTO_BYTES)  # the most an upload request is read
# Positions that must hold an Education & Professional record (DA Registry, 18 Nov 2026)
POSITIONS_WITH_RECORDS = pf.DA_POSITIONS
PASSOUT_FIRST_YEAR = 1950
CERTIFICATE_TYPES = {
	".pdf": "application/pdf",
	".jpg": "image/jpeg",
	".jpeg": "image/jpeg",
	".png": "image/png",
	".webp": "image/webp",
}
PHOTO_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
_MEMBER_NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '\-.]+[^\W\d_]+)*\.?$")
CERTIFICATE = "DA Certificate"


def form_options(client: DARegistryClient) -> dict:
	"""What a screen needs to draw the list and file forms without repeating the rules:
	the Master Data lists of the coded values ([{code, label}], or None when a list cannot
	be read now; the value is still checked when the request is sent) and the file limits."""

	def options(attribute: str) -> list[dict] | None:
		values = pf._values(client, attribute)
		if values is None:
			return None
		return [
			{"code": code, "label": label}
			for code, label in sorted(values.items(), key=lambda item: str(item[1]).casefold())
		]

	return {
		"education_level": options("DA_EDUCATION_LEVEL"),
		"relationship": options("DA_RELATIONSHIP"),
		"passout_year": {"min": PASSOUT_FIRST_YEAR, "max": pf.ethiopian_year(date.today())},
		"certificate": {
			"extensions": sorted(CERTIFICATE_TYPES),
			"max_bytes": MAX_CERTIFICATE_BYTES,
			"max_per_record": MAX_CERTIFICATES,
		},
		"photo": {"extensions": sorted(PHOTO_TYPES), "max_bytes": MAX_PHOTO_BYTES},
	}


# -- files -----------------------------------------------------------------------------


def _sniff(content: bytes) -> str | None:
	"""The type a file really is, from its first bytes (an extension can lie)."""
	if content.startswith(b"%PDF-"):
		return "application/pdf"
	if content.startswith(b"\x89PNG\r\n\x1a\n"):
		return "image/png"
	if content.startswith(b"\xff\xd8\xff"):
		return "image/jpeg"
	if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
		return "image/webp"
	return None


def check_file(filename: str | None, content: bytes | None, *, photo: bool) -> tuple[str, str]:
	"""(file name, content type) of an acceptable certificate or photo, or a 400."""
	allowed = PHOTO_TYPES if photo else CERTIFICATE_TYPES
	label = _("photo") if photo else _("certificate")
	name = re.sub(r"[\\/\x00-\x1f]", "_", (filename or "").strip())[-120:]
	if not name or not content:
		pf._fail(_("Choose a {0} file.").format(label), "file")
	extension = name[name.rfind(".") :].lower() if "." in name else ""
	if extension not in allowed:
		kinds = ", ".join(sorted({e.lstrip(".").upper() for e in allowed}))
		pf._fail(_("A {0} must be a {1} file.").format(label, kinds), "file")
	limit = MAX_PHOTO_BYTES if photo else MAX_CERTIFICATE_BYTES
	if len(content) > limit:
		pf._fail(
			_(
				"{0} is larger than {1} MB. Choose a smaller file, or scan it again at a lower resolution."
			).format(name, limit // (1024 * 1024)),
			"file",
		)
	if _sniff(content) != allowed[extension]:
		pf._fail(_("{0} is not a real {1} file.").format(name, extension.lstrip(".").upper()), "file")
	if allowed[extension] == "application/pdf":
		# Frappe opens a PDF when it stores it: one that cannot be opened is refused here
		# with its own message instead of an error there (as services/visits.py does).
		from pypdf import PdfReader

		try:
			PdfReader(io.BytesIO(content))
		except Exception:
			pf._fail(_("{0} cannot be opened as a PDF.").format(name), "file")
	return name, allowed[extension]


# -- values ----------------------------------------------------------------------------


def _record(ref: DAReference, record_id: str | None) -> dict:
	record = next((q for q in ref.qualifications if q.get("id") == record_id), None) if record_id else None
	if not record:
		pf._fail(
			_("DA {0} has no Education & Professional record {1}.").format(ref.da_id, record_id), "record_id"
		)
	return record


def _member(ref: DAReference, member_id: str | None) -> dict:
	member = next((d for d in ref.dependents if d.get("id") == member_id), None) if member_id else None
	if not member:
		pf._fail(_("DA {0} has no family member {1}.").format(ref.da_id, member_id), "member_id")
	return member


def _date(field: str, value, *, required: bool = False) -> str | None:
	text = pf._text(value)
	if not text:
		if required:
			pf._fail(_("{0} is required.").format(field), field)
		return None
	if not pf._DATE_RE.match(text):
		pf._fail(_("{0} must be a date as YYYY-MM-DD.").format(field), field)
	try:
		day = date.fromisoformat(text)
	except ValueError:
		pf._fail(_("{0} is not a real date.").format(field), field)
	if day > date.today():
		pf._fail(_("{0} cannot be in the future.").format(field), field)
	return text


def _coded(client: DARegistryClient, attribute: str, field: str, value) -> tuple[str, str]:
	text = pf._text(value)
	if not text:
		pf._fail(_("{0} is required.").format(field), field)
	try:
		return pf._code(client, attribute, text, field)
	except pf.InvalidProfileChange as exc:
		exc.field = exc.field or field
		raise


def _record_values(values: dict, client: DARegistryClient, *, partial: bool) -> dict:
	"""Checked values of an Education & Professional record, codes with their labels.

	A record holds the education level and the passout year (and its certificates) only.
	Position, specialisation, joining date and reports_to are the DA's own fields
	(Position and reporting, services/profile_fields.py).
	"""
	moved = sorted(set(values) & {"specialisation", "position", "joined_date", "reports_to"})
	if moved:
		pf._fail(
			_(
				"An education record holds education_level and passout_year only; change {0} on the "
				"DA's Position and reporting fields."
			).format(", ".join(moved)),
			moved[0],
		)
	out: dict = {}
	if not partial or "education_level" in values:
		code, label = _coded(client, "DA_EDUCATION_LEVEL", "education_level", values.get("education_level"))
		out["education_level"], out["education_level_label"] = code, label
	if not partial or "passout_year" in values:
		text = pf._text(values.get("passout_year"))
		latest = pf.ethiopian_year(date.today())
		if not (text.isdigit() and PASSOUT_FIRST_YEAR <= int(text) <= latest):
			pf._fail(
				_("passout_year must be an Ethiopian calendar year from {0} to {1}.").format(
					PASSOUT_FIRST_YEAR, latest
				),
				"passout_year",
			)
		out["passout_year"] = int(text)
	return out


def _member_values(values: dict, client: DARegistryClient, *, partial: bool) -> dict:
	out: dict = {}
	if not partial or "name" in values:
		name = " ".join(pf._text(values.get("name")).split())
		if not name:
			pf._fail(_("name is required."), "name")
		if len(name) > 140 or not _MEMBER_NAME_RE.match(name):
			pf._fail(_("name may hold letters, spaces, apostrophes, hyphens and dots only."), "name")
		out["name"] = name
	if not partial or "relationship" in values:
		code, label = _coded(client, "DA_RELATIONSHIP", "relationship", values.get("relationship"))
		out["relationship"], out["relationship_label"] = code, label
	if not partial or "date_of_birth" in values:
		out["date_of_birth"] = _date("date_of_birth", values.get("date_of_birth"))
	return out


def _changed(current: dict, new: dict) -> dict:
	"""The new values that differ from the current record (labels follow their code)."""
	out = {}
	for key, value in new.items():
		if key.endswith("_label"):
			continue
		if pf._text(current.get(key)).casefold() != pf._text(value).casefold():
			out[key] = value
			if f"{key}_label" in new:
				out[f"{key}_label"] = new[f"{key}_label"]
	return out


def _plain(record: dict, keys) -> dict:
	return {k: record.get(k) for k in keys if k in record}


RECORD_KEYS = ("education_level", "education_level_label", "passout_year")
MEMBER_KEYS = ("name", "relationship", "relationship_label", "date_of_birth")


# -- the requests ------------------------------------------------------------------------


def record_change(
	ref: DAReference, client: DARegistryClient, *, action: str, record_id: str | None, values: dict
) -> tuple[str, dict]:
	"""(section, changes) to add, change or remove an Education & Professional record."""
	action = (action or "").strip().lower()
	if action not in ACTIONS:
		pf._fail(_("action must be one of {0}.").format(", ".join(ACTIONS)), "action")
	values = values if isinstance(values, dict) else {}
	if action == "add":
		new = _record_values(values, client, partial=False)
		same = [q for q in ref.qualifications if all(_same(q.get(k), new.get(k)) for k in pf_keys_core())]
		if same:
			raise pf.NothingToChange(
				_("DA {0} already has this Education & Professional record.").format(ref.da_id)
			)
		return SECTION_RECORDS, {"record:new": {"action": "add", "record_id": None, "from": None, "to": new}}
	current = _record(ref, record_id)
	if action == "delete":
		if len(ref.qualifications) <= 1 and ref.position_code in POSITIONS_WITH_RECORDS:
			pf._fail(
				_("A Development Agent or Senior DA keeps at least one Education & Professional record."),
				"record_id",
			)
		return SECTION_RECORDS, {
			f"record:{record_id}": {
				"action": "delete",
				"record_id": record_id,
				"from": _plain(current, RECORD_KEYS),
				"to": None,
				"certificates": current.get("certificates") or [],
			}
		}
	new = _changed(current, _record_values(values, client, partial=True))
	if not new:
		raise pf.NothingToChange(_("Nothing to change: the new values match the current record."))
	return SECTION_RECORDS, {
		f"record:{record_id}": {
			"action": "update",
			"record_id": record_id,
			"from": _plain(current, [k for k in RECORD_KEYS if k in new]),
			"to": new,
		}
	}


def pf_keys_core() -> tuple[str, ...]:
	return ("education_level", "passout_year")


def _same(a, b) -> bool:
	return pf._text(a).casefold() == pf._text(b).casefold()


def member_change(
	ref: DAReference, client: DARegistryClient, *, action: str, member_id: str | None, values: dict
) -> tuple[str, dict]:
	"""(section, changes) to add, change or remove a family member."""
	action = (action or "").strip().lower()
	if action not in ACTIONS:
		pf._fail(_("action must be one of {0}.").format(", ".join(ACTIONS)), "action")
	values = values if isinstance(values, dict) else {}
	if action == "add":
		new = _member_values(values, client, partial=False)
		if any(
			_same(d.get("name"), new["name"]) and _same(d.get("relationship"), new["relationship"])
			for d in ref.dependents
		):
			raise pf.NothingToChange(
				_("{0} is already a family member of DA {1}.").format(new["name"], ref.da_id)
			)
		return SECTION_FAMILY, {"family:new": {"action": "add", "member_id": None, "from": None, "to": new}}
	current = _member(ref, member_id)
	if action == "delete":
		return SECTION_FAMILY, {
			f"family:{member_id}": {
				"action": "delete",
				"member_id": member_id,
				"from": _plain(current, MEMBER_KEYS),
				"to": None,
			}
		}
	new = _changed(current, _member_values(values, client, partial=True))
	if not new:
		raise pf.NothingToChange(_("Nothing to change: the new values match the current family member."))
	return SECTION_FAMILY, {
		f"family:{member_id}": {
			"action": "update",
			"member_id": member_id,
			"from": _plain(current, [k for k in MEMBER_KEYS if k in new]),
			"to": new,
		}
	}


def certificate_change(
	ref: DAReference,
	*,
	record_id: str | None,
	file_name: str,
	title: str | None,
	issuer: str | None,
	issued_on: str | None,
	expires_on: str | None,
	replaces: str | None,
) -> tuple[str, dict]:
	"""(section, changes) to add a certificate to a record, or put a new file in place of one."""
	record = _record(ref, record_id)
	held = record.get("certificates") or []
	replaces = (replaces or "").strip() or None
	old = next((c for c in held if c.get("document_id") == replaces), None) if replaces else None
	if replaces and not old:
		pf._fail(_("Record {0} has no certificate {1} to replace.").format(record_id, replaces), "replaces")
	if not old and len(held) >= MAX_CERTIFICATES:
		pf._fail(
			_("Record {0} already holds {1} certificates: replace or remove one first.").format(
				record_id, MAX_CERTIFICATES
			),
			"record_id",
		)
	title = " ".join(pf._text(title).split())
	if not title:
		pf._fail(_("title is required."), "title")
	if len(title) > 140:
		pf._fail(_("title is too long."), "title")
	issuer = " ".join(pf._text(issuer).split())[:140] or None
	issued = _date("issued_on", issued_on)
	expires = pf._text(expires_on) or None
	if expires:
		if not pf._DATE_RE.match(expires):
			pf._fail(_("expires_on must be a date as YYYY-MM-DD."), "expires_on")
		if issued and expires < issued:
			pf._fail(_("expires_on cannot be before issued_on."), "expires_on")
	return SECTION_CERTIFICATES, {
		f"certificates:{record_id}": {
			"action": "replace" if old else "add",
			"record_id": record_id,
			"record_label": _record_label(record),
			"from": old,
			"to": {
				"name": file_name,
				"title": title,
				"issuer": issuer,
				"issued_on": issued,
				"expires_on": expires,
			},
		}
	}


def certificate_removal(
	ref: DAReference, *, record_id: str | None, document_id: str | None
) -> tuple[str, dict]:
	record = _record(ref, record_id)
	old = next((c for c in record.get("certificates") or [] if c.get("document_id") == document_id), None)
	if not old:
		pf._fail(_("Record {0} has no certificate {1}.").format(record_id, document_id), "document_id")
	return SECTION_CERTIFICATES, {
		f"certificates:{record_id}": {
			"action": "remove",
			"record_id": record_id,
			"record_label": _record_label(record),
			"from": old,
			"to": None,
		}
	}


def photo_change(ref: DAReference, *, file_name: str | None) -> tuple[str, dict]:
	"""(section, changes) to set the photo (a new file) or, without a file, remove it."""
	if not file_name and not ref.photo_document_id:
		raise pf.NothingToChange(_("DA {0} has no photo to remove.").format(ref.da_id))
	return SECTION_PHOTO, {
		"photo": {
			"action": "set" if file_name else "remove",
			"from": {"document_id": ref.photo_document_id} if ref.photo_document_id else None,
			"to": {"name": file_name} if file_name else None,
		}
	}


def _record_label(record: dict) -> str:
	text = record.get("education_level_label") or record.get("education_level") or ""
	return f"{text} ({record['passout_year']})" if record.get("passout_year") else text


# -- to the registry ----------------------------------------------------------------------

_RECORD_FIELDS = ("education_level", "passout_year")


def registry_change(doc, ref: DAReference, *, document_id: str | None, approver: str) -> SectionChange:
	"""The registry change request for an approved request, built against the DA's live
	record. ``document_id`` is the file this request uploaded (certificate or photo)."""
	entry = next(iter(_changes(doc).values()))
	action = entry.get("action")
	if doc.section == SECTION_PHOTO:
		rows = [
			{
				"internal_record_id": DA_PLACEHOLDER,
				"edit_action": "UPDATE",
				"record_image_document_id": document_id if action == "set" else None,
			}
		]
		return SectionChange(doc.da_id, SECTION_IDENTITY, None, rows, approver, doc.name)
	if doc.section == SECTION_FAMILY:
		row = {"link_internal_record_id": DA_PLACEHOLDER}
		if action == "add":
			row.update({"edit_action": "ADD", "internal_record_id": ""})
		else:
			_member(ref, entry["member_id"])  # still there
			row.update({"edit_action": action.upper(), "internal_record_id": entry["member_id"]})
		values = entry.get("to") or {}
		if action != "delete":
			for ours, theirs in (
				("name", "member_name"),
				("relationship", "relationship"),
				("date_of_birth", "date_of_birth"),
			):
				if ours in values:
					row[theirs] = values[ours]
		return SectionChange(doc.da_id, SECTION_DEPENDENT, DEPENDENT_REGISTER, [row], approver, doc.name)
	if doc.section == SECTION_RECORDS:
		row = {"link_internal_record_id": DA_PLACEHOLDER}
		if action == "add":
			row.update({"edit_action": "ADD", "internal_record_id": ""})
		else:
			_record(ref, entry["record_id"])
			row.update({"edit_action": action.upper(), "internal_record_id": entry["record_id"]})
		if action != "delete":
			row.update({f: v for f, v in (entry.get("to") or {}).items() if f in _RECORD_FIELDS})
		return SectionChange(
			doc.da_id, SECTION_QUALIFICATION, QUALIFICATION_REGISTER, [row], approver, doc.name
		)
	# Certificates: the record's whole list as the registry holds it now, changed.
	record = _record(ref, entry["record_id"])
	held = [
		{"document_id": c["document_id"], "name": c.get("name")} for c in record.get("certificates") or []
	]
	removed = (entry.get("from") or {}).get("document_id") if action in ("replace", "remove") else None
	held = [c for c in held if c["document_id"] != removed]
	if action in ("add", "replace") and document_id not in {c["document_id"] for c in held}:
		held.append({"document_id": document_id, "name": entry["to"]["name"]})
	if len(held) > MAX_CERTIFICATES:
		pf._fail(
			_("Record {0} would hold more than {1} certificates.").format(
				entry["record_id"], MAX_CERTIFICATES
			)
		)
	row = {
		"edit_action": "UPDATE",
		"internal_record_id": entry["record_id"],
		"link_internal_record_id": DA_PLACEHOLDER,
		"certificates": held,
	}
	return SectionChange(doc.da_id, SECTION_QUALIFICATION, QUALIFICATION_REGISTER, [row], approver, doc.name)


def already_applied(doc, ref: DAReference) -> bool:
	"""True when the DA's live record already shows this request (an earlier attempt whose
	answer was lost): a retry then marks it applied instead of sending it twice."""
	entry = next(iter(_changes(doc).values()))
	action = entry.get("action")
	if doc.section == SECTION_PHOTO:
		uploaded = doc.get("uploaded_document_id")
		return (
			(ref.photo_document_id is None)
			if action == "remove"
			else bool(uploaded) and ref.photo_document_id == uploaded
		)
	if doc.section == SECTION_CERTIFICATES:
		record = next((q for q in ref.qualifications if q.get("id") == entry["record_id"]), None)
		if not record:
			return False
		held = {c.get("document_id") for c in record.get("certificates") or []}
		removed = (entry.get("from") or {}).get("document_id")
		if action == "remove":
			return removed not in held
		uploaded = doc.get("uploaded_document_id")
		return bool(uploaded) and uploaded in held and (action == "add" or removed not in held)
	rows, key_id, keys = (
		(ref.qualifications, "record_id", pf_keys_core())
		if doc.section == SECTION_RECORDS
		else (ref.dependents, "member_id", ("name", "relationship"))
	)
	if action == "delete":
		return not any(r.get("id") == entry[key_id] for r in rows)
	to = entry.get("to") or {}
	if action == "add":
		return any(all(_same(r.get(k), to.get(k)) for k in keys if k in to) for r in rows)
	row = next((r for r in rows if r.get("id") == entry[key_id]), None)
	return bool(row) and all(_same(row.get(k), v) for k, v in to.items() if not k.endswith("_label"))


def _changes(doc) -> dict:
	return frappe.parse_json(doc.changes) if isinstance(doc.changes, str) else (doc.changes or {})


# -- the file a request carries ----------------------------------------------------------------


def request_file(doc):
	"""The private File a certificate or photo request carries, or None."""
	name = frappe.db.get_value(
		"File",
		{"attached_to_doctype": doc.doctype, "attached_to_name": doc.name, "is_private": 1},
		"name",
		order_by="creation desc",
	)
	return frappe.get_doc("File", name) if name else None


def attach_file(doc, file_name: str, content: bytes) -> None:
	frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": doc.doctype,
			"attached_to_name": doc.name,
			"is_private": 1,
			"content": content,
		}
	).insert(ignore_permissions=True)


def save_certificate(doc, entry: dict, *, document_id: str | None = None) -> None:
	"""The certificate's own details, kept in DA Services (the registry keeps the file only)."""
	to = entry.get("to") or {}
	frappe.get_doc(
		{
			"doctype": CERTIFICATE,
			"da_id": doc.da_id,
			"record_id": entry.get("record_id"),
			"title": to.get("title"),
			"issuer": to.get("issuer"),
			"issued_on": to.get("issued_on"),
			"expires_on": to.get("expires_on"),
			"file_name": to.get("name"),
			"change_request": doc.name,
			"replaces_document_id": (entry.get("from") or {}).get("document_id"),
			"registry_document_id": document_id,
		}
	).insert(ignore_permissions=True)


def after_applied(doc) -> None:
	"""The registry applied a certificate request: its details now name the registry document."""
	if doc.section == SECTION_CERTIFICATES and doc.get("uploaded_document_id"):
		frappe.db.set_value(
			CERTIFICATE,
			{"change_request": doc.name},
			"registry_document_id",
			doc.uploaded_document_id,
			update_modified=False,
		)
