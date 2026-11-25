# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from da_services.services import geo

PLANNED = "Planned"
CANCELLED = "Cancelled"

TRANSITIONS = {
	PLANNED: {CANCELLED},
	CANCELLED: set(),
}

MAX_ATTENDEES = 20


class DAGathering(Document):
	"""A planned gathering of farmers in a DA kebele (v1: Planned / Cancelled)."""

	def before_insert(self):
		self.created_by_user = self.created_by_user or frappe.session.user
		self.status = self.status or PLANNED

	def before_validate(self):
		# Fill read-only address geo before mandatory checks see ``address``.
		self.stamp_scope_and_address()

	def validate(self):
		self._validate_transition()
		self._validate_plan()
		self._validate_client_op_id()

	def stamp_scope_and_address(self):
		"""Stamp region/zone/woreda/kebele from Effective Kebele Assignment; compose address.

		Editable location inputs are Venue (``location_name``), landmark, lat, lng only.
		``da_id`` comes from meta ``fetch_from: user.da_registry_id`` (or the API service).
		"""
		if not self.da_id:
			return

		kebele = frappe.db.get_value(
			"DA Kebele Assignment",
			{"da_id": self.da_id, "state": "Effective"},
			"kebele",
			order_by="modified desc",
		)
		if not kebele:
			frappe.throw(
				_("DA {0} has no Effective Kebele Assignment.").format(self.da_id),
				frappe.ValidationError,
			)

		chain = geo.ancestors_of_kebele(kebele)
		self.kebele = chain.get("kebele")
		self.woreda = chain.get("woreda")
		self.zone = chain.get("zone")
		self.region = chain.get("region")
		self.address = self._compose_address()

	@frappe.whitelist()
	def preview_address_from_assignment(self) -> dict:
		"""Desk preview of read-only address geo via ``run_doc_method`` (session auth).

		Must not be a ``da_services.services.*`` whitelist — JWT middleware requires
		Bearer on every ``/api/method/da_services.*`` path.
		"""
		# Desk may send user before fetch_from has written da_id into the in-memory doc.
		if not self.da_id and self.user:
			self.da_id = frappe.db.get_value("User", self.user, "da_registry_id")
		self.stamp_scope_and_address()
		return {
			"region": self.region,
			"zone": self.zone,
			"woreda": self.woreda,
			"kebele": self.kebele,
			"address": self.address,
			"da_id": self.da_id,
		}

	def _compose_address(self) -> str:
		"""Venue, Kebele, Woreda, Zone, Region (display names when available)."""
		parts = [
			(self.location_name or "").strip() or None,
			geo.display_name("DA Kebele", self.kebele) or self.kebele,
			geo.display_name("DA Woreda", self.woreda) or self.woreda,
			geo.display_name("DA Zone", self.zone) or self.zone,
			geo.display_name("DA Region", self.region) or self.region,
		]
		return ", ".join(part for part in parts if part)

	def _validate_transition(self):
		if self.status not in TRANSITIONS:
			frappe.throw(_("Unknown gathering status {0}.").format(self.status))
		if self.is_new():
			if self.status != PLANNED:
				frappe.throw(_("A new gathering must start as Planned."))
			return
		previous = self.get_doc_before_save()
		if not previous or previous.status == self.status:
			return
		allowed_statuses = TRANSITIONS[previous.status]
		if self.status not in allowed_statuses:
			frappe.throw(
				_("Gathering {0} cannot move from {1} to {2}.").format(
					self.name, previous.status, self.status
				)
			)

	def _validate_plan(self):
		# venue / address / attendee.farmer_id are DocType ``reqd`` (address stamped above).
		attendee_count = len(self.get("attendees") or [])
		if attendee_count > MAX_ATTENDEES:
			frappe.throw(
				_("A gathering may include at most {0} farmers.").format(MAX_ATTENDEES),
				frappe.ValidationError,
			)
		if self.is_new() and attendee_count < 1:
			frappe.throw(_("At least one farmer is required."), frappe.ValidationError)
		seen_farmer_ids: set[str] = set()
		for attendee in self.get("attendees") or []:
			farmer_id = (attendee.farmer_id or "").strip()
			if not farmer_id:
				continue
			if farmer_id in seen_farmer_ids:
				frappe.throw(_("Duplicate farmer_id {0}.").format(farmer_id), frappe.ValidationError)
			seen_farmer_ids.add(farmer_id)

	def _validate_client_op_id(self):
		if not self.client_op_id:
			return
		duplicate_name = frappe.db.exists(
			"DA Gathering", {"client_op_id": self.client_op_id, "name": ("!=", self.name or "")}
		)
		if duplicate_name:
			frappe.throw(
				_("A gathering with client_op_id {0} already exists ({1}).").format(
					self.client_op_id, duplicate_name
				),
				frappe.DuplicateEntryError,
			)

	def cancel_gathering(self, reason: str | None = None):
		"""Move Planned → Cancelled."""
		if self.status != PLANNED:
			frappe.throw(
				_("Only a Planned gathering can be cancelled (current status: {0}).").format(self.status),
				frappe.ValidationError,
			)
		self.status = CANCELLED
		self.cancel_reason = (reason or "").strip() or None
		self.cancelled_at = now_datetime()
		self.save()
