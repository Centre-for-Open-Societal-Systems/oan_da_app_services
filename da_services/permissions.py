"""Frappe permission hooks for the app's own doctypes (deny-by-default).

The query condition filters list views, reports and the REST API uniformly;
has_permission mirrors it for a single document. Both are registered in hooks.py.
"""

import frappe

from da_services.services import constants as C
from da_services.utils.permissions import get_scope_context

DENY = "1=0"


def _is_unrestricted(user: str) -> bool:
	return bool(set(frappe.get_roles(user)) & C.UNRESTRICTED_ROLES)


def _sql_in(values) -> str:
	return ", ".join(frappe.db.escape(v) for v in sorted(values))


def da_scoped_query_conditions(doctype: str, user: str | None = None) -> str:
	"""List filter for operational doctypes that carry `da_id`, `region`, `woreda`.

	Same rule as utils.permissions.ScopeContext: a DA sees rows for their own DA-ID; a
	Supervisor / Executive sees rows whose posting codes fall inside their grants, the
	narrowest grant winning; a Zone-only grant cannot be resolved against rows that carry
	no zone, so it yields nothing (no silent widening to the Region); Communications
	Officer and anyone without a grant see nothing. Unrestricted roles see everything.
	"""
	user = user or frappe.session.user
	ctx = get_scope_context(user)
	if ctx.unrestricted:
		return ""
	t = f"`tab{doctype}`"
	if ctx.has_any_role(C.ROLE_DA):
		if not ctx.da_ids:
			return DENY
		return f"{t}.`da_id` in ({_sql_in(ctx.da_ids)})"
	if ctx.has_any_role(C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE):
		if ctx.woreda_scopes:
			return f"{t}.`woreda` in ({_sql_in(ctx.woreda_scopes)})"
		if ctx.zone_scopes:
			return DENY  # scope unresolved: rows carry no zone
		if ctx.region_scopes:
			return f"{t}.`region` in ({_sql_in(ctx.region_scopes)})"
	return DENY


def has_da_scoped_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
	"""Single-document mirror of da_scoped_query_conditions. Executive is read-only."""
	user = user or frappe.session.user
	ctx = get_scope_context(user)
	if ctx.unrestricted:
		return True
	if ctx.has_any_role(C.ROLE_DA):
		return ctx.owns_da(doc.da_id)
	if ctx.has_any_role(C.ROLE_SUPERVISOR):
		return ctx.covers(doc.region, doc.woreda)
	if ctx.has_any_role(C.ROLE_EXECUTIVE):
		return ptype == "read" and ctx.covers(doc.region, doc.woreda)
	return False


def da_task_query_conditions(user: str | None = None) -> str:
	return da_scoped_query_conditions("DA Task", user)


def has_da_task_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
	return has_da_scoped_permission(doc, ptype, user)


def da_kebele_assignment_query_conditions(user: str | None = None) -> str:
	return da_scoped_query_conditions("DA Kebele Assignment", user)


def has_da_kebele_assignment_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
	return has_da_scoped_permission(doc, ptype, user)


def da_profile_change_request_query_conditions(user: str | None = None) -> str:
	return da_scoped_query_conditions("DA Profile Change Request", user)


def has_da_profile_change_request_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
	# Writes go through services/profile.py; Desk/REST stays read-only for everyone in scope.
	if ptype != "read" and not _is_unrestricted(user or frappe.session.user):
		return False
	return has_da_scoped_permission(doc, ptype, user)


def da_visit_query_conditions(user: str | None = None) -> str:
	return da_scoped_query_conditions("DA Visit", user)


def da_notification_query_conditions(user: str | None = None) -> str:
	"""Everyone sees only their own notifications; administrators see all."""
	user = user or frappe.session.user
	if _is_unrestricted(user):
		return ""
	return "`tabDA Notification`.`user` = {user}".format(user=frappe.db.escape(user))


def has_da_notification_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
	user = user or frappe.session.user
	if _is_unrestricted(user):
		return True
	return doc.user == user and ptype in ("read", "write")


def has_da_visit_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
	return has_da_scoped_permission(doc, ptype, user)


