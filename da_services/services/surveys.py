"""Survey templates and responses (FR-09d / docs/api-da-screens.md §2.16).

A Supervisor/Admin publishes a template into a Woreda; DAs in that Woreda list it as an
assigned survey and save one response per farmer. Executive reads results.

Thin by design: each function does only its own job (save, read, aggregate, export).
Who may read or write which row comes from Role DocPerm and the user's User Permissions
(DA Region / Zone / Woreda / Kebele); field rules come from the DocType meta and controllers.
"""

from __future__ import annotations

import io
import json
import re
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate, today

from da_services.da_services.doctype.da_administrative_area.da_administrative_area import ancestors_of
from da_services.services import constants as C

TEMPLATE_DOCTYPE = "DA Survey Template"
RESPONSE_DOCTYPE = "DA Survey Response"
ANSWER_DOCTYPE = "DA Survey Answer"

ASSIGNMENT_RULE_PREFIX = "survey.assignment:"
TYPE_CODES = {"Satisfaction": "FSS", "Service quality": "SQ", "Needs assessment": "NA"}
CHOICE_TYPES = frozenset({"single", "multi", "lookup"})
CLOSING_SOON_DAYS = 7
RATING_VALUES = frozenset({"1", "2", "3", "4", "5"})

# A choice question keeps its choices in option_1 .. option_4, and a translated prompt
# in one column per language.
OPTION_FIELDS = tuple(f"option_{i}" for i in range(1, 5))
TRANSLATION_FIELDS = {"Amharic": "prompt_amharic", "Afaan Oromoo": "prompt_afaan_oromoo"}

# XLSX export layout.
EXPORT_SHEET = "Survey Responses"
EXPORT_ANSWERS_SHEET = "Questions and Answers"
EXPORT_ANSWER_COLUMNS = (
	"Response ID",
	"Farmer ID",
	"Collector DA ID",
	"Woreda",
	"Kebele",
	"Question No.",
	"Question",
	"Answer",
)
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

# Consent, Farmer ID and Kebele open every survey.
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


# -- small helpers --------------------------------------------------------------------


def format_survey_date(value) -> str:
	"""Frontend display form: '08 Sep 2026'."""
	return getdate(value).strftime("%d %b %Y")


def derive_ui_status(status: str, window_close) -> str:
	if status == "Closed":
		return "Closed"
	close = getdate(window_close)
	if close < getdate(today()):
		return "Closed"
	if close <= getdate(today()) + timedelta(days=CLOSING_SOON_DAYS):
		return "Closing soon"
	return "Open"


def _languages_of(text) -> list[str]:
	"""Survey languages from the template's `language` text ("English, Amharic")."""
	return [part.strip() for part in str(text or "").split(",") if part.strip()]


def _options_of(raw) -> list[str]:
	"""Choices as a list; the create form may send a list or a comma-separated string."""
	if isinstance(raw, list):
		return [str(o).strip() for o in raw if str(o).strip()]
	return [o.strip() for o in str(raw or "").split(",") if o.strip()]


