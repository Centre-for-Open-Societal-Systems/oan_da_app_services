"""User Permissions on DA Kebele from DA Kebele Assignment.

A user gets a User Permission on ``DA Kebele`` for the Kebele of each of their
assignments that is in force: ``state`` is Effective and ``effective_date`` is today or
earlier. A Superseded, Flagged, Proposed or Validated row grants nothing, and neither
does an Effective row whose date has not come yet.

``sync_user_permissions(user)`` recomputes that set and adds or removes User
Permissions to match; the DA Kebele User Permissions are owned by this module.

It runs when an assignment is inserted (also for the users of the rows it supersedes,
which are written with set_value and fire no hooks) or updated, and daily from the
scheduler (``sync_all_user_permissions``), which grants an Effective row on its
``effective_date``.
"""

from __future__ import annotations

import frappe
from frappe.utils import getdate, nowdate

from da_services.utils.user_permission_sync import reconcile, sync_each, users_with_permissions

ALLOW = "DA Kebele"


def wanted_permissions(user: str, today=None) -> set[tuple[str, str]]:
	"""{("DA Kebele", kebele)} of ``user``'s assignments in force today."""
	today = today or getdate(nowdate())
	kebeles = frappe.get_all(
		"DA Kebele Assignment",
		filters={"user": user, "state": "Effective", "effective_date": ("<=", today)},
		pluck="kebele",
		limit_page_length=0,
	)
	return {(ALLOW, kebele) for kebele in kebeles if kebele}


def sync_user_permissions(user: str, today=None) -> dict:
	if not user or not frappe.db.exists("User", user):
		return {"created": 0, "removed": 0}
	return reconcile(user, [ALLOW], wanted_permissions(user, today))


def sync_all_user_permissions() -> None:
	"""Daily: grant Effective assignments whose date has come; revoke the rest."""
	today = getdate(nowdate())
	users = set(frappe.get_all("DA Kebele Assignment", pluck="user", distinct=True))
	users |= users_with_permissions([ALLOW])
	sync_each(users, lambda user: sync_user_permissions(user, today), "Kebele user permission sync")
