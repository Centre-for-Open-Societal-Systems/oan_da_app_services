"""v1: experts a DA can reach for advice (T3, "DA can view list of experts").

GET /api/v1/da-services/experts?specialisation=&q=&limit=50&start=0

Thin by design: authorise, call the service, wrap in the envelope. Scope and the
"active + has a specialisation + not me" rule live in services/experts.py.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.services import constants as C
from da_services.services import experts as svc
from da_services.utils.permissions import require_roles

# Communications Officer is excluded on purpose: FSD Appendix D gives it no access
# to DA identity.
READ_ROLES = C.DA_READ_ROLES


@route("/experts", methods=("GET",), summary="Experts / My Team visible to the caller")
@frappe.whitelist()
@handle_api_errors
def list_experts(specialisation=None, q=None, limit=None, start=None):
	"""List the specialists (active DAs with a specialisation) in the caller's scope.

	Scope: DA / Supervisor -> their Woreda(s); Executive -> Zone(s) or Region(s);
	Admin -> all. The caller's own DA record is never returned.

	Query parameters:
	- specialisation: exact match, case-insensitive (e.g. "Crop production")
	- q: search in name, specialisation or DA-ID
	- limit: page size (1-500, default 50)
	- start: offset (default 0)

	Returns:
	    {
	        "success": true,
	        "data": [{"da_id", "full_name", "specialisation", "phone", "email", "language",
	                  "kebele", "woreda", "zone", "region", "active_status"}],
	        "meta": {"total", "limit", "start", "by_specialisation": {"Crop production": 3, ...}}
	    }
	"""
	ctx = require_roles(*READ_ROLES)

	specialisation = specialisation or frappe.form_dict.get("specialisation")
	q = q or frappe.form_dict.get("q")
	try:
		limit = cint(limit or frappe.form_dict.get("limit") or 50)
		start = cint(start or frappe.form_dict.get("start") or 0)
	except ValueError, TypeError:
		frappe.throw(_("Pagination parameters must be integers."), frappe.ValidationError)
	if limit < 1 or limit > 500:
		frappe.throw(_("Limit must be between 1 and 500."), frappe.ValidationError)
	if start < 0:
		frappe.throw(_("Start must be >= 0."), frappe.ValidationError)

	rows, meta = svc.list_experts(ctx, specialisation=specialisation, q=q, limit=limit, start=start)
	return success_response(data=rows, meta=meta)