def _est_minutes(param_count: int) -> int:
	return max(2, (param_count * 40 + 59) // 60)


def _next_template_version(survey_type: str) -> str:
	serial = frappe.db.count(TEMPLATE_DOCTYPE, {"survey_type": survey_type}) + 1
	return f"{TYPE_CODES.get(survey_type, 'SRV')}-T{serial}-v1.0"


def _bump_version(version: str) -> str:
	m = re.match(r"^(.*-v)(\d+)\.(\d+)$", version or "")
	if not m:
		return f"{version or 'v1'}.1"
	return f"{m.group(1)}{m.group(2)}.{int(m.group(3)) + 1}"


# -- serialisation ----------------------------------------------------------------------


def serialize_parameter(row) -> dict:
	out = {
		"id": row.parameter_id,
		"order": int(row.sort_order or 0),
		"prompt": row.prompt,
		"type": row.response_type,
		"required": bool(row.required),
	}
	options = [str(row.get(f)).strip() for f in OPTION_FIELDS if str(row.get(f) or "").strip()]
	if options:
		out["options"] = options
	if row.helper:
		out["helper"] = row.helper
	if row.prefill:
		out["prefill"] = row.prefill
	translations = {lang: row.get(f) for lang, f in TRANSLATION_FIELDS.items() if row.get(f)}
	if translations:
		out["translations"] = translations
	if row.condition_parameter_id and row.condition_equals is not None:
		out["condition"] = {"parameterId": row.condition_parameter_id, "equals": row.condition_equals}
	return out


def _response_counts(survey_id: str, collector_da_id: str | None = None) -> dict:
	filters: dict = {"survey": survey_id}
	if collector_da_id:
		filters["da_id"] = collector_da_id
	rows = frappe.get_all(RESPONSE_DOCTYPE, filters=filters, fields=["farmer_id", "sync_state", "consent"])
	done = [r for r in rows if r.sync_state in ("Synced", "Queued") and r.consent != "declined"]
	return {
		"collected": len(done),
		"queued": sum(1 for r in rows if r.sync_state == "Queued"),
		"answered": list(dict.fromkeys(r.farmer_id for r in done if r.farmer_id)),
	}


def serialize_survey(doc, *, include_parameters: bool = True, collector_da_id: str | None = None) -> dict:
	counts = _response_counts(doc.name, collector_da_id)
	assigned_users = _assigned_users(doc.name)
	assigned_da_ids = _assigned_da_ids(doc.name)
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
		"languages": _languages_of(doc.language),
		"targetFarmers": int(doc.target_farmers or 0),
		"collected": counts["collected"],
		"queued": counts["queued"],
		"status": derive_ui_status(doc.status, doc.window_close),
		"estMinutes": int(doc.est_minutes or 0),
		"answered": counts["answered"],
		"region": doc.region,
		"zone": doc.zone,
		"woreda": doc.woreda,
		"created_by_user": doc.created_by_user,
		"assignmentMode": "Selected" if assigned_users else "Woreda",
	}
	if collector_da_id is None:
		out["assignedUsers"] = assigned_users
		out["assignedDaIds"] = assigned_da_ids
	if include_parameters:
		out["parameters"] = [serialize_parameter(r) for r in doc.parameters]
	return out


def _answers_of(rows) -> dict:
	"""Answer rows back to the API shape: {parameter_id: value}, a list for multi."""
	out: dict = {}
	for r in rows:
		if r.response_type == "multi":
			out.setdefault(r.parameter_id, []).append(r.answer_text)
		else:
			out[r.parameter_id] = r.answer_text
	return out


def _answers_by_response(names: list[str]) -> dict[str, dict]:
	"""Answers of many responses in one query, keyed by response name."""
	if not names:
		return {}
	grouped: dict[str, list] = {}
	for r in frappe.get_all(
		ANSWER_DOCTYPE,
		filters={"parent": ("in", names), "parenttype": RESPONSE_DOCTYPE},
		fields=["parent", "parameter_id", "response_type", "answer_text"],
		order_by="parent asc, idx asc",
		limit_page_length=0,
	):
		grouped.setdefault(r.parent, []).append(r)
	return {name: _answers_of(grouped.get(name, [])) for name in names}


def serialize_response(doc) -> dict:
	return {
		"id": doc.name,
		"survey_id": doc.survey,
		"template_version": doc.template_version or None,
		"collector_da_id": doc.da_id,
		"farmer_id": doc.farmer_id,
		"consent": doc.consent or None,
		"answers": _answers_of(doc.answers),
		"sync_state": doc.sync_state,
		"started_at": doc.started_at.isoformat() if doc.started_at else None,
		"completed_at": doc.completed_at.isoformat() if doc.completed_at else None,
		"client_op_id": doc.client_op_id,
		"region": doc.region,
		"zone": doc.zone,
		"woreda": doc.woreda,
		"kebele": doc.kebele,
	}


# -- DA assignment (Selected mode): one DA Task per assigned DA ----------------------------


def _assignment_rule(survey_id: str) -> str:
	return f"{ASSIGNMENT_RULE_PREFIX}{survey_id}"


