"""Survey templates and responses (FR-09d / docs/api-da-screens.md §2.16).

Supervisor/Admin publishes a template into their Woreda; DAs in that Woreda list it
as an assigned survey and capture one response per farmer. Executive reads results
in Region. No separate Deployment DocType in v1 — create = publish.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate, now_datetime, today

from da_services.da_services.doctype.da_survey_response.da_survey_response import CONSENTS, SYNC_STATES
from da_services.da_services.doctype.da_survey_template.da_survey_template import (
	CUSTOM_QUESTION_TYPES,
	SURVEY_TYPES,
)
from da_services.services import constants as C
from da_services.services import farmers as farmer_svc
from da_services.utils.permissions import ScopeContext, require_roles

TEMPLATE_DOCTYPE = "DA Survey Template"
RESPONSE_DOCTYPE = "DA Survey Response"


class SurveyConflictError(frappe.ValidationError):
	"""A duplicate or an unsafe idempotency replay. Mapped to HTTP 409."""

	http_status_code = 409


CREATE_ROLES = (C.ROLE_SUPERVISOR, C.ROLE_ADMIN)
LIST_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)
CAPTURE_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_ADMIN)
RESULTS_ROLES = (C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)

SURVEY_LANGUAGES = ("English", "Amharic", "Afaan Oromoo")
TYPE_CODES = {"Satisfaction": "FSS", "Service quality": "SQ", "Needs assessment": "NA"}
CHOICE_TYPES = frozenset({"single", "multi", "lookup"})
CLOSING_SOON_DAYS = 7
MAX_TEXT = 500
RATING_VALUES = frozenset({"1", "2", "3", "4", "5"})

# XLSX export columns that already exist on DA Survey Response / DA Survey Template.
# Kept as data so the proposed contract is reviewable in docs/surveys-integration.md.
EXPORT_SHEET = "Survey Responses"
EXPORT_BASE_COLUMNS = (
	"Response ID",
	"Survey ID",
	"Template Version",
	"Farmer ID",
	"Farmer Registry ID",
	"Collector DA ID",
	"Consent",
	"Sync State",
	"Started At",
	"Completed At",
	"Region",
	"Zone",
	"Woreda",
	"Kebele",
)

STANDARD_PARAMS = (
	{
		"parameter_id": "p1",
		"sort_order": 1,
		"prompt": (
			"The farmer consents to take part in this survey and understands responses "
			"are used to improve extension services."
		),
		"response_type": "consent",
		"required": 1,
		"helper": "A declined consent ends the response.",
	},
	{
		"parameter_id": "p2",
		"sort_order": 2,
		"prompt": "Farmer ID",
		"response_type": "auto",
		"required": 1,
		"prefill": "Farmer Registry",
	},
	{
		"parameter_id": "p3",
		"sort_order": 3,
		"prompt": "Kebele",
		"response_type": "auto",
		"required": 1,
		"prefill": "DA assignment",
	},
)


def _json_load(raw, default=None):
	if raw in (None, ""):
		return default
	if isinstance(raw, (list, dict)):
		return raw
	try:
		return json.loads(raw)
	except TypeError, ValueError:
		return default


def _json_dump(value) -> str | None:
	if value is None:
		return None
	return json.dumps(value, ensure_ascii=False)


def format_survey_date(value) -> str:
	"""Frontend display form: '08 Sep 2026'."""
	d = getdate(value)
	return d.strftime("%d %b %Y")


def derive_ui_status(status: str, window_close) -> str:
	if status == "Closed":
		return "Closed"
	close = getdate(window_close)
	if close < getdate(today()):
		return "Closed"
	if close <= getdate(today()) + timedelta(days=CLOSING_SOON_DAYS):
		return "Closing soon"
	return "Open"


def _options_of(raw) -> list[str]:
	if isinstance(raw, list):
		return [str(o).strip() for o in raw if str(o).strip()]
	if not raw:
		return []
	if isinstance(raw, str):
		# Accept JSON list or comma-separated choices from the create form.
		parsed = _json_load(raw)
		if isinstance(parsed, list):
			return [str(o).strip() for o in parsed if str(o).strip()]
		return list(dict.fromkeys(o.strip() for o in raw.split(",") if o.strip()))
	return []


def serialize_parameter(row) -> dict:
	out = {
		"id": row.parameter_id,
		"order": int(row.sort_order or 0),
		"prompt": row.prompt,
		"type": row.response_type,
		"required": bool(row.required),
	}
	options = _options_of(row.options_json)
	if options:
		out["options"] = options
	if row.helper:
		out["helper"] = row.helper
	if row.prefill:
		out["prefill"] = row.prefill
	if row.condition_parameter_id and row.condition_equals is not None:
		out["condition"] = {
			"parameterId": row.condition_parameter_id,
			"equals": row.condition_equals,
		}
	return out


def _response_counts(survey_id: str, collector_da_id: str | None = None) -> dict:
	filters: dict = {"survey": survey_id}
	if collector_da_id:
		filters["da_id"] = collector_da_id
	rows = frappe.get_all(
		RESPONSE_DOCTYPE,
		filters=filters,
		fields=["farmer_id", "sync_state", "consent"],
	)
	collected = 0
	queued = 0
	answered: list[str] = []
	for r in rows:
		if r.sync_state == "Queued":
			queued += 1
		if r.sync_state in ("Synced", "Queued") and r.consent != "declined":
			collected += 1
			if r.farmer_id and r.farmer_id not in answered:
				answered.append(r.farmer_id)
	return {"collected": collected, "queued": queued, "answered": answered}


def serialize_survey(
	doc,
	*,
	include_parameters: bool = True,
	collector_da_id: str | None = None,
) -> dict:
	counts = _response_counts(doc.name, collector_da_id=collector_da_id)
	stored = doc.status
	ui_status = derive_ui_status(stored, doc.window_close)
	languages = _json_load(doc.languages_json, []) or []
	out = {
		"id": doc.name,
		"name": doc.title,
		"templateVersion": doc.template_version,
		"type": doc.survey_type,
		"window": {
			"open": format_survey_date(doc.window_open),
			"close": format_survey_date(doc.window_close),
		},
		"window_open": str(getdate(doc.window_open)),
		"window_close": str(getdate(doc.window_close)),
		"languages": languages,
		"targetFarmers": int(doc.target_farmers or 0),
		"collected": counts["collected"],
		"queued": counts["queued"],
		"status": ui_status,
		"estMinutes": int(doc.est_minutes or 0),
		"answered": counts["answered"],
		"region": doc.region,
		"zone": doc.zone,
		"woreda": doc.woreda,
		"created_by_user": doc.created_by_user,
	}
	if include_parameters:
		out["parameters"] = [serialize_parameter(r) for r in doc.parameters]
	return out


def serialize_response(doc) -> dict:
	return {
		"id": doc.name,
		"survey_id": doc.survey,
		"collector_da_id": doc.da_id,
		"farmer_id": doc.farmer_id,
		"consent": doc.consent or None,
		"answers": _json_load(doc.answers_json, {}) or {},
		"sync_state": doc.sync_state,
		"started_at": doc.started_at.isoformat() if doc.started_at else None,
		"completed_at": doc.completed_at.isoformat() if doc.completed_at else None,
		"client_op_id": doc.client_op_id,
		"region": doc.region,
		"zone": doc.zone,
		"woreda": doc.woreda,
		"kebele": doc.kebele,
	}


def _own_da_id(ctx: ScopeContext) -> str:
	if not ctx.da_ids:
		frappe.throw(_("Your account is not bound to a DA-ID."), frappe.PermissionError)
	return sorted(ctx.da_ids)[0]


def _creator_scope(
	ctx: ScopeContext,
	*,
	region: str | None = None,
	zone: str | None = None,
	woreda: str | None = None,
) -> tuple[str | None, str | None, str | None]:
	"""Stamp region/zone/woreda from the creator's RBAC grant (or explicit Admin target)."""
	woreda = (woreda or "").strip() or (
		next(iter(sorted(ctx.woreda_scopes)), None) if ctx.woreda_scopes else None
	)
	zone = (zone or "").strip() or (next(iter(sorted(ctx.zone_scopes)), None) if ctx.zone_scopes else None)
	region = (region or "").strip() or (
		next(iter(sorted(ctx.region_scopes)), None) if ctx.region_scopes else None
	)
	if not ctx.unrestricted:
		if woreda and ctx.woreda_scopes and woreda not in ctx.woreda_scopes:
			frappe.throw(_("Woreda {0} is outside your scope.").format(woreda), frappe.PermissionError)
		if not (woreda or zone or region):
			frappe.throw(
				_("Your account has no Region / Zone / Woreda scope to publish a survey."),
				frappe.PermissionError,
			)
	elif not woreda:
		frappe.throw(
			_("woreda is required when an Administrator publishes a survey."),
			frappe.ValidationError,
		)
	return region, zone, woreda


