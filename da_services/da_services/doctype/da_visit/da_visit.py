# Copyright (c) 2026, COSS - Centre for Open Societal Systems and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, getdate, now_datetime

from da_services.integrations.da_registry.client import DARegistryError, get_client

PLANNED = "Planned"
CONFIRMED = "Confirmed"
IN_PROGRESS = "In progress"
COMPLETE = "Physical Visit Complete"
MISSED = "Missed"
RESCHEDULED = "Rescheduled"
CANCELLED = "Cancelled"
SUPERSEDED = "Superseded"

# HLD 9.4 / FSD 3.6: what a visit may move to from each state. Terminal states have no exits.
TRANSITIONS = {
	PLANNED: {CONFIRMED, IN_PROGRESS, MISSED, RESCHEDULED, CANCELLED, SUPERSEDED},
	CONFIRMED: {IN_PROGRESS, MISSED, RESCHEDULED, CANCELLED, SUPERSEDED},
	IN_PROGRESS: {COMPLETE, CANCELLED},
	COMPLETE: set(),
	MISSED: set(),
	RESCHEDULED: set(),
	CANCELLED: set(),
	SUPERSEDED: set(),
}
OPEN_STATES = (PLANNED, CONFIRMED, IN_PROGRESS)
# What counts as "planned" on the dashboard tile: everything that was meant to happen.
PLANNED_STATES = (PLANNED, CONFIRMED, IN_PROGRESS, COMPLETE, MISSED)

FOLLOW_UP_RULE = "visit.follow_up"