def _assigned_users(survey_id: str) -> list[str]:
	rows = frappe.get_all(
		"DA Task",
		filters={"generated_by_rule": _assignment_rule(survey_id), "status": ("!=", "Cancelled")},
		pluck="user",
	)
	return sorted({u for u in rows if u})


def _assigned_da_ids(survey_id: str) -> list[str]:
	rows = frappe.get_all(
		"DA Task",
		filters={"generated_by_rule": _assignment_rule(survey_id), "status": ("!=", "Cancelled")},
		pluck="da_id",
	)
	return sorted({d for d in rows if d})


def _sync_assignments(doc, users) -> None:
	"""Open a DA Task for each newly assigned User and cancel the ones taken off.

	``users`` must come from the FE (User Link emails). ``da_id`` is optional on the
	marker Task and is filled by DocType ``fetch_from: user.da_registry_id`` when set.
	"""
	wanted = {str(u).strip() for u in (users or []) if str(u).strip()}
	rule = _assignment_rule(doc.name)
	for task in frappe.get_all(
		"DA Task",
		filters={"generated_by_rule": rule, "status": ("!=", "Cancelled")},
		fields=["name", "user"],
	):
		if task.user in wanted:
			wanted.discard(task.user)
			continue
		marker = frappe.get_doc("DA Task", task.name)
		marker.flags.ignore_permissions = True
		marker.cancel_task("Survey assignment removed.")
	for user in sorted(wanted):
		# A system marker that follows the template write the caller was allowed to make.
		frappe.get_doc(
			{
				"doctype": "DA Task",
				"user": user,
				"task_type": "Survey due",
				"title": f"Complete survey: {doc.title}",
				"description": f"Survey {doc.name} is assigned to you.",
				"due_at": get_datetime(f"{getdate(doc.window_close)} 23:59:59"),
				"priority": "Normal",
				"status": "Open",
				"generated_by_rule": rule,
			}
		).insert(ignore_permissions=True)


# -- templates ---------------------------------------------------------------------------


def _parameter_rows(questions) -> list[dict]:
	"""The standard p1-p3 rows, then one row per question the Supervisor wrote."""
	rows = [dict(p) for p in STANDARD_PARAMS]
	for i, q in enumerate(questions or [], start=len(STANDARD_PARAMS) + 1):
		qtype = (q.get("type") or "").strip() or None
		row = {
			"parameter_id": f"p{i}",
			"sort_order": i,
			"prompt": (q.get("prompt") or "").strip() or None,
			"response_type": qtype,
			"required": 1 if q.get("required", True) else 0,
			**dict(zip(OPTION_FIELDS, _options_of(q.get("options")), strict=False)),
			**{
				TRANSLATION_FIELDS[lang]: text
				for lang, text in (q.get("translations") or {}).items()
				if lang in TRANSLATION_FIELDS and str(text or "").strip()
			},
		}
		if qtype == "rating":
			row["helper"] = "1 = very dissatisfied · 5 = very satisfied"
		rows.append(row)
	return rows


def create_template(
	*,
	name=None,
	type=None,
	open=None,
	close=None,
	languages=None,
	targetFarmers=None,
	questions=None,
	woreda=None,
	assignmentMode=None,
	assignedUsers=None,
	client_op_id=None,
) -> str:
	"""Insert one template (create = publish). Returns its id."""
	woreda = (woreda or "").strip() or None
	area = ancestors_of(woreda) if woreda else {}
	params = _parameter_rows(questions)
	doc = frappe.get_doc(
		{
			"doctype": TEMPLATE_DOCTYPE,
			"title": (name or "").strip() or None,
			"survey_type": (type or "").strip() or None,
			"template_version": _next_template_version((type or "").strip()),
			"status": "Open",
			"window_open": open or None,
			"window_close": close or None,
			"language": ", ".join(languages or []),
			"target_farmers": targetFarmers,
			"est_minutes": _est_minutes(len(params)),
			"created_by_user": frappe.session.user,
			"region": area.get("region"),
			"zone": area.get("zone"),
			"woreda": woreda,
			"client_op_id": (client_op_id or "").strip() or None,
			"parameters": params,
		}
	).insert()
	if assignmentMode == "Selected":
		users = [str(u).strip() for u in (assignedUsers or []) if str(u).strip()]
		if not users:
			frappe.throw(
				_("assignedUsers is required when assignmentMode is Selected."), frappe.ValidationError
			)
		_sync_assignments(doc, users)
	return doc.name


