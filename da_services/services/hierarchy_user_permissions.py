"""DA Hierarchy User Permissions: each user may see themselves and everyone below them.

A user with a DA Hierarchy node gets a User Permission on the ``User`` doctype for their
own User and for the User of every node under theirs (the tree's ``lft`` / ``rgt``). So a
manager sees their whole team, and a DA with no reports only themselves. A user with no
node gets none. Only ``User`` is restricted: the node's geography and designation are not.

The permissions are kept in step when a DA Hierarchy node is saved or deleted (for the
node's user and everyone above it, whose team changed) and by a daily job for all. This
module owns every User Permission on ``User``: one that is not wanted is removed.
"""

from __future__ import annotations

import frappe

from da_services.utils.user_permission_sync import reconcile, sync_each, users_with_permissions

HIERARCHY = "DA Hierarchy"
ALLOW = "User"


def wanted_permissions(user: str) -> set[tuple[str, str]]:
	"""``(User, u)`` for ``user`` and the user of every node under theirs."""
	node = frappe.db.get_value(HIERARCHY, {"user": user}, ["lft", "rgt"], as_dict=True)
	if not node:
		return set()
	users = frappe.get_all(
		HIERARCHY,
		filters={"lft": (">=", node.lft), "rgt": ("<=", node.rgt), "user": ("is", "set")},
		pluck="user",
	)
	return {(ALLOW, u) for u in users}


def sync_user_permissions(user: str) -> dict:
	"""Make ``user``'s User Permissions on ``User`` match their team."""
	return reconcile(user, [ALLOW], wanted_permissions(user))


def users_above(node: str | None) -> set[str]:
	"""The users of ``node`` and of every node above it (the root has no user)."""
	if not node:
		return set()
	row = frappe.db.get_value(HIERARCHY, node, ["lft", "rgt"], as_dict=True)
	if not row:
		return set()
	return set(
		frappe.get_all(
			HIERARCHY,
			filters={"lft": ("<=", row.lft), "rgt": (">=", row.rgt), "user": ("is", "set")},
			pluck="user",
		)
	)


def sync_for_change(user: str | None, parents: list[str | None]) -> None:
	"""After a node of ``user`` moved under / away from ``parents``: resync that user and
	everyone above those parents."""
	users = {user} if user else set()
	for parent in parents:
		users |= users_above(parent)
	for u in sorted(u for u in users if u and frappe.db.exists("User", u)):
		sync_user_permissions(u)


def sync_all_user_permissions() -> None:
	"""Daily: every user with a node, and every user still holding a User permission."""
	users = set(frappe.get_all(HIERARCHY, filters={"user": ("is", "set")}, pluck="user"))
	users |= users_with_permissions([ALLOW])
	sync_each(users, sync_user_permissions, "DA Hierarchy user permission sync")
