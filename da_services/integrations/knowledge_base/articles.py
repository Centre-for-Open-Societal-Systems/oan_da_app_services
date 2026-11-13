"""REST API for KB Article.

GET   /api/v1/da-services/knowledge-base/articles?category=&source=&status=&language=&search=&limit=&start=
GET   /api/v1/da-services/knowledge-base/articles/<id>
POST  /api/v1/da-services/knowledge-base/articles
PATCH /api/v1/da-services/knowledge-base/articles/<id>

Create / update body (JSON):
    {
      "title": "Teff row-planting: spacing and seed rate",   // default title (required on create)
      "summary": "Row planting at 20 cm ...",
      "category": "Crop production",                         // KB Category (required on create)
      "source": "MoA Crop Directorate",                      // KB Source
      "published_on": "2026-09-11",
      "status": "Draft" | "Published" | "Archived",
      "languages": [{"language": "EN", "title": "...", "summary": "...", "body": "<p>..</p>"},
                    {"language": "አማ", "title": "...", "body": "..."}],
      "audience_scopes": [{"region": "ET04"}, {"woreda": "ET04-W01"}]   // empty = everyone
    }
A table key (languages, audience_scopes) replaces the whole table. `send_count` is
maintained by the system and cannot be set here.
"""

from __future__ import annotations

import frappe
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.integrations.knowledge_base import common

DOCTYPE = "KB Article"
BASE = "/knowledge-base/articles"
LIST_FIELDS = ["name", "title", "summary", "category", "source", "published_on", "status", "send_count"]
SCALARS = ("title", "summary", "category", "source", "published_on", "status")
TABLES = {
	"languages": ("language", "title", "summary", "body"),
	"audience_scopes": common.SCOPE_FIELDS,
}


def _language_codes(names: list[str]) -> dict[str, list[dict]]:
	if not names:
		return {}
	rows = frappe.get_all(
		"KB Article Language",
		filters={"parenttype": DOCTYPE, "parent": ("in", names)},
		fields=["parent", "language", "title", "summary"],
		order_by="idx asc",
	)
	out: dict[str, list[dict]] = {}
	for r in rows:
		out.setdefault(r.parent, []).append(r)
	return out


def _full(doc) -> dict:
	d = {f: doc.get(f) for f in LIST_FIELDS}
	d["languages"] = [
		{"language": r.language, "title": r.title, "summary": r.summary, "body": r.body}
		for r in doc.languages
	]
	d["audience_scopes"] = common.serialize_scopes(doc)
	d["modified"] = doc.modified
	return d


@route(f"{BASE}", methods=("GET",), summary="List knowledge-base articles")
@frappe.whitelist()
@handle_api_errors
def list_articles(category=None, source=None, status=None, language=None, search=None, limit=20, start=0):
	ctx = common.read_ctx()
	extra = None
	if language:
		matching = frappe.get_all(
			"KB Article Language", filters={"parenttype": DOCTYPE, "language": language}, pluck="parent"
		)
		extra = {"name": ("in", list(set(matching)) or ["__none__"])}
	rows, meta = common.list_items(
		DOCTYPE,
		ctx,
		fields=LIST_FIELDS,
		search_fields=("title", "summary"),
		category=category,
		source=source,
		status=status,
		search=search,
		extra_filters=extra,
		limit=limit,
		start=start,
	)
	versions = _language_codes([r.name for r in rows])
	data = []
	for r in rows:
		item = dict(r)
		vs = versions.get(r.name, [])
		item["languages"] = [v.language for v in vs]
		chosen = next((v for v in vs if v.language == language), None) if language else None
		if chosen:  # the requested language's wording, falling back to the default
			item["title"] = chosen.title or item["title"]
			item["summary"] = chosen.summary or item["summary"]
		data.append(item)
	return success_response(data=data, meta=meta)


@route(f"{BASE}/<article_id>", methods=("GET",), summary="One knowledge-base article")
@frappe.whitelist()
@handle_api_errors
def get_article(article_id: str):
	ctx = common.read_ctx()
	return success_response(data=_full(common.get_item(DOCTYPE, article_id, ctx)))


@route(f"{BASE}", methods=("POST",), status=201, summary="Create a knowledge-base article")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def create_article(**kwargs):
	common.write_ctx()
	data = common.request_body(kwargs)
	doc = frappe.new_doc(DOCTYPE)
	common.apply_fields(doc, data, SCALARS, TABLES)
	doc.insert()
	return success_response(data=_full(doc), message="Article created")


@route(f"{BASE}/<article_id>", methods=("PATCH",), summary="Update a knowledge-base article")
@frappe.whitelist(methods=["PATCH", "POST"])
@handle_api_errors
def update_article(article_id: str, **kwargs):
	ctx = common.write_ctx()
	data = common.request_body(kwargs)
	data.pop("article_id", None)
	doc = common.get_item(DOCTYPE, article_id, ctx)
	common.apply_fields(doc, data, SCALARS, TABLES)
	doc.save()
	return success_response(data=_full(doc), message="Article updated")