def patch_template(survey_id: str, body: dict) -> str:
	"""Set the fields sent and save. Returns the id saved to: a new version when the
	questions change after responses were collected, else the same template."""
	doc = frappe.get_doc(TEMPLATE_DOCTYPE, survey_id)
	fields = {
		"name": "title",
		"status": "status",
		"open": "window_open",
		"close": "window_close",
		"targetFarmers": "target_farmers",
	}
	for key, field in fields.items():
		if body.get(key) is not None:
			doc.set(field, body[key])
	if body.get("languages") is not None:
		doc.language = ", ".join(body["languages"])

	target = doc
	if body.get("questions") is not None:
		params = _parameter_rows(body["questions"])
		if _response_counts(doc.name)["collected"]:
			# Answered questions stay as they were: the edit goes to a new template version.
			doc.save()
			target = frappe.copy_doc(doc)
			target.client_op_id = None
		target.set("parameters", params)
		target.est_minutes = _est_minutes(len(params))
		target.template_version = _bump_version(doc.template_version)
	if target.is_new():
		target.insert()
	else:
		target.save()

	if body.get("assignmentMode") == "Woreda":
		_sync_assignments(doc, [])
	elif body.get("assignedUsers") is not None:
		users = [str(u).strip() for u in (body.get("assignedUsers") or []) if str(u).strip()]
		if body.get("assignmentMode") == "Selected" and not users:
			frappe.throw(
				_("assignedUsers is required when assignmentMode is Selected."), frappe.ValidationError
			)
		_sync_assignments(doc, users)
	return target.name


def _matches(item: dict, *, status=None, language=None, q=None) -> bool:
	if status and item["status"] != status:
		return False
	if language and language not in item["languages"]:
		return False
	needle = (q or "").strip().lower()
	text = " ".join([item["name"], item["type"], item["templateVersion"], item["status"], *item["languages"]])
	return not needle or needle in text.lower()


def list_templates(
	*, status=None, type=None, woreda=None, q=None, limit=50, start=0
) -> tuple[list[dict], int]:
	"""Templates the caller may read (permission query), newest change first."""
	filters = {k: v for k, v in (("survey_type", type), ("woreda", woreda)) if v}
	names = frappe.get_list(TEMPLATE_DOCTYPE, filters=filters, pluck="name", order_by="modified desc")
	rows = [
		item
		for item in (
			serialize_survey(frappe.get_doc(TEMPLATE_DOCTYPE, n), include_parameters=False) for n in names
		)
		if _matches(item, status=status, q=q)
	]
	return rows[start : start + limit], len(rows)


def get_template(survey_id: str) -> dict:
	doc = frappe.get_doc(TEMPLATE_DOCTYPE, survey_id)
	doc.check_permission("read")
	return serialize_survey(doc)


# -- assigned surveys (DA view) ------------------------------------------------------------


def list_surveys(
	*, status=None, type=None, language=None, woreda=None, q=None, limit=50, start=0
) -> tuple[list[dict], int]:
	"""Surveys the caller may read."""
	filters = {k: v for k, v in (("survey_type", type), ("woreda", woreda)) if v}
	names = frappe.get_list(TEMPLATE_DOCTYPE, filters=filters, pluck="name", order_by="modified desc")
	rows = []
	for n in names:
		item = serialize_survey(frappe.get_doc(TEMPLATE_DOCTYPE, n), include_parameters=False)
		if _matches(item, status=status, language=language, q=q):
			rows.append(item)
	return rows[start : start + limit], len(rows)


def get_survey(survey_id: str) -> dict:
	doc = frappe.get_doc(TEMPLATE_DOCTYPE, survey_id)
	doc.check_permission("read")
	return serialize_survey(doc)


