"""REST API for KB Category (read only).

GET /api/v1/da-services/knowledge-base/categories

Returns the enabled categories (the topic tabs on the Knowledge Base), ordered by
`sort_order`, then by name.
"""

from __future__ import annotations

import frappe
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.integrations.knowledge_base import common

DOCTYPE = "KB Category"
BASE = "/knowledge-base/categories"
FIELDS = ["name", "category_name", "category_name_am", "color", "sort_order"]


@route(f"{BASE}", methods=("GET",), summary="List knowledge-base categories")
@frappe.whitelist()
@handle_api_errors
def list_categories():
	common.read_ctx()
	rows = frappe.get_all(
		DOCTYPE, filters={"enabled": 1}, fields=FIELDS, order_by="sort_order asc, category_name asc"
	)
	return success_response(data=[dict(r) for r in rows], meta={"count": len(rows)})
