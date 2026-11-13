"""Write DA Audit Event rows (HLD 10.3: actor, action, change, provenance, outcome, time).

Two write paths, because of transactions:

- An *allowed* action (an RBAC grant created or revoked) is recorded inside the same
  transaction as the change, so the two commit or roll back together.
- A *denied* action is recorded while the request is about to fail. The API layer rolls
  the transaction back on that failure, which would erase the audit row too. Denials
  are therefore queued on ``frappe.local`` and written by the ``after_request`` hook,
  once the rollback has happened, in their own commit. Outside a request (tests, bench
  console) they are written immediately.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

ENTITY_AUDIT = "DA Audit Event"

ACCESS_DENIED = "access.denied"
RBAC_CREATED = "rbac.assignment.created"
RBAC_UPDATED = "rbac.assignment.updated"
RBAC_REVOKED = "rbac.assignment.revoked"


def record(
	action: str,
	*,
	decision: str = "allowed",
	entity_type: str | None = None,
	entity_name: str | None = None,
	reason: str | None = None,
	details: dict | None = None,
	user: str | None = None,
) -> str | None:
	"""Record one event. Returns the new row name, or None when deferred to after_request."""
	user = user or frappe.session.user
	in_request = getattr(frappe.local, "request", None) is not None
	event = {
		"doctype": ENTITY_AUDIT,
		"action": action,
		"decision": decision,
		"user": user if user and user != "Guest" and frappe.db.exists("User", user) else None,
		"roles": ", ".join(sorted(frappe.get_roles(user))) if user and user != "Guest" else "Guest",
		"source": "api" if in_request else "system",
		"request_id": getattr(frappe.local, "request_id", None),
		"entity_type": entity_type,
		"entity_name": entity_name,
		"reason": reason,
		"details": frappe.as_json(details) if details else None,
		"occurred_at": now_datetime(),
	}

	if decision == "denied" and in_request:
		pending = getattr(frappe.local, "da_audit_pending", None)
		if pending is None:
			pending = frappe.local.da_audit_pending = []
		pending.append(event)
		return None

	return _insert(event)


def flush(response=None, request=None):
	"""``after_request`` hook: persist denials queued during a request that failed."""
	pending = getattr(frappe.local, "da_audit_pending", None)
	if not pending:
		return
	frappe.local.da_audit_pending = []
	for event in pending:
		try:
			_insert(event)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		except Exception:
			frappe.log_error(title="DA audit: failed to persist denial", message=frappe.get_traceback())


def _insert(event: dict) -> str:
	doc = frappe.get_doc(event)
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name