# -- responses -------------------------------------------------------------------------------


def _answer_rows(parameters, answers: dict) -> list[dict]:
	"""One answer row per value, in question order; a multi answer gives one row per choice."""
	rows: list[dict] = []
	for p in sorted(parameters, key=lambda p: int(p.sort_order or 0)):
		value = answers.get(p.parameter_id)
		for item in value if isinstance(value, list) else [value]:
			text = str(item).strip() if item is not None else ""
			if not text:
				continue
			rows.append(
				{
					"parameter_id": p.parameter_id,
					"question": p.prompt,
					"response_type": p.response_type,
					"answer_text": text,
					"answer_rating": int(text)
					if p.response_type == "rating" and text in RATING_VALUES
					else None,
				}
			)
	return rows


def create_response(
	survey_id: str,
	*,
	farmer_id=None,
	consent=None,
	answers=None,
	started_at=None,
	completed_at=None,
	sync_state=None,
	client_op_id=None,
	da_id=None,
	kebele=None,
) -> None:
	"""Insert one response for the survey, collected by ``da_id``."""
	survey = frappe.get_doc(TEMPLATE_DOCTYPE, survey_id)
	frappe.get_doc(
		{
			"doctype": RESPONSE_DOCTYPE,
			"survey": survey_id,
			"template_version": survey.template_version,
			"da_id": (da_id or "").strip() or None,
			"farmer_id": (farmer_id or "").strip() or None,
			"consent": (consent or "").strip() or None,
			"sync_state": (sync_state or "").strip() or "Synced",
			"started_at": started_at or None,
			"completed_at": completed_at or None,
			"client_op_id": (client_op_id or "").strip() or None,
			"answers": _answer_rows(survey.parameters, answers if isinstance(answers, dict) else {}),
			"region": survey.region,
			"zone": survey.zone,
			"woreda": survey.woreda,
			"kebele": (kebele or "").strip() or None,
		}
	).insert()


def list_responses(
	survey_id: str,
	*,
	farmer_id=None,
	da_id=None,
	region=None,
	zone=None,
	woreda=None,
	kebele=None,
	limit=50,
	start=0,
) -> tuple[list[dict], int]:
	"""Responses the caller may read (a DA: the ones they collected), newest first."""
	filters = {"survey": survey_id}
	for field, value in (
		("farmer_id", farmer_id),
		("da_id", da_id),
		("region", region),
		("zone", zone),
		("woreda", woreda),
		("kebele", kebele),
	):
		if value:
			filters[field] = value
	names = frappe.get_list(RESPONSE_DOCTYPE, filters=filters, pluck="name", order_by="modified desc")
	page = names[start : start + limit]
	return [serialize_response(frappe.get_doc(RESPONSE_DOCTYPE, n)) for n in page], len(names)


def _readable_responses(survey_id: str, fields: list[str]) -> list:
	return frappe.get_list(
		RESPONSE_DOCTYPE,
		filters={"survey": survey_id},
		fields=fields,
		order_by="creation asc",
		limit_page_length=0,
	)


def survey_results(survey_id: str) -> dict:
	"""Per-question aggregates over the responses the caller may read."""
	doc = frappe.get_doc(TEMPLATE_DOCTYPE, survey_id)
	doc.check_permission("read")
	rows = _readable_responses(survey_id, ["name", "consent", "sync_state"])
	answers_of = _answers_by_response([r.name for r in rows])
	done = [r for r in rows if r.sync_state in ("Synced", "Queued") and r.consent != "declined"]

	by_status: dict[str, int] = {}
	for r in rows:
		by_status[r.sync_state] = by_status.get(r.sync_state, 0) + 1

	parameters = []
	for p in doc.parameters:
		if p.response_type in ("consent", "auto"):
			continue
		values = [answers_of[r.name][p.parameter_id] for r in done if p.parameter_id in answers_of[r.name]]
		summary: dict = {"response_count": len(values)}
		if p.response_type == "rating" and values:
			nums = [int(v) for v in values if str(v) in RATING_VALUES]
			summary["average"] = round(sum(nums) / len(nums), 2) if nums else None
			summary["counts"] = {str(i): nums.count(i) for i in range(1, 6)}
		elif p.response_type in CHOICE_TYPES:
			counts: dict[str, int] = {}
			for val in values:
				for item in val if isinstance(val, list) else [val]:
					counts[str(item)] = counts.get(str(item), 0) + 1
			summary["counts"] = counts
		parameters.append(
			{"id": p.parameter_id, "prompt": p.prompt, "type": p.response_type, "summary": summary}
		)

	return {
		"survey_id": doc.name,
		"name": doc.title,
		"target_farmers": int(doc.target_farmers or 0),
		"collected": len(done),
		"queued": by_status.get("Queued", 0),
		"by_status": by_status,
		"parameters": parameters,
	}


