"""Access rules and generic list / get / create / update plumbing for KB content.

Who may do what (checked in the backend on every call; hiding a card in the UI is not
authorization):

* Read:  any DA Services role. A reader sees only *Published* items, and only items whose
  audience covers them: no audience rows = everyone; otherwise at least one Region / Zone /
  Woreda row must lie inside, or above, one of the user's own grants.
* Write: OAN Administrator, Communications Officer, System Manager. Writers see every
  status and every audience.

An item the caller may not see is answered with "not found", not "forbidden", so its
existence is not revealed.
"""

from __future__ import annotations

import json

import frappe
from frappe import _

from da_services.services import constants as C
from da_services.utils.permissions import ScopeContext, require_roles

READ_ROLES = (*C.DA_SERVICES_ROLES, C.ROLE_COMMS, "System Manager")
WRITE_ROLES = (C.ROLE_ADMIN, C.ROLE_COMMS, C.ROLE_SUPERVISOR, "System Manager")

MAX_PAGE_LENGTH = 100
STATUSES = ("Draft", "Published", "Archived")
SCOPE_FIELDS = ("region", "zone", "woreda")


# ---- request helpers -------------------------------------------------------------------


def read_ctx() -> ScopeContext:
	return require_roles(*READ_ROLES)


def write_ctx() -> ScopeContext:
	return require_roles(*WRITE_ROLES)


def is_writer(ctx: ScopeContext) -> bool:
	return ctx.has_any_role(*WRITE_ROLES)


def clamp_page(limit, start) -> tuple[int, int]:
	try:
		limit, start = int(limit), int(start)
	except TypeError, ValueError:
		frappe.throw(_("limit and start must be whole numbers."), frappe.ValidationError)
	return max(1, min(limit, MAX_PAGE_LENGTH)), max(0, start)


def request_body(kwargs: dict) -> dict:
	"""JSON body when there is one, else the call's form values (framework keys dropped)."""
	request = getattr(frappe.local, "request", None)
	data = (request.get_json(silent=True) if request is not None else None) or {}
	if not isinstance(data, dict):
		frappe.throw(_("Request body must be a JSON object."), frappe.ValidationError)
	if not data:
		data = {k: v for k, v in kwargs.items() if k not in ("cmd", "_", "sid")}
	return data


def not_found(doctype: str, name: str):
	frappe.throw(_("{0} {1} not found.").format(_(doctype), name), frappe.DoesNotExistError)


# ---- audience --------------------------------------------------------------------------


def _reach(ctx: ScopeContext) -> set[str]:
	"""Every area the user's grants touch: the grants, everything below them, and the
	ancestors above them (content aimed at a Region reaches a Woreda user inside it)."""
	# Narrowest grant wins (same rule as ScopeContext.scope_decision): the Region on an
	# assignment that also names a Zone or Woreda is context, not a grant of the Region.
	grants = set(ctx.zone_scopes) | set(ctx.woreda_scopes) or set(ctx.region_scopes)
	if not grants:
		return set()
	parent: dict[str, str | None] = {}
	children: dict[str, list[str]] = {}
	for row in frappe.get_all("DA Administrative Area", fields=["name", "parent_area"]):
		parent[row.name] = row.parent_area
		if row.parent_area:
			children.setdefault(row.parent_area, []).append(row.name)
	reach, stack = set(grants), list(grants)
	while stack:  # descendants
		for child in children.get(stack.pop(), []):
			if child not in reach:
				reach.add(child)
				stack.append(child)
	for g in grants:  # ancestors
		p, hops = parent.get(g), 0
		while p and p not in reach and hops < 10:
			reach.add(p)
			p, hops = parent.get(p), hops + 1
	return reach


def _audience_rows(doctype: str, names: list[str]) -> dict[str, list[dict]]:
	if not names:
		return {}
	rows = frappe.get_all(
		"KB Audience Scope",
		filters={"parenttype": doctype, "parent": ("in", names)},
		fields=["parent", "region", "zone", "woreda"],
	)
	out: dict[str, list[dict]] = {}
	for r in rows:
		out.setdefault(r.parent, []).append(r)
	return out


