"""User Permissions from DA RBAC Assignment scopes (Region / Zone / Woreda).

A user gets one User Permission per scope value of their *live* assignments:
``active`` is set, ``effective_from`` is today or earlier, and ``effective_to`` is empty
or today or later. ``region_scope`` -> ``DA Region``, ``zone_scope`` -> ``DA Zone``,
``woreda_scope`` -> ``DA Woreda``.

``sync_user_permissions(user)`` recomputes that set from all of the user's live
assignments and adds or removes User Permissions to match. Recomputing (instead of
adding and removing per assignment) keeps a value that another live assignment still
grants when one assignment ends.

A live assignment with role Development Agent also grants a User Permission on the
``Not Assigned`` DA Kebele (seeded on migrate, setup/install.py), but only while the user
has no other DA Kebele User Permission. Once a Kebele assignment gives them one, the Not
Assigned one is removed; when they lose their last Kebele, it comes back
(``sync_not_assigned``, also run by services/kebele_user_permissions.py).

The User Permissions on DA Region / DA Zone / DA Woreda, and the one on the Not Assigned
DA Kebele, are owned by this module: one a user has that no live assignment grants is
removed. Every other DA Kebele User Permission belongs to services/kebele_user_permissions.py.

It runs when an assignment is saved or deleted, and daily from the scheduler
(``sync_all_user_permissions``): that is what grants an assignment on its
``effective_from`` date and revokes it the day after its ``effective_to`` date.
"""

from __future__ import annotations

import frappe
from frappe.utils import getdate, nowdate

from da_services.services import constants as C
from da_services.utils.user_permission_sync import reconcile, sync_each, users_with_permissions

SCOPE_DOCTYPES = {"region_scope": "DA Region", "zone_scope": "DA Zone", "woreda_scope": "DA Woreda"}
NOT_ASSIGNED = ("DA Kebele", C.NOT_ASSIGNED_KEBELE)


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


def is_live_development_agent(user: str, today=None) -> bool:
	rows = frappe.get_all(
		"DA RBAC Assignment",
		filters={"user": user, "role": C.ROLE_DA, "active": 1},
		fields=["active", "effective_from", "effective_to"],
		limit_page_length=0,
	)
	return any(is_live(row, today) for row in rows)


def has_kebele_permission(user: str) -> bool:
	"""Whether ``user`` has a DA Kebele User Permission other than Not Assigned."""
	return bool(
		frappe.db.exists(
			"User Permission",
			{"user": user, "allow": NOT_ASSIGNED[0], "for_value": ("!=", NOT_ASSIGNED[1])},
		)
	)


def sync_not_assigned(user: str, today=None) -> dict:
	"""Give a live Development Agent with no DA Kebele User Permission the Not Assigned one;
	remove it from anyone else (a DA who now has a Kebele, or no longer a live DA)."""
	if not user or not frappe.db.exists("User", user):
		return {"created": 0, "removed": 0}
	want = is_live_development_agent(user, today) and not has_kebele_permission(user)
	return reconcile(
		user, [NOT_ASSIGNED[0]], {NOT_ASSIGNED} if want else set(), only_values=[NOT_ASSIGNED[1]]
	)


def sync_user_permissions(user: str, today=None) -> dict:
	"""Make ``user``'s DA Region / Zone / Woreda User Permissions match their live scopes,
	and their Not Assigned DA Kebele one match ``sync_not_assigned``."""
	if not user or not frappe.db.exists("User", user):
		return {"created": 0, "removed": 0}
	scopes = reconcile(user, list(SCOPE_DOCTYPES.values()), wanted_permissions(user, today))
	kebele = sync_not_assigned(user, today)
	return {k: scopes[k] + kebele[k] for k in scopes}


def sync_all_user_permissions() -> None:
	"""Daily: grant assignments that start today, revoke those that ended yesterday."""
	today = getdate(nowdate())
	users = set(frappe.get_all("DA RBAC Assignment", pluck="user", distinct=True))
	users |= users_with_permissions(list(SCOPE_DOCTYPES.values()))
	users |= set(
		frappe.get_all(
			"User Permission",
			filters={"allow": NOT_ASSIGNED[0], "for_value": NOT_ASSIGNED[1]},
			pluck="user",
			distinct=True,
		)
	)
	sync_each(users, lambda user: sync_user_permissions(user, today), "RBAC user permission sync")