def rbac_assignment_query_conditions(user: str | None = None) -> str:
	"""Administrators see every grant; everyone else sees only their own rows."""
	user = user or frappe.session.user
	if _is_unrestricted(user):
		return ""
	return "`tabDA RBAC Assignment`.`user` = {user}".format(user=frappe.db.escape(user))


def has_rbac_assignment_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
	user = user or frappe.session.user
	if _is_unrestricted(user):
		return True
	# Non-administrators may only read, and only their own grants.
	return ptype == "read" and doc.user == user


def da_farmer_link_query_conditions(user: str | None = None) -> str:
	"""DA Farmer Link: DAs see only their own links, Supervisors/Executives see by scope.

	DAs are read-only through this hook; writes go through services that check scope.
	"""
	user = user or frappe.session.user
	ctx = get_scope_context(user)
	if ctx.unrestricted:
		return ""
	t = "`tabDA Farmer Link`"
	if ctx.has_any_role(C.ROLE_DA):
		if not ctx.da_ids:
			return DENY
		return f"{t}.`da_id` in ({_sql_in(ctx.da_ids)})"
	if ctx.has_any_role(C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE):
		if ctx.woreda_scopes:
			return f"{t}.`woreda` in ({_sql_in(ctx.woreda_scopes)})"
		if ctx.zone_scopes:
			return DENY  # scope unresolved: rows carry no zone
		if ctx.region_scopes:
			return f"{t}.`region` in ({_sql_in(ctx.region_scopes)})"
	return DENY


def has_da_farmer_link_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
	"""DA Farmer Link: DAs read-only, Supervisors can write in their Woreda."""
	user = user or frappe.session.user
	ctx = get_scope_context(user)
	if ctx.unrestricted:
		return True
	if ctx.has_any_role(C.ROLE_DA):
		# DA can only read their own links, not create/write
		return ptype == "read" and ctx.owns_da(doc.da_id)
	if ctx.has_any_role(C.ROLE_SUPERVISOR):
		return ctx.covers(doc.region, doc.woreda)
	if ctx.has_any_role(C.ROLE_EXECUTIVE):
		return ptype == "read" and ctx.covers(doc.region, doc.woreda)
	return False


def da_farmer_reference_query_conditions(user: str | None = None) -> str:
	"""DA Farmer Reference: DAs see linked farmers, Supervisors/Executives see by scope.

	This is the cache layer for farmer data from the registry.
	"""
	user = user or frappe.session.user
	ctx = get_scope_context(user)
	if ctx.unrestricted:
		return ""
	t = "`tabDA Farmer Reference`"
	if ctx.has_any_role(C.ROLE_DA):
		# DA sees only linked farmers
		if not ctx.da_ids:
			return DENY
		# Get linked farmer IDs
		linked_farmer_ids = frappe.get_all(
			"DA Farmer Link",
			filters={"da_id": ("in", list(ctx.da_ids)), "is_active": 1},
			pluck="farmer_id",
		)
		if not linked_farmer_ids:
			return DENY
		return f"{t}.`farmer_id` in ({_sql_in(linked_farmer_ids)})"
	if ctx.has_any_role(C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE):
		if ctx.woreda_scopes:
			return f"{t}.`woreda` in ({_sql_in(ctx.woreda_scopes)})"
		if ctx.zone_scopes:
			return DENY  # scope unresolved: rows carry no zone
		if ctx.region_scopes:
			return f"{t}.`region` in ({_sql_in(ctx.region_scopes)})"
	return DENY


def has_da_farmer_reference_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
	"""DA Farmer Reference: Read-only for most users."""
	user = user or frappe.session.user
	ctx = get_scope_context(user)
	if ctx.unrestricted:
		return True
	if ctx.has_any_role(C.ROLE_DA):
		# DA can read if farmer is linked to them
		if not ctx.da_ids:
			return False
		is_linked = frappe.db.exists(
			"DA Farmer Link",
			{"farmer_id": doc.farmer_id, "da_id": ("in", list(ctx.da_ids)), "is_active": 1},
		)
		return ptype == "read" and is_linked
	if ctx.has_any_role(C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE):
		return ptype == "read" and ctx.covers(doc.region, doc.woreda)
	return False