# -- Excel export ------------------------------------------------------------------------------


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
	return value.strftime("%Y-%m-%d %H:%M:%S") if hasattr(value, "strftime") else str(value)


def build_export_rows(parameters: list[dict], responses: list[dict]) -> list[list]:
	"""Header + one row per response. Pure, so the workbook layout is unit-testable."""
	rows: list[list] = [list(EXPORT_BASE_COLUMNS) + [f"{p['id']}: {p['prompt']}" for p in parameters]]
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


def build_export_answer_rows(parameters: list[dict], responses: list[dict]) -> list[list]:
	"""Header + one row per written question per response: the question beside its answer."""
	questions = [p for p in parameters if p.get("type") not in ("consent", "auto")]
	rows: list[list] = [list(EXPORT_ANSWER_COLUMNS)]
	for response in responses:
		answers = response.get("answers") or {}
		for number, p in enumerate(questions, start=1):
			rows.append(
				[
					response.get("name") or "",
					response.get("farmer_reg_id") or response.get("farmer_id") or "",
					response.get("da_id") or "",
					response.get("woreda") or "",
					response.get("kebele") or "",
					number,
					p.get("prompt") or "",
					_cell_text(answers.get(p["id"])),
				]
			)
	return rows


def build_export_xlsx(
	parameters: list[dict], responses: list[dict], *, sheet_name: str = EXPORT_SHEET
) -> bytes:
	"""An XLSX workbook: one row per response, then a sheet of questions beside answers."""
	from frappe.utils.xlsxutils import make_xlsx
	from openpyxl import load_workbook

	workbook = load_workbook(make_xlsx(build_export_rows(parameters, responses), sheet_name))
	answers_sheet = workbook.create_sheet(EXPORT_ANSWERS_SHEET)
	for row in build_export_answer_rows(parameters, responses):
		answers_sheet.append(row)
	out = io.BytesIO()
	workbook.save(out)
	return out.getvalue()


def export_responses(survey_id: str) -> tuple[str, bytes]:
	"""``(filename, xlsx_bytes)`` of the responses the caller may read for a survey."""
	doc = frappe.get_doc(TEMPLATE_DOCTYPE, survey_id)
	doc.check_permission("read")
	responses = _readable_responses(
		survey_id,
		[
			"name",
			"survey",
			"template_version",
			"da_id",
			"farmer_id",
			"consent",
			"sync_state",
			"started_at",
			"completed_at",
			"region",
			"zone",
			"woreda",
			"kebele",
		],
	)
	answers_of = _answers_by_response([r.name for r in responses])
	farmer_ids = sorted({r.farmer_id for r in responses if r.farmer_id})
	reg_ids = dict(
		frappe.get_all(
			"DA Farmer Reference",
			filters={"name": ("in", farmer_ids)},
			fields=["name", "reg_id"],
			as_list=True,
		)
		if farmer_ids
		else []
	)
	parameters = [{"id": p.parameter_id, "prompt": p.prompt, "type": p.response_type} for p in doc.parameters]
	rows = [
		{**r, "farmer_reg_id": reg_ids.get(r.farmer_id), "answers": answers_of.get(r.name, {})}
		for r in responses
	]
	return f"survey-{doc.name}-responses.xlsx", build_export_xlsx(parameters, rows)
