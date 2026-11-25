"""In-memory Grievance Service fixtures for unit and API tests only.

Not used in production — ``get_client()`` always builds ``HttpGrievanceClient``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .client import GrievanceClient, GrievanceNotFound, GrievanceRejected
from .mapper import location_text, to_attachment, to_grievance_detail, to_grievance_summary
from .schemas import (
	EvidenceFile,
	GrievanceAction,
	GrievanceDetail,
	GrievanceResult,
	GrievanceSubmission,
	GrievanceSummary,
)

# The Grievance Service's evidence rules (its grievance_attachment.py and attachment.py),
# copied so the mock refuses what the service refuses.
EVIDENCE_MAX_FILES = 10
EVIDENCE_MAX_BYTES = 10 * 1024 * 1024
EVIDENCE_TYPES = {
	".jpg": "image/jpeg",
	".jpeg": "image/jpeg",
	".png": "image/png",
	".pdf": "application/pdf",
	".mp3": "audio/mpeg",
}

_COUNTER = 100

FIXTURE_GRIEVANCES: dict[str, dict] = {
	"GRV-000001": {
		"ticket_id": "GRV-000001",
		"subject": "Fertilizer delivery delayed",
		"description": "Expected delivery on 2026-09-15 has not arrived.",
		"status": "Open",
		"category": "Supply Chain",
		"subcategory": "Input Delivery",
		"complainant_id": "DA-000001",
		"complainant_name": "Abebe Kebede",
		"complainant_role": "Development Agent",
		"region": "ET04",
		"woreda": "ET04-W01",
		"assigned_to": None,
		"resolution": None,
		"created_at": "2026-09-20T10:00:00Z",
		"updated_at": "2026-09-20T10:00:00Z",
		"resolved_at": None,
		"timeline": [
			{
				"timestamp": "2026-09-20T10:00:00Z",
				"action": "created",
				"actor": "DA-000001",
				"detail": "Grievance submitted",
			},
		],
	},
	"GRV-000002": {
		"ticket_id": "GRV-000002",
		"subject": "Training schedule conflict",
		"description": "Two training sessions scheduled at the same time for ET04-W01-K03.",
		"status": "In Progress",
		"category": "Training",
		"subcategory": "Scheduling",
		"complainant_id": "DA-000001",
		"complainant_name": "Abebe Kebede",
		"complainant_role": "Development Agent",
		"region": "ET04",
		"woreda": "ET04-W01",
		"assigned_to": "supervisor@example.com",
		"resolution": None,
		"created_at": "2026-09-18T08:30:00Z",
		"updated_at": "2026-09-19T14:00:00Z",
		"resolved_at": None,
		"timeline": [
			{
				"timestamp": "2026-09-18T08:30:00Z",
				"action": "created",
				"actor": "DA-000001",
				"detail": "Grievance submitted",
			},
			{
				"timestamp": "2026-09-19T14:00:00Z",
				"action": "assigned",
				"actor": "admin@example.com",
				"detail": "Assigned to supervisor@example.com",
			},
		],
	},
	"GRV-000003": {
		"ticket_id": "GRV-000003",
		"subject": "Incorrect KPI data recorded",
		"description": "Monthly KPI for September shows wrong crop yield figures.",
		"status": "Resolved",
		"category": "Data Quality",
		"subcategory": "KPI",
		"complainant_id": "DA-000002",
		"complainant_name": "Marta Tesfaye",
		"complainant_role": "Development Agent",
		"region": "ET04",
		"woreda": "ET04-W01",
		"assigned_to": "supervisor@example.com",
		"resolution": "KPI data corrected in the system.",
		"created_at": "2026-09-10T09:00:00Z",
		"updated_at": "2026-09-12T16:00:00Z",
		"resolved_at": "2026-09-12T16:00:00Z",
		"timeline": [
			{
				"timestamp": "2026-09-10T09:00:00Z",
				"action": "created",
				"actor": "DA-000002",
				"detail": "Grievance submitted",
			},
			{
				"timestamp": "2026-09-11T10:00:00Z",
				"action": "assigned",
				"actor": "admin@example.com",
				"detail": "Assigned to supervisor@example.com",
			},
			{
				"timestamp": "2026-09-12T16:00:00Z",
				"action": "resolved",
				"actor": "supervisor@example.com",
				"detail": "KPI data corrected in the system.",
			},
		],
	},
}

_FILTER_FIELDS = {
	"category": "category",
	"type": "subcategory",
	"zone": "zone",
	"kebele": "kebele",
	"location": "location",
	"administrative_unit": "administrative_unit",
	"assigned_dept": "assigned_dept",
	"department": "assigned_dept",
	"dept": "assigned_dept",
	"assigned_to": "assigned_to",
	"submission_channel": "submission_channel",
	"channel": "submission_channel",
	"submitter": "complainant_id",
	"escalated": "escalated",
	"is_escalated": "escalated",
	"is_anonymous": "is_anonymous",
}


def _choices(value) -> list[str]:
	if isinstance(value, (list, tuple)):
		return [str(item) for item in value if str(item).strip()]
	return [part.strip() for part in str(value).split(",") if part.strip()]


def _flag(value) -> bool:
	return str(value).lower() in ("1", "true", "yes")


def _field_matches(record: dict, key: str, expected) -> bool:
	field = _FILTER_FIELDS.get(key, key)
	choices = _choices(expected)
	if field == "location":
		actual = location_text(record) or ""
		return any(choice == actual or choice.lower() in actual.lower() for choice in choices)
	if field in ("escalated", "is_anonymous"):
		return bool(record.get(field)) is _flag(expected)
	actual = record.get(field)
	return actual is not None and str(actual) in choices


# Queue cards on GET /v1/grievances/summary. Open is not a card.
_SUMMARY_CARDS = (
	("All", "All", 0, 0),
	("Submitted", "Submitted", 1, 0),
	("Reopened", "Reopened", 1, 0),
	("In Progress", "In Progress", 1, 0),
	("More Info Needed", "More Info Needed", 1, 0),
	("Rejected", "Rejected", 0, 1),
	("Resolved", "Resolved", 0, 1),
	("Closed", "Closed", 0, 1),
)
_CARD_STATUSES = {status for status, _label, _is_open, _is_terminal in _SUMMARY_CARDS if status != "All"}


FIXTURE_OPTIONS: dict = {
	"categories": [
		{
			"value": "Supply Chain",
			"label": "Supply Chain",
			"subcategories": ["Input Delivery", "Equipment", "Storage"],
		},
		{"value": "Training", "label": "Training", "subcategories": ["Scheduling", "Content", "Logistics"]},
		{
			"value": "Data Quality",
			"label": "Data Quality",
			"subcategories": ["KPI", "Registration", "Survey"],
		},
		{
			"value": "Administrative",
			"label": "Administrative",
			"subcategories": ["Leave", "Transfer", "Policy"],
		},
	],
	"statuses": [
		{"status": status, "label": label, "order": order, "is_open": is_open, "is_terminal": is_terminal}
		for order, (status, label, is_open, is_terminal) in enumerate(_SUMMARY_CARDS, start=1)
		if status != "All"
	],
}


class MockGrievanceClient(GrievanceClient):
	_instance: MockGrievanceClient | None = None

	def __init__(self, records: dict[str, dict] | None = None):
		self._records = {k: dict(v) for k, v in (records or FIXTURE_GRIEVANCES).items()}
		self._by_uuid: dict[str, str] = {}
		# ticket -> evidence rows, each with its bytes under "content"
		self._attachments: dict[str, list[dict]] = {}
		self._attachment_counter = 0

	@classmethod
	def shared(cls) -> MockGrievanceClient:
		if cls._instance is None:
			cls._instance = cls()
		return cls._instance

	@classmethod
	def reset(cls) -> None:
		cls._instance = None

	def submit_grievance(self, submission: GrievanceSubmission) -> GrievanceResult:
		global _COUNTER
		existing = submission.client_submission_uuid and self._by_uuid.get(submission.client_submission_uuid)
		if existing and existing in self._records:
			return GrievanceResult(
				accepted=True,
				ticket_id=existing,
				ticket_number=existing,
				message="Already submitted",
			)
		_COUNTER += 1
		ticket_id = f"GRV-{_COUNTER:06d}"
		now = datetime.now(UTC).isoformat()
		record = {
			"ticket_id": ticket_id,
			"subject": submission.subject,
			"description": submission.description,
			"status": "Open",
			"category": submission.category,
			"subcategory": submission.subcategory,
			"complainant_id": submission.complainant_id,
			"complainant_name": submission.complainant_name,
			"complainant_role": submission.complainant_role,
			"region": submission.region,
			"woreda": submission.woreda,
			"assigned_to": None,
			"resolution": None,
			"created_at": now,
			"updated_at": now,
			"resolved_at": None,
			"timeline": [
				{
					"timestamp": now,
					"action": "created",
					"actor": submission.complainant_id,
					"detail": "Grievance submitted",
				}
			],
		}
		self._records[ticket_id] = record
		if submission.client_submission_uuid:
			self._by_uuid[submission.client_submission_uuid] = ticket_id
		return GrievanceResult(
			accepted=True,
			ticket_id=ticket_id,
			ticket_number=ticket_id,
			message="Grievance created",
			raw={"created_at": now},
		)

	def list_grievances(
		self,
		*,
		region: str | list[str] | None = None,
		woreda: str | list[str] | None = None,
		complainant_id: str | list[str] | None = None,
		status: str | None = None,
		page: int = 1,
		page_size: int = 20,
		service_category: str | None = None,
		grievance_type: str | None = None,
		search: str | None = None,
		administrative_area: str | None = None,
		from_date: str | None = None,
		to_date: str | None = None,
		**filters,
	) -> tuple[list[GrievanceSummary], int]:
		filtered = list(self._records.values())
		if region:
			regions = _choices(region)
			filtered = [r for r in filtered if r.get("region") in regions]
		if woreda:
			woredas = _choices(woreda)
			filtered = [r for r in filtered if r.get("woreda") in woredas]
		if complainant_id:
			ids = _choices(complainant_id)
			filtered = [r for r in filtered if r.get("complainant_id") in ids]
		if status:
			statuses = _choices(status)
			filtered = [r for r in filtered if r.get("status") in statuses]
		if service_category:
			categories = _choices(service_category)
			filtered = [r for r in filtered if r.get("category") in categories]
		if grievance_type:
			types = _choices(grievance_type)
			filtered = [r for r in filtered if r.get("subcategory") in types]
		if administrative_area:
			areas = _choices(administrative_area)
			filtered = [
				r for r in filtered if r.get("woreda") in areas or r.get("administrative_area") in areas
			]
		if from_date:
			filtered = [r for r in filtered if (r.get("created_at") or "") >= str(from_date)]
		if to_date:
			end = str(to_date) if len(str(to_date)) > 10 else f"{to_date}T23:59:59"
			filtered = [r for r in filtered if (r.get("created_at") or "") <= end]
		if search:
			needle = search.lower()
			filtered = [
				r
				for r in filtered
				if needle in (r.get("subject") or "").lower()
				or needle in (r.get("description") or "").lower()
				or needle in (r.get("ticket_id") or "").lower()
			]
		for key, expected in filters.items():
			if key in ("sort_by", "sort_order", "limit") or expected is None or expected == "":
				continue
			filtered = [r for r in filtered if _field_matches(r, key, expected)]

		sort_by = filters.get("sort_by") or "created_at"
		sort_field = "created_at" if sort_by in ("creation", "created_at") else sort_by
		descending = str(filters.get("sort_order") or "desc").lower() != "asc"
		filtered.sort(key=lambda row: str(row.get(sort_field) or ""), reverse=descending)

		total = len(filtered)
		start = (page - 1) * page_size
		page_items = filtered[start : start + page_size]
		return [to_grievance_summary(r) for r in page_items], total

	def get_grievance(self, ticket_id: str) -> GrievanceDetail:
		record = self._records.get(ticket_id)
		if not record:
			raise GrievanceNotFound(ticket_id)
		return to_grievance_detail(record)

	def take_action(self, action: GrievanceAction) -> GrievanceResult:
		record = self._records.get(action.ticket_id)
		if not record:
			raise GrievanceNotFound(action.ticket_id)

		now = datetime.now(UTC).isoformat()
		record["updated_at"] = now

		if action.action_type == "resolve":
			record["status"] = "Resolved"
			record["resolution"] = action.resolution or action.comment
			record["resolved_at"] = now
		elif action.action_type == "escalate":
			record["status"] = "Escalated"
		elif action.action_type == "reassign":
			record["assigned_to"] = action.assigned_to
		elif action.action_type == "close":
			record["status"] = "Closed"

		timeline = record.setdefault("timeline", [])
		timeline.append(
			{
				"timestamp": now,
				"action": action.action_type,
				"actor": action.assigned_to or "system",
				"detail": action.comment,
			}
		)
		return GrievanceResult(
			accepted=True, ticket_id=action.ticket_id, message=f"Action '{action.action_type}' applied"
		)

	def get_timeline(self, ticket_id: str) -> list[dict]:
		record = self._records.get(ticket_id)
		if not record:
			raise GrievanceNotFound(ticket_id)
		return record.get("timeline") or []

	def get_options(self, **_params) -> dict:
		return FIXTURE_OPTIONS

	def get_summary(self) -> dict:
		"""Queue cards from the grievance summary contract.

		A fixture status that is not a card, such as Open, counts under In Progress.
		``is_open`` and ``is_terminal`` follow that card list. A live summary is not remapped.
		"""
		counts = {status: 0 for status in _CARD_STATUSES}
		for record in self._records.values():
			status = record.get("status") or ""
			if status in counts:
				counts[status] += 1
			elif status:
				counts["In Progress"] += 1
		cards = []
		for order, (status, label, is_open, is_terminal) in enumerate(_SUMMARY_CARDS, start=1):
			count = len(self._records) if status == "All" else counts[status]
			cards.append(
				{
					"status": status,
					"label": label,
					"order": order,
					"is_open": is_open,
					"is_terminal": is_terminal,
					"count": count,
				}
			)
		return {"cards": cards}

	# ------------------------------------------------------------------ evidence

	def upload_attachments(
		self, ticket_id: str, files: list[EvidenceFile], *, document_type: str | None = None
	) -> list[dict]:
		"""The service's refusals: unknown ticket, file type, size, and ten files a case.

		The type is taken from the file name here. The live service reads the leading
		bytes instead, so a renamed file passes this mock and is refused there.
		"""
		if ticket_id not in self._records:
			raise GrievanceNotFound(ticket_id)
		stored = self._attachments.setdefault(ticket_id, [])
		if len(stored) + len(files) > EVIDENCE_MAX_FILES:
			raise GrievanceRejected(f"A grievance may carry at most {EVIDENCE_MAX_FILES} attachments.")
		for upload in files:
			if not upload.content:
				raise GrievanceRejected(f"The uploaded file {upload.file_name} is empty.")
			if len(upload.content) > EVIDENCE_MAX_BYTES:
				raise GrievanceRejected(f"File '{upload.file_name}' exceeds maximum size of 10 MB.")
			if _extension(upload.file_name) not in EVIDENCE_TYPES:
				raise GrievanceRejected(
					f"{upload.file_name} is not a file type we recognise. Allowed: JPG, PNG, PDF and MP3."
				)
		created = []
		for upload in files:
			self._attachment_counter += 1
			row = {
				"name": f"GAT-{self._attachment_counter:05d}",
				"file_name": upload.file_name,
				"mime_type": EVIDENCE_TYPES[_extension(upload.file_name)],
				"size_bytes": len(upload.content),
				"document_type": document_type,
				"timeline_entry": None,
				"scan_status": "Pending",
				"scanned_at": None,
				"creation": datetime.now(UTC).isoformat(),
				"content": upload.content,
			}
			stored.append(row)
			created.append(to_attachment(row))
		return created

	def list_attachments(self, ticket_id: str) -> list[dict]:
		"""A file comes back Pending from the upload and Clean from the first list after it,
		which stands in for the virus scan finishing."""
		if ticket_id not in self._records:
			raise GrievanceNotFound(ticket_id)
		rows = self._attachments.get(ticket_id, [])
		for row in rows:
			if row["scan_status"] == "Pending":
				row["scan_status"] = "Clean"
				row["scanned_at"] = datetime.now(UTC).isoformat()
		return [to_attachment(row) for row in rows]

	def get_attachment_content(self, attachment_id: str) -> bytes:
		for rows in self._attachments.values():
			for row in rows:
				if row["name"] != attachment_id:
					continue
				if row["scan_status"] != "Clean":
					raise GrievanceRejected(
						f"{row['file_name']} is not available: its scan status is {row['scan_status']}."
					)
				return row["content"]
		raise GrievanceNotFound(attachment_id)


def _extension(file_name: str) -> str:
	"""Lower-case extension with its dot (".pdf"), or "" when the name has none."""
	name = file_name.lower()
	return name[name.rfind(".") :] if "." in name else ""
