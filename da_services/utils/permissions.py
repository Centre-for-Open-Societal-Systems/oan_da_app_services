"""Authorization helpers.

Who may do what is Frappe's: Role Permission Manager (DocPerms) and User
Permissions, which narrow lists and
documents to the user's DA Region / Zone / Woreda / Kebele. Every denial is written to
the audit log.
"""

from __future__ import annotations

import frappe
from frappe import _

from da_services.utils import audit

# Denial reason written to the audit log (`DA Audit Event.reason`).
MISSING_PERMISSION = "missing permission"


def _deny(
	message: str, *, reason: str, entity_type: str | None = None, entity_name: str | None = None, **details
):
	audit.record(
		audit.ACCESS_DENIED,
		decision="denied",
		entity_type=entity_type,
		entity_name=entity_name,
		reason=reason,
		details=details or None,
	)
	frappe.throw(message, frappe.PermissionError)


def require_permission(doctype: str, ptype: str = "read", doc=None, user: str | None = None) -> None:
	"""Raise PermissionError unless Frappe grants `ptype` on `doctype` (or on `doc`).

	Who holds which permission is set in Role Permission Manager (the doctype's DocPerms),
	and User Permissions narrow it to records when `doc` is given.
	"""
	user = user or frappe.session.user
	if not frappe.has_permission(doctype, ptype, doc=doc, user=user):
		_deny(
			_("You do not have permission to perform this action."),
			reason=MISSING_PERMISSION,
			entity_type=doctype,
			entity_name=getattr(doc, "name", doc) if doc else None,
			ptype=ptype,
		)


# User Permission doctype -> key in granted_areas().
_AREA_KEYS = {"DA Region": "regions", "DA Zone": "zones", "DA Woreda": "woredas"}


def granted_areas(user: str | None = None) -> dict[str, frozenset[str]]:
	"""The DA Region / Zone / Woreda codes in the user's User Permissions, as data: what
	`/me` reports and the default Woreda some lists start from. It restricts nothing; the
	User Permissions themselves do."""
	allowed = frappe.permissions.get_user_permissions(user or frappe.session.user)
	return {
		key: frozenset(row.get("doc") for row in allowed.get(doctype, []) if row.get("doc"))
		for doctype, key in _AREA_KEYS.items()
	}
