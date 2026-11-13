"""REST API for KB Video.

GET   /api/v1/da-services/knowledge-base/videos?category=&source=&status=&search=&limit=&start=
GET   /api/v1/da-services/knowledge-base/videos/<id>
POST  /api/v1/da-services/knowledge-base/videos
PATCH /api/v1/da-services/knowledge-base/videos/<id>

Create / update body (JSON):
    {
      "title": "...",                                        // required on create
      "description": "...",
      "youtube_url": "https://youtu.be/dQw4w9WgXcQ",         // required on create; youtube_id is parsed
      "thumbnail": "https://...",                            // optional; defaults to the YouTube thumbnail
      "category": "Livestock",                               // KB Category (required on create)
      "source": "Ministry of Agriculture",                   // KB Source
      "published_on": "2026-10-07",
      "status": "Draft" | "Published" | "Archived",
      "audience_scopes": [{"region": "ET04"}, {"woreda": "ET04-W01"}]   // empty = everyone
    }
`audience_scopes`, when present, replaces the whole table. `youtube_id` is derived and
cannot be set here.
"""

from __future__ import annotations

import frappe
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.integrations.knowledge_base import common

DOCTYPE = "KB Video"
BASE = "/knowledge-base/videos"
LIST_FIELDS = [
	"name",
	"title",
	"description",
	"youtube_url",
	"youtube_id",
	"thumbnail",
	"category",
	"source",
	"published_on",
	"status",
]
SCALARS = ("title", "description", "youtube_url", "thumbnail", "category", "source", "published_on", "status")
TABLES = {"audience_scopes": common.SCOPE_FIELDS}


def _full(doc) -> dict:
	d = {f: doc.get(f) for f in LIST_FIELDS}
	d["audience_scopes"] = common.serialize_scopes(doc)
	d["modified"] = doc.modified
	return d


@route(f"{BASE}", methods=("GET",), summary="List knowledge-base videos")
@frappe.whitelist()
@handle_api_errors
def list_videos(category=None, source=None, status=None, search=None, limit=20, start=0):
	ctx = common.read_ctx()
	rows, meta = common.list_items(
		DOCTYPE,
		ctx,
		fields=LIST_FIELDS,
		search_fields=("title", "description"),
		category=category,
		source=source,
		status=status,
		search=search,
		limit=limit,
		start=start,
	)
	return success_response(data=[dict(r) for r in rows], meta=meta)


@route(f"{BASE}/<video_id>", methods=("GET",), summary="One knowledge-base video")
@frappe.whitelist()
@handle_api_errors
def get_video(video_id: str):
	ctx = common.read_ctx()
	return success_response(data=_full(common.get_item(DOCTYPE, video_id, ctx)))


@route(f"{BASE}", methods=("POST",), status=201, summary="Create a knowledge-base video")
@frappe.whitelist(methods=["POST"])
@handle_api_errors
def create_video(**kwargs):
	common.write_ctx()
	data = common.request_body(kwargs)
	doc = frappe.new_doc(DOCTYPE)
	common.apply_fields(doc, data, SCALARS, TABLES)
	doc.insert()
	return success_response(data=_full(doc), message="Video created")


@route(f"{BASE}/<video_id>", methods=("PATCH",), summary="Update a knowledge-base video")
@frappe.whitelist(methods=["PATCH", "POST"])
@handle_api_errors
def update_video(video_id: str, **kwargs):
	ctx = common.write_ctx()
	data = common.request_body(kwargs)
	data.pop("video_id", None)
	doc = common.get_item(DOCTYPE, video_id, ctx)
	common.apply_fields(doc, data, SCALARS, TABLES)
	doc.save()
	return success_response(data=_full(doc), message="Video updated")
