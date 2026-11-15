"""Evidence on a grievance: the one rule DA Services adds to the Grievance Service's own.

The service reads an attachment by its id alone (``GET /v1/attachments/{id}/view``) and
checks it against the configured Development Agent account, not against a ticket. The DA
route names the ticket, so the attachment must be one of that ticket's.
"""

import frappe
from frappe import _

from da_services.integrations.grievance.client import get_client


def read_attachment(ticket_id: str, attachment_id: str) -> tuple[dict, bytes]:
	"""The attachment's row and its bytes, when it is on this ticket."""
	client = get_client()
	row = next((r for r in client.list_attachments(ticket_id) if r["attachment_id"] == attachment_id), None)
	if row is None:
		frappe.throw(
			_("Document {0} not found on grievance {1}.").format(attachment_id, ticket_id),
			frappe.DoesNotExistError,
		)
	return row, client.get_attachment_content(attachment_id)
