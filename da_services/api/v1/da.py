"""v1: DA records, read from the registry within the caller's scope.

GET /api/v1/da-services/agents/{da_id}

Thin by design: authorise, call the service, wrap in the envelope. The scope rule
lives in services/da_lookup.py and utils/permissions.py.
"""

import frappe
from oan_auth_service.api.utils import handle_api_errors, success_response

from da_services.api.router import route
from da_services.services.da_lookup import get_da_reference


@route("/agents/<da_id>", methods=("GET",), summary="DA reference from the registry, scope-checked")
@frappe.whitelist()
@handle_api_errors
def get_da(da_id: str):
	ref = get_da_reference(da_id)
	return success_response(data=ref.to_dict())