class DAVisit(Document):
	"""A field visit (data-model 5.3).

	Status moves only along TRANSITIONS. Check-in and outcome are explicit actions
	(start_visit, submit_outcome) rather than free edits, so the dashboard counting rule
	(only ``Physical Visit Complete`` counts) cannot be gamed by editing a status field.
	A submitted outcome may create a follow-up DA Task and a follow-up Planned visit.
	"""

	def before_insert(self):
		self.created_by_user = self.created_by_user or frappe.session.user
		self.physical_status = self.physical_status or PLANNED
		self._fill_scope_from_registry()

	def validate(self):
		self._validate_transition()
		self._validate_plan()
		self._validate_client_op_id()

	# -- rules -------------------------------------------------------------------

	def _validate_transition(self):
		if self.physical_status not in TRANSITIONS:
			frappe.throw(_("Unknown visit status {0}.").format(self.physical_status))
		if self.is_new():
			if self.physical_status not in (PLANNED, CONFIRMED, IN_PROGRESS):
				frappe.throw(_("A new visit must start as Planned, Confirmed or In progress."))
			return
		before = self.get_doc_before_save()
		if not before or before.physical_status == self.physical_status:
			return
		allowed = TRANSITIONS[before.physical_status]
		if self.physical_status not in allowed:
			frappe.throw(
				_("Visit {0} cannot move from {1} to {2}.").format(
					self.name, before.physical_status, self.physical_status
				)
			)

	def _validate_plan(self):
		if self.physical_status in (PLANNED, CONFIRMED) and not self.scheduled_at:
			frappe.throw(_("A planned visit needs a scheduled date and time."))
		if self.next_action_type == "Follow-up visit" and not self.next_action_date:
			frappe.throw(_("A follow-up visit needs a date."))
		if self.visit_type != "Advisory" and self.advisory_qa:
			frappe.throw(_("Advisory Q&A belongs to Advisory visits only."))

	def _validate_client_op_id(self):
		if not self.client_op_id:
			return
		dup = frappe.db.exists(
			"DA Visit", {"client_op_id": self.client_op_id, "name": ("!=", self.name or "")}
		)
		if dup:
			frappe.throw(
				_("A visit with client_op_id {0} already exists ({1}).").format(self.client_op_id, dup),
				frappe.DuplicateEntryError,
			)

	def _fill_scope_from_registry(self):
		if self.region and self.zone and self.woreda and self.kebele:
			return
		try:
			ref = get_client().get_da(self.da_id)
		except DARegistryError:
			return
		self.region = self.region or ref.region
		self.zone = self.zone or ref.zone
		self.woreda = self.woreda or ref.woreda
		self.kebele = self.kebele or ref.kebele

	# -- derived (not stored) -----------------------------------------------------

	@property
	def ui_status(self) -> str:
		"""List chip in the prototype: Planned | Confirmed | Completed | Missed."""
		if self.physical_status == COMPLETE:
			return "Completed"
		if self.physical_status == MISSED:
			return "Missed"
		if (
			self.physical_status in (PLANNED, CONFIRMED, IN_PROGRESS)
			and self.farmer_confirmation == "Confirmed"
		):
			return "Confirmed"
		return self.physical_status if self.physical_status not in OPEN_STATES else "Planned"

	@property
	def planner_status(self) -> str:
		"""Planner legend: scheduled | completed | overdue."""
		if self.physical_status == COMPLETE:
			return "completed"
		if (
			self.physical_status in OPEN_STATES
			and self.scheduled_at
			and get_datetime(self.scheduled_at) < now_datetime()
		):
			return "overdue"
		return "scheduled"

	# -- actions -----------------------------------------------------------------

	def start_visit(self, arrived_at=None, gps: dict | None = None, farmer_present: bool = True):
		"""Check in at the farm (FR-05b-iv: fix the position before any survey)."""
		if self.physical_status not in (PLANNED, CONFIRMED):
			frappe.throw(_("Only a Planned or Confirmed visit can be started."))
		if not farmer_present:
			frappe.throw(_("Confirm the farmer is present before starting the visit."))
		now = now_datetime()
		self.arrived_at = get_datetime(arrived_at) if arrived_at else now
		self.started_at = now
		self.farmer_present = 1
		gps = gps or {}
		if gps.get("lat") is not None and gps.get("lng") is not None:
			self.gps_lat = gps["lat"]
			self.gps_lng = gps["lng"]
			self.gps_accuracy_m = gps.get("accuracy_m")
			self.gps_captured_at = get_datetime(gps["captured_at"]) if gps.get("captured_at") else now
		self.physical_status = IN_PROGRESS
		self.save()

	def submit_outcome(
		self,
		*,
		purpose=None,
		farmer_response=None,
		observed=None,
		advice_given=None,
		inputs_actions=None,
		outcome=None,
		next_action: dict | None = None,
		advisory_qa: list[dict] | None = None,
	):
		"""Close the physical visit and create the follow-up the DA asked for."""
		if self.physical_status not in (IN_PROGRESS, PLANNED, CONFIRMED):
			frappe.throw(_("Visit {0} is already closed ({1}).").format(self.name, self.physical_status))
		if self.physical_status != IN_PROGRESS:
			# Submitting without an explicit check-in: record the closure time as arrival.
			self.started_at = self.started_at or now_datetime()
			self.arrived_at = self.arrived_at or self.started_at
		if purpose:
			self.purpose = purpose
		self.farmer_response = farmer_response
		self.observed = observed
		self.advice_given = advice_given
		self.inputs_actions = inputs_actions
		self.outcome = outcome or (observed or "")[:140]
		next_action = next_action or {}
		self.next_action_type = next_action.get("type") or "None"
		self.next_action_note = next_action.get("note")
		self.next_action_date = next_action.get("date")
		if advisory_qa is not None:
			self.set("advisory_qa", [])
			for row in advisory_qa:
				if (row.get("question") or "").strip():
					self.append(
						"advisory_qa",
						{"question": row["question"].strip(), "answer": (row.get("answer") or "").strip()},
					)
		now = now_datetime()
		self.completed_at = now
		self.outcome_submitted_at = now
		# Go through In progress first so the transition table stays the single source of truth.
		if self.physical_status != IN_PROGRESS:
			self.physical_status = IN_PROGRESS
			self.save()
		self.physical_status = COMPLETE
		self.save()
		self._create_follow_up()

	def _create_follow_up(self):
		if self.next_action_type not in ("Follow-up visit", "Task"):
			return
		title = self.next_action_note or (
			_("Follow-up visit") if self.next_action_type == "Follow-up visit" else _("Follow-up")
		)
		due = f"{getdate(self.next_action_date)} 09:00:00" if self.next_action_date else None
		task = frappe.get_doc(
			{
				"doctype": "DA Task",
				"da_id": self.da_id,
				"task_type": "Follow-up",
				"title": f"{title}: {self.farmer_id}" if self.farmer_id else title,
				"description": self.next_action_note,
				"farmer_id": self.farmer_id,
				"visit": self.name,
				"due_at": due,
				"priority": "Normal",
				"generated_by_rule": FOLLOW_UP_RULE,
				"region": self.region,
				"zone": self.zone,
				"woreda": self.woreda,
			}
		)
		task.insert(ignore_permissions=True)
		self.db_set("follow_up_task", task.name, update_modified=False)
		from da_services.services.notifications import notify_da

		notify_da(
			self.da_id,
			kind="Task",
			title=_("Follow-up task created: {0}").format(task.title)[:140],
			body=self.next_action_note,
			href=f"/tasks/{task.name}",
			event="task_created",
			reference_doctype="DA Task",
			reference_name=task.name,
		)
		if self.next_action_type == "Follow-up visit":
			visit = frappe.get_doc(
				{
					"doctype": "DA Visit",
					"da_id": self.da_id,
					"farmer_id": self.farmer_id,
					"visit_type": "Follow-up",
					"purpose": self.next_action_note or _("Follow-up to {0}").format(self.name),
					"scheduled_at": due,
					"priority": self.priority,
					"source": "Planned",
					"plot_ref": self.plot_ref,
					"location_lat": self.location_lat,
					"location_lng": self.location_lng,
					"region": self.region,
					"zone": self.zone,
					"woreda": self.woreda,
					"kebele": self.kebele,
					"created_by_user": self.created_by_user,
				}
			)
			visit.insert(ignore_permissions=True)
			self.db_set("follow_up_visit", visit.name, update_modified=False)

	def reschedule(self, scheduled_at, reason: str | None = None):
		"""Supersede this visit with a new Planned one; the farmer is told the new time."""
		if self.physical_status not in (PLANNED, CONFIRMED):
			frappe.throw(_("Only a Planned or Confirmed visit can be rescheduled."))
		new = frappe.copy_doc(self)
		new.physical_status = PLANNED
		new.scheduled_at = get_datetime(scheduled_at)
		new.rescheduled_from = self.name
		new.reschedule_reason = None
		new.farmer_confirmation = "Unknown"
		new.confirmation_note = None
		new.client_op_id = None
		new.insert(ignore_permissions=True)
		self.physical_status = RESCHEDULED
		self.reschedule_reason = reason or "Other"
		self.save()
		return new

	def mark_missed(self, note: str | None = None):
		if self.physical_status not in (PLANNED, CONFIRMED):
			frappe.throw(_("Only a Planned or Confirmed visit can be marked missed."))
		self.physical_status = MISSED
		if note:
			self.outcome = note
		self.save()

	def cancel_visit(self, reason: str | None = None):
		if self.physical_status not in OPEN_STATES:
			frappe.throw(_("Visit {0} is already closed.").format(self.name))
		self.physical_status = CANCELLED
		if reason:
			self.outcome = f"Cancelled: {reason}"
		self.save()
