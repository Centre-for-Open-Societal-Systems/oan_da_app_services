# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

# Cache key of the enabled menu, read by api/v1/me.py menu_for().
CACHE_KEY = "da_portal_menu"


class DAPortalMenuItem(Document):
	def validate(self):
		self.route = (self.route or "").strip()
		if not self.route.startswith("/"):
			frappe.throw(_("Route must start with /, e.g. /dashboard."), frappe.ValidationError)
		self.label = (self.label or "").strip() or None

	def on_update(self):
		frappe.cache.delete_value(CACHE_KEY)

	def on_trash(self):
		frappe.cache.delete_value(CACHE_KEY)


def load_menu() -> list[tuple[str, frozenset[str]]]:
	"""Enabled items in sequence order, each with the roles that see it."""
	items = frappe.get_all(
		"DA Portal Menu Item",
		filters={"enabled": 1},
		fields=["name", "route"],
		order_by="sequence asc, route asc",
	)
	roles: dict[str, set[str]] = {}
	for row in frappe.get_all(
		"Has Role",
		filters={"parenttype": "DA Portal Menu Item", "parent": ("in", [i.name for i in items] or [""])},
		fields=["parent", "role"],
	):
		roles.setdefault(row.parent, set()).add(row.role)
	return [(i.route, frozenset(roles.get(i.name, ()))) for i in items]


def cached_menu() -> list[tuple[str, frozenset[str]]]:
	return frappe.cache.get_value(CACHE_KEY, generator=load_menu)
