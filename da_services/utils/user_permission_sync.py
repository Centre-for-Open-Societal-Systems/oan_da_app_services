"""Make a user's User Permissions on some doctypes match a wanted set.

Shared by services/rbac_user_permissions.py (DA Region / Zone / Woreda, and the
"Not Assigned" DA Kebele) and services/kebele_user_permissions.py (every other DA Kebele).
Each caller owns the User Permissions of its own ``allow`` doctypes and values: one that
is not in the wanted set is removed.
"""

from __future__ import annotations

import frappe


def reconcile(
	user: str,
	allows: list[str],
	wanted: set[tuple[str, str]],
	*,
	only_values: list[str] | None = None,
	skip_values: list[str] | None = None,
) -> dict:
	"""Add the missing ``(allow, value)`` User Permissions of ``user``; remove the rest.

	``only_values`` limits the User Permissions looked at (and so removed) to those
	values; ``skip_values`` leaves those values alone, for another caller to own.
	"""
	if not user or not frappe.db.exists("User", user):
		return {"created": 0, "removed": 0}
	filters = {"user": user, "allow": ("in", allows)}
	if only_values:
		filters["for_value"] = ("in", only_values)
	elif skip_values:
		filters["for_value"] = ("not in", skip_values)
	existing = frappe.get_all(
		"User Permission",
		filters=filters,
		fields=["name", "allow", "for_value"],
		limit_page_length=0,
	)
	have = set()
	removed = 0
	for perm in existing:
		key = (perm.allow, perm.for_value)
		if key in wanted and key not in have:
			have.add(key)
			continue
		frappe.delete_doc("User Permission", perm.name, ignore_permissions=True, force=True)
		removed += 1

	created = 0
	for allow, value in sorted(wanted - have):
		if not frappe.db.exists(allow, value):
			continue  # the value is not (yet) in the master
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": user,
				"allow": allow,
				"for_value": value,
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)
		created += 1
	return {"created": created, "removed": removed}


def users_with_permissions(allows: list[str]) -> set[str]:
	return set(
		frappe.get_all("User Permission", filters={"allow": ("in", allows)}, pluck="user", distinct=True)
	)


def sync_each(users, sync_one, title: str) -> None:
	"""Run ``sync_one(user)`` per user, committing each; one failure does not stop the rest."""
	for user in sorted(u for u in users if u):
		try:
			sync_one(user)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"{title} failed for {user}")
