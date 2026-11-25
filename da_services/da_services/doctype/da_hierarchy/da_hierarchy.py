# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

"""DA Hierarchy: who reports to whom, as a tree of users.

Laid out like ERPNext's Department: one root record, "All DA Hierarchy", that has no user and
is the only node without a parent; every other node is a user, and one saved without a
parent is placed under the root.
"""

import frappe
from frappe import _
from frappe.utils.nestedset import NestedSet

from da_services.services import hierarchy_user_permissions

# Not "DA Hierarchy": Frappe refuses a name equal to its doctype (naming.validate_name).
ROOT = "All DA Hierarchy"


class DAHierarchy(NestedSet):
	nsm_parent_field = "parent_da_hierarchy"
	allow_root_deletion = False  # NestedSet.on_trash refuses to delete the root

	def autoname(self):
		# The doctype names "By script": a user node by its user, the root (no user) as ROOT.
		self.name = self.user or ROOT

	def validate(self):
		# autoname has run, so a new node without a user is named ROOT here.
		is_root = self.name == ROOT and not (self.is_new() and frappe.db.exists(self.doctype, ROOT))
		if is_root:
			self.validate_root()
		else:
			if not self.user:
				frappe.throw(_("User is required."), frappe.MandatoryError)
			if not self.parent_da_hierarchy:
				self.parent_da_hierarchy = ROOT
			self.validate_parent_is_group()

	def validate_root(self):
		if self.user or self.parent_da_hierarchy:
			frappe.throw(_("{0} is the root: it has no user and no parent.").format(frappe.bold(ROOT)))
		self.is_group = 1
		self.full_name = ROOT

	def validate_parent_is_group(self):
		"""A node can only sit under a group: NestedSet checks the node itself, not its parent,
		and the tree view never expands a leaf, so a child under one would be hidden."""
		parent = self.parent_da_hierarchy
		if parent and not frappe.db.get_value(self.doctype, parent, "is_group"):
			frappe.throw(
				_("{0} is not a group, so {1} cannot be placed under it.").format(
					frappe.bold(parent), frappe.bold(self.name or self.user)
				)
			)

	def on_update(self):
		super().on_update()  # NestedSet: place the node in the tree first
		before = self.get_doc_before_save()
		old_parent = before.parent_da_hierarchy if before else None
		hierarchy_user_permissions.sync_for_change(self.user, [self.parent_da_hierarchy, old_parent])

	def after_delete(self):
		hierarchy_user_permissions.sync_for_change(self.user, [self.parent_da_hierarchy])


def on_doctype_update():
	frappe.db.add_index("DA Hierarchy", ["lft", "rgt"])


@frappe.whitelist()
def add_node():
	"""The tree's "Add Child". Pins the doctype, which otherwise comes from the request."""
	from frappe.desk.treeview import make_tree_args

	args = make_tree_args(**frappe.form_dict)
	args.doctype = "DA Hierarchy"
	frappe.get_doc(args).insert()


def ensure_root():
	"""Create the root, and put every other parentless node under it."""
	if not frappe.db.exists("DA Hierarchy", ROOT):
		frappe.get_doc({"doctype": "DA Hierarchy", "is_group": 1}).insert(ignore_permissions=True)
	for name in frappe.get_all(
		"DA Hierarchy",
		filters={"parent_da_hierarchy": ("is", "not set"), "name": ("!=", ROOT)},
		pluck="name",
	):
		doc = frappe.get_doc("DA Hierarchy", name)
		doc.parent_da_hierarchy = ROOT
		doc.save(ignore_permissions=True)
