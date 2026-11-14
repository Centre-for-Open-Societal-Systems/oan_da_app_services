"""User Permissions from DA RBAC Assignment scopes (Region / Zone / Woreda).

A user gets one User Permission per scope value of their *live* assignments:
``active`` is set, ``effective_from`` is today or earlier, and ``effective_to`` is empty
or today or later. ``region_scope`` -> ``DA Region``, ``zone_scope`` -> ``DA Zone``,
``woreda_scope`` -> ``DA Woreda``.

``sync_user_permissions(user)`` recomputes that set from all of the user's live
assignments and adds or removes User Permissions to match. Recomputing (instead of
adding and removing per assignment) keeps a value that another live assignment still
grants when one assignment ends.

The User Permissions on DA Region / DA Zone / DA Woreda are owned by this module: one a
user has that no live assignment grants is removed.

It runs when an assignment is saved or deleted, and daily from the scheduler
(``sync_all_user_permissions``): that is what grants an assignment on its
``effective_from`` date and revokes it the day after its ``effective_to`` date.
"""

from __future__ import annotations

import frappe
from frappe.utils import getdate, nowdate

from da_services.utils.user_permission_sync import reconcile, sync_each, users_with_permissions

SCOPE_DOCTYPES = {"region_scope": "DA Region", "zone_scope": "DA Zone", "woreda_scope": "DA Woreda"}


def is_live(assignment, today=None) -> bool:
	today = today or getdate(nowdate())
	if not assignment.get("active") or not assignment.get("effective_from"):
		return False
	if getdate(assignment.effective_from) > today:
		return False
	return not assignment.get("effective_to") or getdate(assignment.effective_to) >= today


def wanted_permissions(user: str, today=None) -> set[tuple[str, str]]:
	"""{(allow doctype, value)} granted to ``user`` by their live assignments today."""
	rows = frappe.get_all(
		"DA RBAC Assignment",
		filters={"user": user, "active": 1},
		fields=["effective_from", "effective_to", "active", *SCOPE_DOCTYPES],
		limit_page_length=0,
	)
	wanted = set()
	for row in rows:
		if not is_live(row, today):
			continue
		for field, doctype in SCOPE_DOCTYPES.items():
			if row.get(field):
				wanted.add((doctype, row.get(field)))
	return wanted


def sync_user_permissions(user: str, today=None) -> dict:
	"""Make ``user``'s DA Region / Zone / Woreda User Permissions match their live scopes."""
	if not user or not frappe.db.exists("User", user):
		return {"created": 0, "removed": 0}
	return reconcile(user, list(SCOPE_DOCTYPES.values()), wanted_permissions(user, today))


def sync_all_user_permissions() -> None:
	"""Daily: grant assignments that start today, revoke those that ended yesterday."""
	today = getdate(nowdate())
	users = set(frappe.get_all("DA RBAC Assignment", pluck="user", distinct=True))
	users |= users_with_permissions(list(SCOPE_DOCTYPES.values()))
	sync_each(users, lambda user: sync_user_permissions(user, today), "RBAC user permission sync")
