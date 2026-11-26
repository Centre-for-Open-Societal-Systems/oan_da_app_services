"""The Desk's Workflow buttons on a DA Profile Change Request.

The form's Actions menu and the list view's bulk action call Frappe's
``frappe.model.workflow.apply_workflow`` / ``bulk_workflow_approval``. For this DocType a
step is more than a state change (who decided is recorded, people are told, the last
approval sends the change to the DA Registry, and the app's own rules apply), so hooks.py
routes those two calls here (``override_whitelisted_methods``) and a step taken in the
Desk runs the same service as one taken through the portal's API. Every other DocType is
passed on to Frappe untouched.

A note for the step (needed to reject, ask for changes or close) is the request's "Note
for the next step": written in the form and saved before the action is chosen, because the
Desk offers no actions on a form with unsaved changes. The list view's bulk action carries
no note and takes the service's limit of requests at a time: a step that needs a note is
taken on the form.
"""

from __future__ import annotations

import frappe
from frappe.model import workflow as frappe_workflow

from da_services.services import profile as service
from da_services.workflows.profile_change import DOCTYPE


def _own_saved_note(name: str) -> str | None:
	"""The note this person saved on the request for their step: as saved, not as the form
	sent it, and only when they were the last to save the request. A note someone else
	left there (the DA may save one too) is never recorded as this person's."""
	saved = frappe.db.get_value(DOCTYPE, name, ["step_note", "modified_by"], as_dict=True)
	if saved and saved.modified_by == frappe.session.user:
		return saved.step_note
	return None


@frappe.whitelist()
def apply_workflow(doc, action: str):
	"""The form's Workflow action. Returns the document, as Frappe's own does."""
	values = frappe.parse_json(doc) if isinstance(doc, str) else doc
	if (values or {}).get("doctype") != DOCTYPE:
		return frappe_workflow.apply_workflow(doc, action)
	name = str(values.get("name") or "")
	service.act(name, action=action, note=_own_saved_note(name))
	return frappe.get_doc(DOCTYPE, name)


@frappe.whitelist(methods=["POST"])
def bulk_workflow_approval(docnames: str, doctype: str, action: str):
	"""The list view's bulk Workflow action: each request on its own, as the service does it."""
	if doctype != DOCTYPE:
		return frappe_workflow.bulk_workflow_approval(docnames, doctype, action)
	out = service.act_many(frappe.parse_json(docnames), action=action)
	for row in out["results"]:
		if not row["ok"]:
			frappe.msgprint(f"{row['name']}: {row['message']}", indicator="red")
	frappe.msgprint(
		frappe._("{0}: {1} done, {2} not done").format(action, out["done"], out["failed"]),
		indicator="green" if not out["failed"] else "orange",
	)