def _in_audience(rows: list[dict], reach: set[str]) -> bool:
	if not rows:
		return True
	for r in rows:
		narrowest = r.get("woreda") or r.get("zone") or r.get("region")
		if narrowest and narrowest in reach:
			return True
	return False


def visible_names(ctx: ScopeContext, doctype: str, names: list[str]) -> set[str]:
	"""Subset of `names` this reader's audience covers."""
	if is_writer(ctx):
		return set(names)
	rows, reach = _audience_rows(doctype, names), _reach(ctx)
	return {n for n in names if _in_audience(rows.get(n, []), reach)}


# ---- generic operations ----------------------------------------------------------------


def list_items(
	doctype: str,
	ctx: ScopeContext,
	*,
	fields: list[str],
	search_fields: tuple[str, ...],
	category=None,
	source=None,
	status=None,
	search=None,
	extra_filters: dict | None = None,
	limit=20,
	start=0,
) -> tuple[list[dict], dict]:
	limit, start = clamp_page(limit, start)
	filters: dict = dict(extra_filters or {})
	if category:
		filters["category"] = category
	if source:
		filters["source"] = source
	if is_writer(ctx):
		if status:
			if status not in STATUSES:
				frappe.throw(
					_("status must be one of {0}.").format(", ".join(STATUSES)), frappe.ValidationError
				)
			filters["status"] = status
	else:
		filters["status"] = "Published"
	or_filters = None
	if search:
		like = f"%{str(search).strip()[:100]}%"
		or_filters = [[doctype, f, "like", like] for f in search_fields]

	rows = frappe.get_all(
		doctype,
		filters=filters,
		or_filters=or_filters,
		fields=fields,
		order_by="published_on desc, modified desc",
	)
	allowed = visible_names(ctx, doctype, [r.name for r in rows])
	rows = [r for r in rows if r.name in allowed]
	total = len(rows)
	page = rows[start : start + limit]
	return page, {"count": len(page), "total": total, "limit": limit, "start": start}


def get_item(doctype: str, name: str, ctx: ScopeContext):
	if not frappe.db.exists(doctype, name):
		not_found(doctype, name)
	doc = frappe.get_doc(doctype, name)
	if not is_writer(ctx):
		if doc.status != "Published" or name not in visible_names(ctx, doctype, [name]):
			not_found(doctype, name)
	return doc


def parse_scopes(value) -> list[dict]:
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except ValueError:
			frappe.throw(_("audience_scopes must be JSON."), frappe.ValidationError)
	if not isinstance(value, list):
		frappe.throw(_("audience_scopes must be a list."), frappe.ValidationError)
	out = []
	for row in value:
		if not isinstance(row, dict):
			frappe.throw(_("Each audience scope must be an object."), frappe.ValidationError)
		out.append({k: row.get(k) or None for k in SCOPE_FIELDS})
	return out


def apply_fields(doc, data: dict, scalar_fields: tuple[str, ...], table_fields: dict[str, tuple[str, ...]]):
	"""Copy only whitelisted keys onto the doc. A table key replaces the whole table."""
	for f in scalar_fields:
		if f in data:
			doc.set(f, data[f])
	for table, allowed in table_fields.items():
		if table not in data:
			continue
		rows = data[table]
		if table == "audience_scopes":
			rows = parse_scopes(rows)
		elif not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
			frappe.throw(_("{0} must be a list of objects.").format(table), frappe.ValidationError)
		doc.set(table, [])
		for r in rows:
			doc.append(table, {k: r.get(k) for k in allowed})


def serialize_scopes(doc) -> list[dict]:
	return [{k: r.get(k) for k in SCOPE_FIELDS} for r in doc.get("audience_scopes") or []]
