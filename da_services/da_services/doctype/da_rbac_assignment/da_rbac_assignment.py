# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from da_services.services import constants as C
from da_services.services.rbac_user_permissions import sync_user_permissions
from da_services.utils import audit

AUDITED_FIELDS = (
	"user",
	"role",
	"active",
	"da_id",
	"region_scope",
	"zone_scope",
	"woreda_scope",
	"effective_from",
	"effective_to",
)


class DARBACAssignment(Document):
	"""One role grant for one user, bounded by Region / Zone / Woreda and a date window.

	Saving a row also attaches the role to the User so Frappe's own DocPerm checks agree
	with ours, and keeps the User's DA Region / Zone / Woreda User Permissions in step with
	the live scopes (services/rbac_user_permissions.py). The scope questions (which Woreda,
	which DA) stay in utils.permissions. Every create, change and revocation is written to the audit log.
	"""

	def validate(self):
		# da_id: fetch_from user.da_registry_id on the DocType (Frappe). Strip pasted scopes.
		for field in ("region_scope", "zone_scope", "woreda_scope"):
			if self.get(field):
				self.set(field, self.get(field).strip())
		self._validate_role()
		self._validate_scope()
		self._validate_window()

	def before_insert(self):
		self.assigned_by = frappe.session.user

	def after_insert(self):
		audit.record(
			audit.RBAC_CREATED,
			entity_type=self.doctype,
			entity_name=self.name,
			details=self._snapshot(),
		)

	def on_update(self):
		self._sync_user_role()
		before = self.get_doc_before_save()
		# User Permissions for the Region / Zone / Woreda scope, as of today's window.
		sync_user_permissions(self.user)
		if before and before.user and before.user != self.user:
			sync_user_permissions(before.user)
		if self.is_new() or not before:
			return
		changes = {
			f: {"from": before.get(f), "to": self.get(f)}
			for f in AUDITED_FIELDS
			if before.get(f) != self.get(f)
		}
		if not changes:
			return
		revoked = before.get("active") and not self.active
		audit.record(
			audit.RBAC_REVOKED if revoked else audit.RBAC_UPDATED,
			entity_type=self.doctype,
			entity_name=self.name,
			details={"changes": _jsonable(changes), "user": self.user, "role": self.role},
		)

	def after_delete(self):
		sync_user_permissions(self.user)

	def _snapshot(self) -> dict:
		return _jsonable({f: self.get(f) for f in AUDITED_FIELDS})

	def _validate_role(self):
		if self.role not in C.DA_SERVICES_ROLES:
			frappe.throw(
				_("Role {0} is not a DA Services role. Choose one of: {1}").format(
					self.role, ", ".join(C.DA_SERVICES_ROLES)
				)
			)

	def _validate_scope(self):
		if self.role == C.ROLE_DA and not self.da_id:
			frappe.throw(_("A Development Agent assignment needs the DA-ID it is bound to."))
		if self.role == C.ROLE_SUPERVISOR and not self.woreda_scope:
			# FSD 3.1.1: a Supervisor acts only on the DAs of their own Woreda.
			frappe.throw(_("A Supervisor assignment needs a Woreda scope."))
		if self.role == C.ROLE_EXECUTIVE and not self.region_scope:
			frappe.throw(_("An Executive assignment needs a Region scope."))
		if self.woreda_scope and not self.region_scope:
			frappe.throw(_("A Woreda scope needs its Region."))
		if self.zone_scope and not self.region_scope:
			frappe.throw(_("A Zone scope needs its Region."))

	def _validate_window(self):
		if self.effective_to and getdate(self.effective_to) < getdate(self.effective_from):
			frappe.throw(_("Effective To cannot be before Effective From."))

	def _sync_user_role(self):
		"""Keep the User's Has Role table in step with this grant (add only; never remove).

		Removing a role automatically would be wrong while another assignment for the same
		role is still live, so revocation is an explicit administrator action.
		"""
		if not self.active:
			return
		user = frappe.get_doc("User", self.user)
		if any(r.role == self.role for r in user.roles):
			return
		user.append("roles", {"role": self.role})
		user.flags.ignore_permissions = True
		user.save()


def _jsonable(value):
	if isinstance(value, dict):
		return {k: _jsonable(v) for k, v in value.items()}
	if hasattr(value, "isoformat"):
		return value.isoformat()
	return value
