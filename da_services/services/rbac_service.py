"""Administer DA RBAC Assignments.

Only unrestricted roles (OAN Administrator, System Manager, Administrator) may grant,
change or revoke a scope. Validation of the grant itself (Supervisor needs a Woreda,
DA needs a DA-ID, date window) lives on the doctype so it holds for Desk and API alike.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import getdate, today

from da_services.services import constants as C
from da_services.utils.permissions import ScopeContext, require_roles

MAX_PAGE_LENGTH = 500


def clamp_page(limit, start) -> tuple[int, int]:
	"""Page arguments as the database will see them: 1..MAX_PAGE_LENGTH rows, start >= 0."""
	return max(1, min(int(limit), MAX_PAGE_LENGTH)), max(0, int(start))


FIELDS = [
	"name",
	"user",
	"role",
	"active",
	"da_id",
	"region_scope",
	"zone_scope",
	"woreda_scope",
	"effective_from",
	"effective_to",
	"assigned_by",
	"notes",
	"modified",
]
MUTABLE = {
	"active",
	"region_scope",
	"zone_scope",
	"woreda_scope",
	"da_id",
	"effective_from",
	"effective_to",
	"notes",
}


def require_admin() -> ScopeContext:
	return require_roles(*C.UNRESTRICTED_ROLES)


def serialize(doc) -> dict:
	out = {f: doc.get(f) for f in FIELDS}
	for f in ("effective_from", "effective_to"):
		out[f] = str(out[f]) if out[f] else None
	out["modified"] = str(out["modified"]) if out["modified"] else None
	out["active"] = bool(out["active"])
	return out


def list_assignments(
	user=None, role=None, region=None, woreda=None, active=None, limit=50, start=0
) -> list[dict]:
	require_admin()
	filters: dict = {}
	if user:
		filters["user"] = user
	if role:
		filters["role"] = role
	if region:
		filters["region_scope"] = region
	if woreda:
		filters["woreda_scope"] = woreda
	if active is not None and active != "":
		filters["active"] = 1 if str(active).lower() in ("1", "true", "yes") else 0
	limit, start = clamp_page(limit, start)
	rows = frappe.get_all(
		"DA RBAC Assignment",
		filters=filters,
		fields=FIELDS,
		order_by="modified desc",
		limit_page_length=limit,
		limit_start=start,
	)
	return [serialize(frappe._dict(r)) for r in rows]


def get_assignment(name: str) -> dict:
	require_admin()
	return serialize(frappe.get_doc("DA RBAC Assignment", name))


def create_assignment(
	user,
	role,
	effective_from=None,
	effective_to=None,
	region_scope=None,
	woreda_scope=None,
	da_id=None,
	notes=None,
	zone_scope=None,
) -> dict:
	require_admin()
	if not user or not role:
		frappe.throw(_("user and role are required"), frappe.ValidationError)
	doc = frappe.get_doc(
		{
			"doctype": "DA RBAC Assignment",
			"user": user,
			"role": role,
			"active": 1,
			"effective_from": effective_from or today(),
			"effective_to": effective_to,
			"region_scope": region_scope,
			"zone_scope": zone_scope,
			"woreda_scope": woreda_scope,
			"da_id": da_id,
			"notes": notes,
		}
	)
	doc.insert()
	return serialize(doc)


def update_assignment(name: str, **changes) -> dict:
	require_admin()
	doc = frappe.get_doc("DA RBAC Assignment", name)
	unknown = set(changes) - MUTABLE
	if unknown:
		frappe.throw(
			_("Fields cannot be changed here: {0}").format(", ".join(sorted(unknown))), frappe.ValidationError
		)
	for field, value in changes.items():
		if field == "active":
			value = 1 if str(value).lower() in ("1", "true", "yes") else 0
		doc.set(field, value)
	doc.save()
	return serialize(doc)


def revoke_assignment(name: str, reason: str | None = None) -> dict:
	"""Deactivate a grant and close its window today. The row stays for audit."""
	require_admin()
	doc = frappe.get_doc("DA RBAC Assignment", name)
	doc.active = 0
	if not doc.effective_to or getdate(doc.effective_to) > getdate(today()):
		doc.effective_to = today()
	if reason:
		doc.notes = f"{doc.notes}\nRevoked: {reason}".strip() if doc.notes else f"Revoked: {reason}"
	doc.save()
	return serialize(doc)