def _load_template(survey_id: str, ctx: ScopeContext, *, write: bool = False):
	if not frappe.db.exists(TEMPLATE_DOCTYPE, survey_id):
		frappe.throw(_("Survey {0} was not found.").format(survey_id), frappe.DoesNotExistError)
	doc = frappe.get_doc(TEMPLATE_DOCTYPE, survey_id)
	if ctx.unrestricted:
		return doc
	if not ctx.covers(doc.region, doc.woreda, doc.zone):
		frappe.throw(_("You do not have access to this survey."), frappe.PermissionError)
	if write and ctx.has_any_role(C.ROLE_EXECUTIVE) and not ctx.has_any_role(C.ROLE_SUPERVISOR, C.ROLE_ADMIN):
		frappe.throw(_("Executives cannot modify surveys."), frappe.PermissionError)
	return doc


def _est_minutes(param_count: int) -> int:
	return max(2, (param_count * 40 + 59) // 60)


def _next_template_version(survey_type: str, serial: int | None = None) -> str:
	code = TYPE_CODES[survey_type]
	if serial is None:
		serial = frappe.db.count(TEMPLATE_DOCTYPE, {"survey_type": survey_type}) + 1
	return f"{code}-T{serial}-v1.0"


def _bump_version(version: str) -> str:
	m = re.match(r"^(.*-v)(\d+)\.(\d+)$", version or "")
	if not m:
		return f"{version or 'v1'}.1"
	major, minor = int(m.group(2)), int(m.group(3))
	return f"{m.group(1)}{major}.{minor + 1}"


def _validate_template_input(
	*,
	name: str | None,
	survey_type: str | None,
	open_date,
	close_date,
	languages,
	target_farmers,
	questions,
) -> tuple:
	errors: list[str] = []
	title = (name or "").strip()
	if len(title) < 3:
		errors.append(_("Give the template a name."))
	if survey_type not in SURVEY_TYPES:
		errors.append(_("Survey type must be one of {0}.").format(", ".join(SURVEY_TYPES)))
	try:
		start, end = getdate(open_date), getdate(close_date)
	except Exception:
		errors.append(_("Set the collection window."))
		start = end = None
	else:
		if not open_date or not close_date:
			errors.append(_("Set the collection window."))
		elif end < start:
			errors.append(_("The window must close after it opens."))

	langs = languages if isinstance(languages, list) else []
	langs = [str(l).strip() for l in langs if str(l).strip()]
	unknown = [l for l in langs if l not in SURVEY_LANGUAGES]
	if unknown:
		errors.append(_("Unknown language: {0}.").format(", ".join(unknown)))
	if not langs:
		errors.append(_("Pick at least one language."))

	try:
		target = int(target_farmers)
	except TypeError, ValueError:
		target = 0
	if target < 1:
		errors.append(_("Set how many farmers each DA should survey."))

	qs = questions if isinstance(questions, list) else []
	if not qs:
		errors.append(_("Add at least one question."))
	clean_questions: list[dict] = []
	for i, q in enumerate(qs, start=1):
		if not isinstance(q, dict):
			errors.append(_("Question {0} is invalid.").format(i))
			continue
		prompt = (q.get("prompt") or "").strip()
		qtype = (q.get("type") or "").strip()
		if not prompt:
			errors.append(_("Question {0} needs text.").format(i))
		if qtype not in CUSTOM_QUESTION_TYPES:
			errors.append(_("Question {0} has an unsupported type.").format(i))
		options = _options_of(q.get("options"))
		if qtype in ("single", "multi") and len(options) < 2:
			errors.append(_("Question {0} needs at least two choices.").format(i))
		clean_questions.append(
			{
				"prompt": prompt,
				"type": qtype,
				"options": options,
				"required": bool(q.get("required", True)),
			}
		)

	if errors:
		frappe.throw(" ".join(str(e) for e in errors), frappe.ValidationError)
	return title, survey_type, start, end, langs, target, clean_questions


def _parameter_rows(questions: list[dict]) -> list[dict]:
	rows = [dict(p) for p in STANDARD_PARAMS]
	for i, q in enumerate(questions):
		order = len(STANDARD_PARAMS) + i + 1
		row = {
			"parameter_id": f"p{order}",
			"sort_order": order,
			"prompt": q["prompt"],
			"response_type": q["type"],
			"required": 1 if q["required"] else 0,
			"options_json": _json_dump(q["options"]) if q["options"] else None,
		}
		if q["type"] == "rating":
			row["helper"] = "1 = very dissatisfied · 5 = very satisfied"
		rows.append(row)
	return rows


# -- create / patch / list templates -------------------------------------------------


def create_template(
	*,
	name: str | None,
	type: str | None = None,
	survey_type: str | None = None,
	open: str | None = None,
	close: str | None = None,
	languages=None,
	targetFarmers=None,
	target_farmers=None,
	questions=None,
	region: str | None = None,
	zone: str | None = None,
	woreda: str | None = None,
	client_op_id: str | None = None,
	ctx: ScopeContext | None = None,
) -> dict:
	ctx = ctx or require_roles(*CREATE_ROLES)
	if not ctx.has_any_role(*CREATE_ROLES):
		frappe.throw(
			_("Only a Supervisor or Administrator can create survey templates."), frappe.PermissionError
		)

	client_op_id = (client_op_id or "").strip() or None
	if client_op_id:
		existing = frappe.db.get_value(
			TEMPLATE_DOCTYPE,
			{"client_op_id": client_op_id},
			["name", "created_by_user"],
			as_dict=True,
		)
		if existing:
			# A replay may only read back the row the same user created. Someone
			# else's operation id is a conflict, never a template disclosure.
			if existing.created_by_user != ctx.user:
				raise SurveyConflictError(
					_("client_op_id was already used by another user.")
				)
			return serialize_survey(frappe.get_doc(TEMPLATE_DOCTYPE, existing.name))

	survey_type = survey_type or type
	target = target_farmers if target_farmers is not None else targetFarmers
	title, survey_type, start, end, langs, target, clean_qs = _validate_template_input(
		name=name,
		survey_type=survey_type,
		open_date=open,
		close_date=close,
		languages=languages,
		target_farmers=target,
		questions=questions,
	)
	region, zone, woreda = _creator_scope(ctx, region=region, zone=zone, woreda=woreda)

	params = _parameter_rows(clean_qs)
	doc = frappe.get_doc(
		{
			"doctype": TEMPLATE_DOCTYPE,
			"title": title,
			"survey_type": survey_type,
			"template_version": _next_template_version(survey_type),
			"status": "Open",
			"window_open": start,
			"window_close": end,
			"languages_json": _json_dump(langs),
			"target_farmers": target,
			"est_minutes": _est_minutes(len(params)),
			"created_by_user": ctx.user,
			"region": region,
			"zone": zone,
			"woreda": woreda,
			"client_op_id": client_op_id,
			"parameters": params,
		}
	)
	doc.insert(ignore_permissions=True)
	return serialize_survey(doc)


def patch_template(survey_id: str, body: dict, ctx: ScopeContext | None = None) -> dict:
	ctx = ctx or require_roles(*CREATE_ROLES)
	doc = _load_template(survey_id, ctx, write=True)
	if not ctx.has_any_role(*CREATE_ROLES):
		frappe.throw(
			_("Only a Supervisor or Administrator can update survey templates."), frappe.PermissionError
		)

	if "status" in body and body["status"] is not None:
		status = str(body["status"]).strip()
		if status not in ("Open", "Closed"):
			frappe.throw(_("status must be Open or Closed."), frappe.ValidationError)
		doc.status = status

	open_date = body.get("open", body.get("window_open"))
	close_date = body.get("close", body.get("window_close"))
	if open_date is not None:
		doc.window_open = getdate(open_date)
	if close_date is not None:
		doc.window_close = getdate(close_date)
	if getdate(doc.window_close) < getdate(doc.window_open):
		frappe.throw(_("The window must close after it opens."), frappe.ValidationError)

	if "languages" in body and body["languages"] is not None:
		langs = [str(l).strip() for l in (body["languages"] or []) if str(l).strip()]
		if not langs:
			frappe.throw(_("Pick at least one language."), frappe.ValidationError)
		unknown = [l for l in langs if l not in SURVEY_LANGUAGES]
		if unknown:
			frappe.throw(_("Unknown language: {0}.").format(", ".join(unknown)), frappe.ValidationError)
		doc.languages_json = _json_dump(langs)

	target = body.get("targetFarmers", body.get("target_farmers"))
	if target is not None:
		try:
			target_n = int(target)
		except TypeError, ValueError:
			target_n = 0
		if target_n < 1:
			frappe.throw(_("Set how many farmers each DA should survey."), frappe.ValidationError)
		doc.target_farmers = target_n

	if "name" in body and body["name"] is not None:
		title = str(body["name"]).strip()
		if len(title) < 3:
			frappe.throw(_("Give the template a name."), frappe.ValidationError)
		doc.title = title

	if "questions" in body and body["questions"] is not None:
		counts = _response_counts(doc.name)
		if counts["collected"] > 0:
			frappe.response["http_status_code"] = 409
			frappe.throw(
				_("Questions cannot change after responses have been collected."),
				frappe.ValidationError,
			)
		_title, _stype, _s, _e, _langs, _t, clean_qs = _validate_template_input(
			name=doc.title,
			survey_type=doc.survey_type,
			open_date=doc.window_open,
			close_date=doc.window_close,
			languages=_json_load(doc.languages_json, []),
			target_farmers=doc.target_farmers,
			questions=body["questions"],
		)
		doc.set("parameters", [])
		for row in _parameter_rows(clean_qs):
			doc.append("parameters", row)
		doc.est_minutes = _est_minutes(len(doc.parameters))
		doc.template_version = _bump_version(doc.template_version)

	doc.save(ignore_permissions=True)
	return serialize_survey(doc)


def list_templates(
	ctx: ScopeContext | None = None,
	*,
	status: str | None = None,
	type: str | None = None,
	q: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int]:
	ctx = ctx or require_roles(*RESULTS_ROLES)
	limit = max(1, min(int(limit or 50), 200))
	start = max(0, int(start or 0))
	filters: dict = {}
	if type:
		filters["survey_type"] = type
	# Stored status is Open/Closed; UI status Closing soon is derived later.
	if status == "Closed":
		filters["status"] = "Closed"
	elif status in ("Open", "Closing soon"):
		filters["status"] = "Open"

	# Caller must set the session user so permission_query_conditions apply (same as leave).
	names = frappe.get_list(
		TEMPLATE_DOCTYPE,
		filters=filters,
		fields=["name"],
		order_by="modified desc",
		limit_page_length=limit,
		limit_start=start,
	)
	total = len(frappe.get_list(TEMPLATE_DOCTYPE, filters=filters, fields=["name"], limit_page_length=0))
	rows = []
	needle = (q or "").strip().lower()
	for n in names:
		doc = frappe.get_doc(TEMPLATE_DOCTYPE, n.name)
		item = serialize_survey(doc, include_parameters=False)
		if status and item["status"] != status:
			continue
		if (
			needle
			and needle
			not in " ".join(
				[
					item["name"],
					item["type"],
					item["templateVersion"],
					item["status"],
					" ".join(item["languages"]),
				]
			).lower()
		):
			continue
		rows.append(item)
	return rows, total


def get_template(survey_id: str, ctx: ScopeContext | None = None) -> dict:
	ctx = ctx or require_roles(*RESULTS_ROLES)
	doc = _load_template(survey_id, ctx)
	return serialize_survey(doc)


# -- assigned surveys (DA view) ------------------------------------------------------


def list_surveys(
	ctx: ScopeContext | None = None,
	*,
	status: str | None = None,
	type: str | None = None,
	language: str | None = None,
	q: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int]:
	ctx = ctx or require_roles(*LIST_ROLES)
	limit = max(1, min(int(limit or 50), 200))
	start = max(0, int(start or 0))
	filters: dict = {}
	if type:
		filters["survey_type"] = type

	collector = _own_da_id(ctx) if ctx.has_any_role(C.ROLE_DA) and not ctx.unrestricted else None

	# Caller must set the session user so permission_query_conditions apply.
	names = frappe.get_list(
		TEMPLATE_DOCTYPE,
		filters=filters,
		fields=["name"],
		order_by="modified desc",
		limit_page_length=0,
	)

	needle = (q or "").strip().lower()
	rows = []
	for n in names:
		doc = frappe.get_doc(TEMPLATE_DOCTYPE, n.name)
		item = serialize_survey(doc, include_parameters=False, collector_da_id=collector)
		if status and item["status"] != status:
			continue
		if language and language not in item["languages"]:
			continue
		if (
			needle
			and needle
			not in " ".join(
				[
					item["name"],
					item["type"],
					item["templateVersion"],
					item["status"],
					" ".join(item["languages"]),
					f"{item['collected']} of {item['targetFarmers']}",
				]
			).lower()
		):
			continue
		rows.append(item)
	total = len(rows)
	return rows[start : start + limit], total


def get_survey(survey_id: str, ctx: ScopeContext | None = None) -> dict:
	ctx = ctx or require_roles(*LIST_ROLES)
	doc = _load_template(survey_id, ctx)
	collector = _own_da_id(ctx) if ctx.has_any_role(C.ROLE_DA) and not ctx.unrestricted else None
	return serialize_survey(doc, include_parameters=True, collector_da_id=collector)


# -- responses -----------------------------------------------------------------------


def _visible_params(parameters, answers: dict) -> list:
	visible = []
	for p in parameters:
		cond_id = p.condition_parameter_id
		if cond_id:
			if answers.get(cond_id) != p.condition_equals:
				continue
		visible.append(p)
	return visible


def _validate_answers(parameters, answers: dict, consent: str | None, sync_state: str) -> None:
	known = {p.parameter_id for p in parameters}
	unknown = sorted(key for key in answers if key not in known)
	if unknown:
		frappe.throw(
			_("Unknown answer parameter(s): {0}.").format(", ".join(unknown)),
			frappe.ValidationError,
		)
	if sync_state == "Draft":
		return
	if consent == "declined":
		return
	visible = _visible_params(parameters, answers)
	for p in visible:
		if p.response_type in ("consent", "auto"):
			continue
		val = answers.get(p.parameter_id)
		if p.required and (val is None or val == "" or val == []):
			frappe.throw(_("{0} is required.").format(p.prompt), frappe.ValidationError)
		if val is None or val == "":
			continue
		if p.response_type == "rating":
			if str(val) not in RATING_VALUES:
				frappe.throw(_("Rating must be 1 to 5."), frappe.ValidationError)
		elif p.response_type == "text":
			if not isinstance(val, str):
				frappe.throw(_("Text answer must be a string."), frappe.ValidationError)
			if len(val) > MAX_TEXT:
				frappe.throw(
					_("Text answer cannot exceed {0} characters.").format(MAX_TEXT), frappe.ValidationError
				)
		elif p.response_type in CHOICE_TYPES:
			options = _options_of(p.options_json)
			if p.response_type == "multi":
				if not isinstance(val, list):
					frappe.throw(_("Multi answers must be a list."), frappe.ValidationError)
				bad = [v for v in val if v not in options]
				if bad:
					frappe.throw(_("{0} is not an allowed value.").format(bad[0]), frappe.ValidationError)
			else:
				if val not in options:
					frappe.throw(_("{0} is not an allowed value.").format(val), frappe.ValidationError)


def submit_response(
	survey_id: str,
	*,
	farmer_id: str | None,
	consent: str | None = None,
	answers=None,
	started_at=None,
	completed_at=None,
	sync_state: str | None = None,
	client_op_id: str | None = None,
	da_id: str | None = None,
	ctx: ScopeContext | None = None,
) -> dict:
	ctx = ctx or require_roles(*CAPTURE_ROLES)
	doc_survey = _load_template(survey_id, ctx)
	ui_status = derive_ui_status(doc_survey.status, doc_survey.window_close)
	if ui_status == "Closed":
		frappe.throw(_("This survey window is closed."), frappe.ValidationError)

	farmer_input = (farmer_id or "").strip()
	if not farmer_input:
		frappe.throw(_("farmer_id is required."), frappe.ValidationError)

	# Collector DA: DA uses own id; Supervisor may collect on behalf of a DA in scope.
	if (
		ctx.has_any_role(C.ROLE_DA)
		and not ctx.unrestricted
		and not (ctx.has_any_role(C.ROLE_SUPERVISOR) and da_id)
	):
		collector = _own_da_id(ctx)
	elif da_id:
		collector = da_id.strip()
	elif ctx.has_any_role(C.ROLE_DA):
		collector = _own_da_id(ctx)
	else:
		frappe.throw(_("da_id is required when a Supervisor collects a response."), frappe.ValidationError)

	# The caller-supplied ID is never trusted: resolve it, prove the farmer is in
	# the collector's assigned Kebele and inside the survey's Woreda, then keep the
	# canonical id and the farmer's own location for the response row.
	ref = farmer_svc.resolve_farmer_for_capture(
		farmer_input,
		ctx,
		collector_da_id=collector,
		survey_woreda=doc_survey.woreda,
	)
	canonical_farmer_id = ref["farmer_id"]

	consent = (consent or "").strip() or None
	if consent and consent not in CONSENTS:
		frappe.throw(_("consent must be granted or declined."), frappe.ValidationError)

	state = (sync_state or "").strip() or ("Declined" if consent == "declined" else "Synced")
	if state not in SYNC_STATES:
		frappe.throw(
			_("sync_state must be one of {0}.").format(", ".join(SYNC_STATES)), frappe.ValidationError
		)
	if consent == "declined":
		state = "Declined"
	elif state != "Draft" and consent != "granted":
		frappe.throw(_("consent must be granted to submit a response."), frappe.ValidationError)

	answers = dict(answers) if isinstance(answers, dict) else {}
	# The auto answers must agree with the resolved farmer, whatever the client sent.
	answers["p2"] = ref.get("reg_id") or canonical_farmer_id
	answers["p3"] = ref.get("kebele")
	_validate_answers(doc_survey.parameters, answers, consent, state)

	# Idempotent replay is scoped to the same survey *and* collector. A client_op_id
	# owned by another survey / DA is a conflict and never returns its row.
	client_op_id = (client_op_id or "").strip() or None
	if client_op_id:
		existing = frappe.db.get_value(
			RESPONSE_DOCTYPE,
			{"client_op_id": client_op_id, "survey": survey_id, "da_id": collector},
			["name", "farmer_id", "consent", "answers_json", "sync_state"],
			as_dict=True,
		)
		if existing:
			same_payload = (
				existing.farmer_id == canonical_farmer_id
				and (existing.consent or None) == consent
				and (existing.sync_state or None) == state
				and (_json_load(existing.answers_json, {}) or {}) == answers
			)
			if not same_payload:
				raise SurveyConflictError(
					_("client_op_id was already used with a different response.")
				)
			return serialize_response(frappe.get_doc(RESPONSE_DOCTYPE, existing.name))
		if frappe.db.exists(RESPONSE_DOCTYPE, {"client_op_id": client_op_id}):
			raise SurveyConflictError(
				_("client_op_id was already used for another response.")
			)

	# Duplicate guard for completed non-declined responses. Drafts and declined
	# responses keep the existing behaviour until the business decision is confirmed.
	if state in ("Synced", "Queued") and consent != "declined":
		guard = f"{survey_id}|{canonical_farmer_id}"
		if frappe.db.exists(RESPONSE_DOCTYPE, {"duplicate_guard_key": guard}):
			raise SurveyConflictError(
				_("A response for this farmer already exists for this survey.")
			)
	else:
		guard = None

	started = get_datetime(started_at) if started_at else now_datetime()
	completed = get_datetime(completed_at) if completed_at else (None if state == "Draft" else now_datetime())

	row = frappe.get_doc(
		{
			"doctype": RESPONSE_DOCTYPE,
			"survey": survey_id,
			"da_id": collector,
			"farmer_id": canonical_farmer_id,
			"consent": consent,
			"answers_json": _json_dump(answers),
			"sync_state": state,
			"started_at": started,
			"completed_at": completed,
			"client_op_id": client_op_id,
			"duplicate_guard_key": guard,
			# Always the farmer's authoritative location, never the collector DA's.
			"region": ref.get("region"),
			"zone": ref.get("zone"),
			"woreda": ref.get("woreda"),
			"kebele": ref.get("kebele"),
		}
	)
	try:
		row.insert(ignore_permissions=True)
	except frappe.UniqueValidationError, frappe.DuplicateEntryError:
		raise SurveyConflictError(
			_("A response for this farmer already exists for this survey.")
		) from None
	return serialize_response(row)


def list_responses(
	survey_id: str,
	ctx: ScopeContext | None = None,
	*,
	farmer_id: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int]:
	ctx = ctx or require_roles(*LIST_ROLES)
	_load_template(survey_id, ctx)
	limit = max(1, min(int(limit or 50), 200))
	start = max(0, int(start or 0))
	filters: dict = {"survey": survey_id}
	if farmer_id:
		filters["farmer_id"] = farmer_id
	if (
		ctx.has_any_role(C.ROLE_DA)
		and not ctx.unrestricted
		and not ctx.has_any_role(C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)
	):
		filters["da_id"] = ("in", list(ctx.da_ids))

	names = frappe.get_list(
		RESPONSE_DOCTYPE,
		filters=filters,
		fields=["name"],
		order_by="modified desc",
		limit_page_length=limit,
		limit_start=start,
	)
	total = len(frappe.get_list(RESPONSE_DOCTYPE, filters=filters, fields=["name"], limit_page_length=0))

	rows = [serialize_response(frappe.get_doc(RESPONSE_DOCTYPE, n.name)) for n in names]
	return rows, total


def survey_results(survey_id: str, ctx: ScopeContext | None = None) -> dict:
	ctx = ctx or require_roles(*RESULTS_ROLES)
	doc = _load_template(survey_id, ctx)
	rows = frappe.get_all(
		RESPONSE_DOCTYPE,
		filters={"survey": survey_id},
		fields=["farmer_id", "consent", "sync_state", "answers_json"],
	)
	by_status: dict[str, int] = {}
	for r in rows:
		by_status[r.sync_state] = by_status.get(r.sync_state, 0) + 1

	param_summaries = []
	for p in doc.parameters:
		if p.response_type in ("consent", "auto"):
			continue
		summary: dict = {"response_count": 0}
		values = []
		for r in rows:
			if r.sync_state not in ("Synced", "Queued") or r.consent == "declined":
				continue
			answers = _json_load(r.answers_json, {}) or {}
			if p.parameter_id not in answers:
				continue
			val = answers[p.parameter_id]
			summary["response_count"] += 1
			values.append(val)
		if p.response_type == "rating" and values:
			nums = [int(v) for v in values if str(v) in RATING_VALUES]
			summary["average"] = round(sum(nums) / len(nums), 2) if nums else None
			summary["counts"] = {str(i): nums.count(i) for i in range(1, 6)}
		elif p.response_type in CHOICE_TYPES:
			counts: dict[str, int] = {}
			for val in values:
				items = val if isinstance(val, list) else [val]
				for item in items:
					key = str(item)
					counts[key] = counts.get(key, 0) + 1
			summary["counts"] = counts
		param_summaries.append(
			{
				"id": p.parameter_id,
				"prompt": p.prompt,
				"type": p.response_type,
				"summary": summary,
			}
		)

	counts = _response_counts(survey_id)
	return {
		"survey_id": doc.name,
		"name": doc.title,
		"target_farmers": int(doc.target_farmers or 0),
		"collected": counts["collected"],
		"queued": counts["queued"],
		"by_status": by_status,
		"parameters": param_summaries,
	}


# -- Excel export (Supervisor / Executive / Admin) -----------------------------------


def _cell_text(value) -> str:
	"""One spreadsheet cell: lists become comma-separated, richer values JSON."""
	if value is None:
		return ""
	if isinstance(value, (list, tuple)):
		return ", ".join(_cell_text(v) for v in value)
	if isinstance(value, dict):
		return json.dumps(value, ensure_ascii=False)
	return str(value)


def _cell_datetime(value) -> str:
	if not value:
		return ""
	if hasattr(value, "strftime"):
		return value.strftime("%Y-%m-%d %H:%M:%S")
	return str(value)


def build_export_rows(parameters: list[dict], responses: list[dict]) -> list[list]:
	"""Header + one row per response. Pure, so the workbook layout is unit-testable."""
	headers = list(EXPORT_BASE_COLUMNS) + [
		f"{p['id']}: {p['prompt']}" for p in parameters
	]
	rows: list[list] = [headers]
	for response in responses:
		answers = response.get("answers") or {}
		row = [
			response.get("name") or "",
			response.get("survey") or "",
			response.get("template_version") or "",
			response.get("farmer_id") or "",
			response.get("farmer_reg_id") or "",
			response.get("da_id") or "",
			response.get("consent") or "",
			response.get("sync_state") or "",
			_cell_datetime(response.get("started_at")),
			_cell_datetime(response.get("completed_at")),
			response.get("region") or "",
			response.get("zone") or "",
			response.get("woreda") or "",
			response.get("kebele") or "",
		]
		row.extend(_cell_text(answers.get(p["id"])) for p in parameters)
		rows.append(row)
	return rows


def build_export_xlsx(
	parameters: list[dict],
	responses: list[dict],
	*,
	sheet_name: str = EXPORT_SHEET,
) -> bytes:
	"""Build an XLSX workbook for the given parameters and response dicts."""
	from frappe.utils.xlsxutils import make_xlsx

	data = build_export_rows(parameters, responses)
	workbook = make_xlsx(data, sheet_name)
	return workbook.getvalue()


def export_responses(survey_id: str, ctx: ScopeContext | None = None) -> tuple[str, bytes]:
	"""Return ``(filename, xlsx_bytes)`` for every response stored against a survey.

	Result roles only. ``_load_template`` applies the same scope check as results.
	All stored response rows are exported exactly as recorded; filtering declined or
	draft rows is a pending business decision and is deliberately not applied.
	"""
	ctx = ctx or require_roles(*RESULTS_ROLES)
	if not ctx.has_any_role(*RESULTS_ROLES):
		frappe.throw(
			_("Only a Supervisor, Executive or Administrator can export survey responses."),
			frappe.PermissionError,
		)
	doc = _load_template(survey_id, ctx)

	responses = frappe.get_all(
		RESPONSE_DOCTYPE,
		filters={"survey": survey_id},
		fields=[
			"name",
			"survey",
			"da_id",
			"farmer_id",
			"consent",
			"sync_state",
			"started_at",
			"completed_at",
			"answers_json",
			"region",
			"zone",
			"woreda",
			"kebele",
		],
		order_by="creation asc",
		limit_page_length=0,
	)

	farmer_ids = sorted({row.farmer_id for row in responses if row.farmer_id})
	reg_ids: dict[str, str | None] = {}
	if farmer_ids:
		for ref in frappe.get_all(
			"DA Farmer Reference",
			filters={"name": ("in", farmer_ids)},
			fields=["name", "reg_id"],
		):
			reg_ids[ref.name] = ref.reg_id

	parameters = [{"id": p.parameter_id, "prompt": p.prompt} for p in doc.parameters]
	rows = [
		{
			"name": row.name,
			"survey": row.survey,
			"template_version": doc.template_version,
			"farmer_id": row.farmer_id,
			"farmer_reg_id": reg_ids.get(row.farmer_id),
			"da_id": row.da_id,
			"consent": row.consent,
			"sync_state": row.sync_state,
			"started_at": row.started_at,
			"completed_at": row.completed_at,
			"region": row.region,
			"zone": row.zone,
			"woreda": row.woreda,
			"kebele": row.kebele,
			"answers": _json_load(row.answers_json, {}) or {},
		}
		for row in responses
	]

	return f"survey-{doc.name}-responses.xlsx", build_export_xlsx(parameters, rows)
