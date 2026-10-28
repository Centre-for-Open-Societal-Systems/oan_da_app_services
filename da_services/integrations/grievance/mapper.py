"""Translate grievance service payloads into our schemas.

Field names on the right-hand side are the Grievance Service API contract names.
Change them here and nowhere else.
"""

from __future__ import annotations

import re

from .schemas import GrievanceDetail, GrievanceResult, GrievanceSubmission, GrievanceSummary, TimelineEntry

SUBMISSION_CHANNEL = "Development Agent Assisted"
ASSISTED_ROLE = "Grievance Assisted Submitter"
_ALREADY_FILED = re.compile(r"already been filed as\s+([A-Za-z0-9][A-Za-z0-9-]*)", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")


def unwrap_data(body):
	"""Return the payload inside the grievance service envelope.

	Success bodies are ``{"status": "success", "data": ...}``. A few collection
	scripts also accept ``message.data``.
	"""
	if not isinstance(body, dict):
		return body
	message = body.get("message")
	if isinstance(message, dict) and "data" in message:
		return message["data"]
	if "data" in body:
		return body["data"]
	return body


def error_text(body, fallback: str = "") -> str:
	parts = [fallback or ""]
	if isinstance(body, dict):
		message = body.get("message")
		if isinstance(message, dict):
			message = message.get("message") or ""
		parts.append(str(message or ""))
		server = body.get("_server_messages")
		if server:
			parts.append(str(server))
		exc = body.get("exception") or body.get("exc")
		if exc:
			parts.append(str(exc))
	return _TAGS.sub("", " ".join(part for part in parts if part))


def ticket_from_duplicate(text: str) -> str | None:
	"""Ticket named when the service refuses a repeated client_submission_uuid."""
	match = _ALREADY_FILED.search(_TAGS.sub("", text or ""))
	return match.group(1) if match else None


def to_upstream_submission(submission: GrievanceSubmission) -> dict:
	"""K6 body. ``subject`` has no upstream field, so it is folded into the description."""
	description = (submission.description or "").strip()
	subject = (submission.subject or "").strip()
	if subject and subject not in description:
		description = f"{subject}\n\n{description}".strip() if description else subject

	body = {
		"client_submission_uuid": submission.client_submission_uuid,
		"submission_channel": SUBMISSION_CHANNEL,
		"submitter_type": submission.submitter_type or "Individual Farmer",
		"submitter_name": submission.submitter_name or submission.complainant_name,
		"contact_mobile": submission.contact_mobile,
		"administrative_area": submission.administrative_area,
		"administrative_unit": submission.administrative_unit,
		"service_category": submission.service_category or submission.category or None,
		"grievance_type": submission.grievance_type or submission.subcategory or None,
		"description": description,
		"desired_outcome": submission.desired_outcome,
		"is_anonymous": 1 if submission.is_anonymous else 0,
		"consent_given": 1 if submission.consent_given else 0,
	}
	return {key: value for key, value in body.items() if value is not None and value != ""} | {
		"submission_channel": SUBMISSION_CHANNEL,
		"is_anonymous": body["is_anonymous"],
		"consent_given": body["consent_given"],
	}


def _priority(payload: dict) -> str:
	if "priority" in payload and payload["priority"] is not None:
		return payload["priority"]
	return "Medium"


def _ticket(payload: dict) -> str:
	return str(payload.get("ticket_id") or payload.get("ticket_number") or "")


def normalize_summary(payload: dict) -> dict:
	"""List-item shape from either the mock or ``GET /v1/grievances``."""
	if payload.get("ticket_id") and "ticket_number" not in payload and "submitter_name" not in payload:
		return payload
	description = payload.get("description") or ""
	subject = payload.get("subject") or str(description).split("\n", 1)[0][:140]
	ticket = _ticket(payload)
	return {
		"ticket_id": ticket,
		"ticket_number": payload.get("ticket_number") or ticket,
		"subject": subject,
		"status": payload.get("status") or "",
		"priority": payload.get("priority") or "",
		"category": payload.get("service_category") or payload.get("category") or "",
		"complainant_name": payload.get("submitter_name") or payload.get("complainant_name") or "",
		"created_at": payload.get("submitted_on") or payload.get("created_at"),
		"updated_at": payload.get("updated_at"),
	}


def normalize_event(payload: dict) -> dict:
	if payload.get("timestamp") or payload.get("actor"):
		return payload
	return {
		"timestamp": payload.get("created_on") or "",
		"action": payload.get("action") or payload.get("entry_type") or "",
		"actor": payload.get("author_name") or payload.get("author_role") or "",
		"detail": payload.get("body") or "",
	}


def normalize_case(payload: dict) -> dict:
	"""Detail shape from a track body or from the timeline case envelope."""
	if not isinstance(payload, dict):
		return {}
	summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else None
	submitter = payload.get("submitter") if isinstance(payload.get("submitter"), dict) else None
	if summary is None and submitter is None and payload.get("ticket_id"):
		return payload

	sla = payload.get("sla") if isinstance(payload.get("sla"), dict) else {}
	description = (summary or {}).get("description") or payload.get("description") or ""
	subject = payload.get("subject") or str(description).split("\n", 1)[0][:140]
	ticket = _ticket(payload)
	events = payload.get("timeline") or []
	return {
		"ticket_id": ticket,
		"ticket_number": payload.get("ticket_number") or ticket,
		"subject": subject,
		"description": description,
		"status": payload.get("status") or "",
		"priority": payload.get("priority") or "",
		"category": (summary or {}).get("service_category")
		or payload.get("service_category")
		or payload.get("category")
		or "",
		"subcategory": (summary or {}).get("grievance_type_name")
		or (summary or {}).get("grievance_type_id")
		or payload.get("grievance_type")
		or payload.get("subcategory")
		or "",
		"complainant_id": (submitter or {}).get("assisted_by_officer") or payload.get("complainant_id") or "",
		"complainant_name": (submitter or {}).get("name")
		or payload.get("submitter_name")
		or payload.get("complainant_name")
		or "",
		"complainant_role": (submitter or {}).get("submitter_type") or payload.get("complainant_role") or "",
		"region": payload.get("region"),
		"woreda": (summary or {}).get("administrative_area")
		or payload.get("administrative_area")
		or payload.get("woreda"),
		"assigned_to": (
			(payload.get("assignment") or {}) if isinstance(payload.get("assignment"), dict) else {}
		).get("assigned_to")
		or payload.get("assigned_to"),
		"resolution": payload.get("resolution"),
		"created_at": payload.get("submitted_on") or payload.get("created_at"),
		"updated_at": payload.get("updated_at"),
		"resolved_at": payload.get("resolved_at"),
		"sla_due_date": sla.get("sla_due_date") or payload.get("sla_due_date"),
		"sla_consumed_percent": sla.get("sla_consumed_percent")
		if "sla_consumed_percent" not in payload
		else payload.get("sla_consumed_percent"),
		"timeline": [normalize_event(event) for event in events if isinstance(event, dict)],
	}


def to_options(payload: dict) -> dict:
	"""Public ``/v1/submitters/options`` into the dropdown shape the DA route already returns."""
	categories: dict[str, list[str]] = {}
	raw_categories = payload.get("service_categories") or []
	for item in raw_categories:
		name = _label(item, "category_name", "name", "value")
		if name:
			categories.setdefault(name, [])
	for item in payload.get("grievance_types") or []:
		type_name = _label(item, "type_name", "name", "value")
		parent = ""
		if isinstance(item, dict):
			parent = _label(item.get("service_category"), "category_name", "name") or _label(
				item, "service_category"
			)
		if isinstance(item, str):
			type_name = item
		if not type_name:
			continue
		if parent:
			categories.setdefault(parent, [])
			if type_name not in categories[parent]:
				categories[parent].append(type_name)
		elif categories:
			first = next(iter(categories))
			if type_name not in categories[first]:
				categories[first].append(type_name)
	return {
		"categories": [{"value": name, "subcategories": types} for name, types in categories.items()],
		"priorities": list(payload.get("priorities") or []),
		"statuses": [
			_label(item, "status", "name", "value") or item for item in (payload.get("statuses") or [])
		],
		"service_categories": raw_categories,
		"grievance_types": payload.get("grievance_types") or [],
		"submitter_types": payload.get("submitter_types") or [],
		"submission_types": payload.get("submission_types") or [],
	}


def _label(item, *keys: str) -> str:
	if isinstance(item, str):
		return item
	if not isinstance(item, dict):
		return ""
	for key in keys:
		value = item.get(key)
		if isinstance(value, str) and value:
			return value
	return ""


def to_grievance_summary(payload: dict) -> GrievanceSummary:
	ticket = _ticket(payload)
	return GrievanceSummary(
		ticket_id=ticket,
		subject=payload.get("subject") or "",
		status=payload.get("status") or "",
		priority=_priority(payload),
		category=payload.get("category") or "",
		complainant_name=payload.get("complainant_name") or "",
		created_at=payload.get("created_at"),
		updated_at=payload.get("updated_at"),
		ticket_number=payload.get("ticket_number") or ticket,
	)


def to_grievance_detail(payload: dict) -> GrievanceDetail:
	raw_timeline = payload.get("timeline") or []
	timeline = [
		to_timeline_entry(normalize_event(e) if isinstance(e, dict) else {}).to_dict() for e in raw_timeline
	]
	ticket = _ticket(payload)
	return GrievanceDetail(
		ticket_id=ticket,
		subject=payload.get("subject") or "",
		description=payload.get("description") or "",
		status=payload.get("status") or "",
		priority=_priority(payload),
		category=payload.get("category") or "",
		subcategory=payload.get("subcategory") or "",
		complainant_id=str(payload.get("complainant_id") or ""),
		complainant_name=payload.get("complainant_name") or "",
		complainant_role=payload.get("complainant_role") or "",
		region=payload.get("region"),
		woreda=payload.get("woreda"),
		assigned_to=payload.get("assigned_to"),
		resolution=payload.get("resolution"),
		created_at=payload.get("created_at"),
		updated_at=payload.get("updated_at"),
		resolved_at=payload.get("resolved_at"),
		timeline=timeline,
		ticket_number=payload.get("ticket_number") or ticket,
		sla_due_date=payload.get("sla_due_date"),
		sla_consumed_percent=payload.get("sla_consumed_percent"),
	)


def to_timeline_entry(payload: dict) -> TimelineEntry:
	return TimelineEntry(
		timestamp=payload.get("timestamp") or "",
		action=payload.get("action") or "",
		actor=payload.get("actor") or "",
		detail=payload.get("detail") or "",
	)


def to_grievance_result(payload: dict) -> GrievanceResult:
	ticket = payload.get("ticket_id") or payload.get("ticket_number")
	return GrievanceResult(
		accepted=bool(payload.get("accepted", False)),
		ticket_id=ticket,
		ticket_number=payload.get("ticket_number") or ticket,
		message=payload.get("message"),
		retryable=bool(payload.get("retryable", False)),
		raw=payload,
	)
