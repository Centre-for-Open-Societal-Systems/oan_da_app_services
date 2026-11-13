"""DA profile changes: request, two approvals in DA Services, then the DA Registry (FSD 3.7).

The DA Registry owns the DA record. Nobody edits it from DA Services directly: each edit
is a `DA Profile Change Request`.

1. The DA asks for a change to their own profile, or their Woreda Supervisor asks for one
   on a DA of their Woreda.
2. Level 1: a Supervisor of the DA's Woreda approves, rejects or asks for changes. A
   Supervisor's own request has its level 1 already (the Supervisor who sent it).
3. Level 2: an OAN Administrator approves or rejects; never the person who gave level 1.
4. On the level-2 approval, in the Administrator's own request, the change is sent to the
   registry as one change request, with the Administrator's token (user mode). The
   registry applies it without an approval of its own (or, until that is switched off,
   holds it at its own stages); DA Services reads the answer back.

No registry write is ever made in a DA's or a Supervisor's request: with the planned
registry roles their tokens can read only. Nobody decides a request for their own DA
record or one they sent.

Who may do what:
- Development Agent: view own profile, send / cancel own requests, list own requests.
- Supervisor: view the DAs of their Woreda, send a request for one, decide level 1 for
  the DAs of their Woreda.
- OAN Administrator: decide level 2, retry or close a request the registry did not take.
- Executive: read requests and DA profiles in their Region.
"""

from __future__ import annotations

import functools
import time

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime, today

from da_services.da_services.doctype.da_profile_change_request import (
	da_profile_change_request as states,
)
from da_services.da_services.doctype.da_profile_change_request.da_profile_change_request import OPEN_STATES
from da_services.integrations.da_registry.client import (
	DANotFound,
	DARegistryError,
	DARegistryUnavailable,
	get_client,
)
from da_services.integrations.da_registry.schemas import DAReference, ProfileUpdate
from da_services.services import constants as C
from da_services.services import profile_fields
from da_services.services.da_lookup import get_da_reference
from da_services.services.notifications import notify, notify_da
from da_services.utils import audit
from da_services.utils.permissions import ScopeContext, require_roles

DOCTYPE = "DA Profile Change Request"
READ_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)
SUBMIT_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR)
DECIDE_ROLES = (C.ROLE_SUPERVISOR, C.ROLE_ADMIN)
AGENT_PROFILE_ROLES = (C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)
DECISIONS = {"Approve": "Approved", "Reject": "Rejected", "Request changes": "Changes requested"}
LEVEL2_DECISIONS = ("Approve", "Reject")
NOTE_MIN_LENGTH = 5
READ_BACK_STEP_SECONDS = 2
LOCK_TIMEOUT_SECONDS = 5


class AlreadyDecided(frappe.ValidationError):
	"""The request is no longer in the state this action needs: somebody acted on it first.

	The screen reloads the request instead of showing an input error.
	"""

	api_code = "ALREADY_DECIDED"


class OpenRequestExists(frappe.ValidationError):
	"""The DA already has an open change request for one of the fields."""

	api_code = "OPEN_REQUEST_EXISTS"


# What the DA (and the portal) is shown for each state
STATUS_LABELS = {
	states.PENDING_SUPERVISOR: "Waiting for the Supervisor",
	states.PENDING_ADMINISTRATOR: "Waiting for the Administrator",
	states.SENT_TO_REGISTRY: "Approved, waiting for the DA Registry",
	states.APPLIED: "Approved and applied",
	states.REGISTRY_FAILED: "Approved, but the DA Registry did not apply it",
	states.REJECTED: "Rejected",
	states.CHANGES_REQUESTED: "Changes requested",
	states.CANCELLED: "Cancelled",
	states.CLOSED: "Closed",
}

FIELDS = [
	"name",
	"da_id",
	"da_name",
	"section",
	"status",
	"initiated_by_role",
	"requested_by",
	"reason",
	"no_supervisor_found",
	"changes",
	"region",
	"zone",
	"woreda",
	"region_name",
	"zone_name",
	"woreda_name",
	"l1_by",
	"l1_at",
	"l1_note",
	"l2_by",
	"l2_at",
	"l2_note",
	"approver",
	"decided_at",
	"decision_note",
	"registry_sync_state",
	"registry_reference",
	"registry_approval",
	"registry_error_code",
	"registry_error",
	"push_attempts",
	"last_push_at",
	"applied_at",
	"local_sync_state",
	"local_sync_error",
	"closed_by",
	"closed_at",
	"close_note",
	"client_op_id",
	"creation",
	"modified",
]
_DATETIMES = (
	"l1_at",
	"l2_at",
	"decided_at",
	"last_push_at",
	"applied_at",
	"closed_at",
	"creation",
	"modified",
)


_PEOPLE = ("requested_by", "l1_by", "l2_by", "closed_by")


def _full_names(rows) -> dict[str, str]:
	"""{user id: full name} of everyone named on these requests (one query)."""
	users = {row.get(f) for row in rows for f in _PEOPLE if row.get(f)}
	if not users:
		return {}
	found = frappe.get_all("User", filters={"name": ("in", sorted(users))}, fields=["name", "full_name"])
	return {u.name: u.full_name for u in found if u.full_name}


def serialize(row, ctx: ScopeContext, names: dict[str, str] | None = None) -> dict:
	"""One change request as the API answers it, for the caller ``ctx``.

	``names`` is ``_full_names`` of a whole list, so a list costs one query.
	"""
	out = {f: row.get(f) for f in FIELDS}
	if isinstance(out.get("changes"), str):
		out["changes"] = frappe.parse_json(out["changes"])
	for f in _DATETIMES:
		if out.get(f):
			# One format whether the row was just saved (text) or read back (datetime):
			# ISO 8601 in the site's time zone, without an offset.
			out[f] = get_datetime(out[f]).isoformat()
	out["no_supervisor_found"] = bool(out.get("no_supervisor_found"))
	out["status_label"] = STATUS_LABELS.get(out.get("status"), out.get("status"))
	names = _full_names([out]) if names is None else names
	for f in _PEOPLE:
		# A staff user id is an opaque IAM id: the screen shows the name.
		out[f"{f}_name"] = names.get(out[f]) if out.get(f) else None
	out.update(_actions(out, ctx))
	return out


def serialize_many(rows, ctx: ScopeContext) -> list[dict]:
	names = _full_names(rows)
	return [serialize(row, ctx, names) for row in rows]


def _actions(row: dict, ctx: ScopeContext) -> dict:
	"""What this caller may do with the request now, by the same rules the actions apply."""
	admin = ctx.has_any_role(C.ROLE_ADMIN)
	failed = row.get("status") == states.REGISTRY_FAILED
	allowed = _allowed_decisions(row, ctx)
	return {
		"can_decide": bool(allowed),
		"allowed_decisions": allowed,
		"can_cancel": (
			ctx.has_any_role(*SUBMIT_ROLES)
			and row.get("requested_by") == ctx.user
			and row.get("status") in states.PENDING_STATES
		),
		"can_retry": admin and failed and not ctx.owns_da(row.get("da_id")),
		"can_close": admin and failed,
		"can_sync_local": admin and _local_update_failed(row),
	}


def _local_update_failed(row) -> bool:
	"""An applied location change whose DA Services follow-up (the grant move) failed."""
	return (
		row.get("status") == states.APPLIED
		and row.get("section") == "Location"
		and row.get("local_sync_state") == "Failed"
	)


def _own_da_id(ctx: ScopeContext) -> str:
	own = sorted(ctx.da_ids)
	if not own:
		frappe.throw(_("Your account is not bound to a DA-ID."), frappe.PermissionError)
	return own[0]


def _changes(doc) -> dict:
	return frappe.parse_json(doc.changes) if isinstance(doc.changes, str) else (doc.changes or {})


# -- view ----------------------------------------------------------------------------


def _profile(ref: DAReference) -> dict:
	return {
		"da_id": ref.da_id,
		"full_name": ref.full_name,
		"mobile": ref.phone,
		"email": ref.email,
		"language": ref.language,
		"photo": ref.photo_url,
		"education_tier": ref.education_tier,
		"specialisation": ref.specialisation,
		"region": ref.region,
		"zone": ref.zone,
		"woreda": ref.woreda,
		"kebele": ref.kebele,
		"active_status": ref.active_status,
		"joined_at": ref.joined_at,
		"fayda_status": ref.fayda_status,
		# Demographics & personal details, as the DA Registry holds them
		"salutation": ref.salutation,
		"first_name": ref.first_name,
		"father_name": ref.father_name,
		"grandfather_name": ref.grandfather_name,
		"fayda_id": ref.fayda_id,
		"gender": ref.gender,
		"marital_status": ref.marital_status,
		"birth_month_ec": ref.birth_month_ec,
		"birth_month_name": ref.birth_month_name,
		"birth_year_ec": ref.birth_year_ec,
		"telegram_number": ref.telegram_number,
		"alternate_number": ref.alternate_number,
		"position": ref.position,
		"country": ref.country,
		"region_name": ref.region_name,
		"zone_name": ref.zone_name,
		"woreda_name": ref.woreda_name,
		"kebele_name": ref.kebele_name,
	}


def _profile_view(ref: DAReference, ctx: ScopeContext) -> dict:
	open_rows = frappe.get_all(
		DOCTYPE,
		filters={"da_id": ref.da_id, "status": ("in", list(OPEN_STATES))},
		fields=FIELDS,
		order_by="creation desc",
	)
	return {
		"profile": _profile(ref),
		"editable_fields": sorted(profile_fields.FIELDS),
		"fields": profile_fields.catalog(ref, get_client()),
		"note_min_length": profile_fields.NOTE_MIN_LENGTH,
		"locked_fields": [],
		"pending_requests": serialize_many(open_rows, ctx),
	}


def my_profile(ctx: ScopeContext | None = None) -> dict:
	"""The DA's profile as the Registry holds it, plus open change requests."""
	ctx = ctx or require_roles(C.ROLE_DA)
	da_id = _own_da_id(ctx)
	ref = get_da_reference(da_id, ctx)
	if _refresh_registry_results(da_id):
		ref = get_da_reference(da_id, ctx)  # a change just landed: show it
	return _profile_view(ref, ctx)


def agent_profile(da_id: str, ctx: ScopeContext | None = None) -> dict:
	"""A DA's profile for a Supervisor, Executive or Administrator in scope, with open requests.

	``can_request`` says whether this caller may send a change request for the DA: only
	a Supervisor of the DA's Woreda may.
	"""
	ctx = ctx or require_roles(*AGENT_PROFILE_ROLES)
	ref = get_da_reference(da_id, ctx)  # scope: the registry's own posting of the DA
	if _refresh_registry_results(ref.da_id):
		ref = get_da_reference(da_id, ctx)
	out = _profile_view(ref, ctx)
	out["can_request"] = _supervises(ctx, ref.woreda) and not ctx.owns_da(ref.da_id)
	return out


def _supervises(ctx: ScopeContext, woreda: str | None) -> bool:
	"""A Supervisor grant on exactly this Woreda (Supervisor grants are Woreda grants)."""
	return ctx.has_any_role(C.ROLE_SUPERVISOR) and bool(woreda) and woreda in ctx.woreda_scopes


# -- submit / cancel -----------------------------------------------------------------


def submit_change_request(
	*,
	changes: dict,
	reason: str | None = None,
	client_op_id: str | None = None,
	da_id: str | None = None,
	ctx: ScopeContext | None = None,
) -> dict:
	"""A DA asks to change their own profile, or a Supervisor asks for a DA of their Woreda.

	A DA's ``da_id`` always comes from their own DA RBAC assignment: one sent in the
	request is ignored. A Supervisor names the DA; the DA must be posted in their Woreda.
	"""
	ctx = ctx or require_roles(*SUBMIT_ROLES)
	if not ctx.has_any_role(*SUBMIT_ROLES):
		frappe.throw(
			_("Only a Development Agent or their Supervisor can request a profile change."),
			frappe.PermissionError,
		)
	client_op_id = (client_op_id or "").strip() or None
	if client_op_id:
		existing = frappe.db.get_value(DOCTYPE, {"client_op_id": client_op_id}, "name")
		if existing:
			return serialize(frappe.get_doc(DOCTYPE, existing), ctx)

	target = (da_id or "").strip()
	as_supervisor = bool(target) and ctx.has_any_role(C.ROLE_SUPERVISOR) and not ctx.owns_da(target)
	if as_supervisor:
		ref = get_da_reference(target, ctx)
		if not _supervises(ctx, ref.woreda):
			frappe.throw(_("DA {0} is not posted in your Woreda.").format(ref.da_id), frappe.PermissionError)
	elif ctx.has_any_role(C.ROLE_DA):
		ref = get_da_reference(_own_da_id(ctx), ctx)
	else:
		frappe.throw(_("da_id is required: name a DA of your Woreda."), frappe.ValidationError)

	if _refresh_registry_results(ref.da_id):
		ref = get_da_reference(ref.da_id, ctx)  # an earlier change just landed: diff against it
	section, diff = profile_fields.validate_changes(changes, ref=ref, reason=reason, client=get_client())
	# Two submits for one DA at once (a double tap, the DA and the Supervisor together)
	# would both pass the open-request check: take them one at a time.
	lock = _lock_da(ref.da_id)
	try:
		# A transaction reads the database as it was when it began: start a new one now
		# that the lock is held, or the check below cannot see the row a request that
		# held the lock a moment ago has just committed.
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		if client_op_id:
			existing = frappe.db.get_value(DOCTYPE, {"client_op_id": client_op_id}, "name")
			if existing:
				return serialize(frappe.get_doc(DOCTYPE, existing), ctx)  # the same operation, sent twice
		out = _create_request(ctx, ref, section, diff, reason, client_op_id, as_supervisor)
		# Committed before the lock is released, so the next holder sees this request.
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		return out
	finally:
		_unlock(lock)


def _lock(key: str, busy: str) -> str | None:
	"""Take a MariaDB named lock across workers, or answer 400 with ``busy`` after a few seconds.

	Released by ``_unlock`` in the caller's finally block, and by MariaDB itself if the
	connection drops.
	"""
	if frappe.db.db_type != "mariadb":
		return None  # no named locks on other engines; the request transaction still applies
	key = f"da_services:profile_change:{key}"
	if not frappe.db.sql("select get_lock(%s, %s)", (key, LOCK_TIMEOUT_SECONDS))[0][0]:
		frappe.throw(busy, frappe.ValidationError)
	return key


def _lock_da(da_id: str) -> str | None:
	"""Serialise submissions for one DA."""
	return _lock(
		f"da:{da_id}",
		_("Another change request for DA {0} is being saved; try again in a moment.").format(da_id),
	)


def _one_at_a_time(action):
	"""Run an action on one change request (decide, cancel, retry, close ...) alone.

	Two people acting on the same request at the same moment (two Supervisors of a
	Woreda, a double click) are taken one after the other. Each starts a new
	transaction once it holds the lock and commits before it lets go, so the second
	one reads what the first one did and gets the ordinary answer ("already approved
	by ...") instead of a database conflict.
	"""

	@functools.wraps(action)
	def wrapper(name: str, *args, **kwargs):
		key = _lock(
			f"request:{name}",
			_("Change request {0} is being processed by someone else; try again in a moment.").format(name),
		)
		try:
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
			out = action(name, *args, **kwargs)
			frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
			return out
		finally:
			_unlock(key)

	return wrapper


def _unlock(key: str | None) -> None:
	if key:
		frappe.db.sql("select release_lock(%s)", (key,))


def _create_request(
	ctx: ScopeContext,
	ref: DAReference,
	section: str,
	diff: dict,
	reason: str | None,
	client_op_id: str | None,
	as_supervisor: bool,
) -> dict:
	# One open request per field: a second request may change other fields of the same
	# section (email while a mobile change waits), never a field that is already in an
	# open request. The four location levels travel together, so Location has one.
	for row in frappe.get_all(
		DOCTYPE,
		filters={"da_id": ref.da_id, "section": section, "status": ("in", list(OPEN_STATES))},
		fields=["name", "status", "changes"],
		order_by="creation asc",
	):
		taken = sorted(set(diff) & set(_changes(row)))
		if taken:
			raise OpenRequestExists(
				_(
					"DA {0} already has an open change request for {1} ({2}: {3}). It must be finished first."
				).format(ref.da_id, ", ".join(taken), row.name, STATUS_LABELS.get(row.status, row.status))
			)

	supervisors = _woreda_supervisors(ref.woreda)
	now = now_datetime()
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"da_id": ref.da_id,
			"da_name": ref.full_name or None,
			"section": section,
			"status": states.PENDING_ADMINISTRATOR if as_supervisor else states.PENDING_SUPERVISOR,
			"initiated_by_role": C.ROLE_SUPERVISOR if as_supervisor else C.ROLE_DA,
			"requested_by": ctx.user,
			"reason": (reason or "").strip() or None,
			"no_supervisor_found": 0 if as_supervisor or supervisors else 1,
			"changes": frappe.as_json(diff),
			"region": ref.region,
			"zone": ref.zone,
			"woreda": ref.woreda,
			# Names as the registry gave them now: lists show them without a lookup.
			"region_name": ref.region_name,
			"zone_name": ref.zone_name,
			"woreda_name": ref.woreda_name,
			"client_op_id": client_op_id,
		}
	)
	if as_supervisor:
		# The Supervisor who asks is the request's level 1.
		doc.update({"l1_by": ctx.user, "l1_at": now, "l1_note": _("Requested by the Supervisor")})
	doc.insert(ignore_permissions=True)
	audit.record(
		"profile_change_requested",
		entity_type=doc.doctype,
		entity_name=doc.name,
		details={"initiated_by_role": doc.initiated_by_role, "section": section, "changes": diff},
	)

	if as_supervisor:
		_tell_da(doc, _("Your Supervisor asked for a change to your {0}").format(section.lower()))
		_tell_administrators(doc)
	elif supervisors:
		label = _("Profile change of DA {0} ({1}) waits for your approval").format(doc.da_id, section)
		for user in supervisors:
			if user != ctx.user:
				_send(user, doc, label, event="profile_change_waiting")
	else:
		frappe.log_error(
			title=f"{DOCTYPE} {doc.name}: no Supervisor for Woreda {doc.woreda}",
			message=f"No active Supervisor assignment has woreda_scope {doc.woreda!r}; the request waits.",
		)
	return serialize(doc, ctx)


@_one_at_a_time
def cancel_change_request(name: str, ctx: ScopeContext | None = None) -> dict:
	"""Whoever sent a request (the DA, or the Supervisor) withdraws it while it waits for a decision.

	A DA cannot cancel a request their Supervisor sent about them.
	"""
	ctx = ctx or require_roles(*SUBMIT_ROLES)
	doc = frappe.get_doc(DOCTYPE, name, for_update=True)
	if doc.requested_by != ctx.user:
		frappe.throw(_("You can only cancel a change request you sent."), frappe.PermissionError)
	if doc.status not in states.PENDING_STATES:
		raise AlreadyDecided(_("Only a request waiting for a decision can be cancelled."))
	doc.status = states.CANCELLED
	doc.flags.ignore_permissions = True
	doc.save()
	audit.record("profile_change_cancelled", entity_type=doc.doctype, entity_name=doc.name, details={})
	return serialize(doc, ctx)


# -- list / get ----------------------------------------------------------------------


def _check_read(doc, ctx: ScopeContext) -> None:
	if ctx.unrestricted:
		return
	if ctx.has_any_role(C.ROLE_DA) and ctx.owns_da(doc.da_id):
		return
	if ctx.has_any_role(C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE) and ctx.covers(doc.region, doc.woreda, doc.zone):
		return
	frappe.throw(_("Change request {0} is outside your scope.").format(doc.name), frappe.PermissionError)


def _many(value) -> list[str]:
	"""A filter given as a list or as comma-separated text: "Pending Administrator,Registry failed"."""
	if not value:
		return []
	items = value if isinstance(value, list | tuple) else str(value).split(",")
	return [str(v).strip() for v in items if str(v).strip()]


def list_change_requests(
	ctx: ScopeContext | None = None,
	*,
	status: str | list | None = None,
	da_id: str | None = None,
	woreda: str | list | None = None,
	section: str | None = None,
	mine: bool = False,
	no_supervisor_found: bool = False,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int]:
	"""DA: own. Supervisor / Executive: their Woreda / Region (scope via the query hook).

	``status`` and ``woreda`` take one value or several (comma-separated); the scope
	rule still applies on top, so a Woreda outside the caller's scope lists nothing.
	``mine`` keeps the requests the caller sent (a Supervisor's own requests wait for an
	Administrator, so they are in no queue of the Supervisor's).
	``no_supervisor_found`` keeps the requests still waiting for a Supervisor in a Woreda
	that had none when they were sent: an Administrator has to assign one.
	"""
	ctx = ctx or require_roles(*READ_ROLES)
	filters: dict = {}
	statuses = _many(status)
	unknown = sorted(set(statuses) - set(STATUS_LABELS))
	if unknown:
		frappe.throw(
			_("Unknown status: {0}. Use one of {1}.").format(", ".join(unknown), ", ".join(STATUS_LABELS)),
			frappe.ValidationError,
		)
	if statuses:
		filters["status"] = ("in", statuses)
	woredas = _many(woreda)
	if woredas:
		filters["woreda"] = ("in", woredas)
	if section:
		filters["section"] = section
	if da_id:
		filters["da_id"] = da_id
	if mine:
		filters["requested_by"] = ctx.user
	if no_supervisor_found:
		filters["no_supervisor_found"] = 1
		if not statuses:
			# Once a Supervisor has decided, the flag is history.
			filters["status"] = states.PENDING_SUPERVISOR
	limit = max(1, min(int(limit), 500))
	start = max(0, int(start))
	rows = frappe.get_list(
		DOCTYPE,
		filters=filters,
		fields=FIELDS,
		order_by="creation desc",
		limit_page_length=limit,
		limit_start=start,
	)
	total = len(frappe.get_list(DOCTYPE, filters=filters, pluck="name", limit_page_length=0))
	return serialize_many(rows, ctx), total


def get_change_request(name: str, ctx: ScopeContext | None = None) -> dict:
	ctx = ctx or require_roles(*READ_ROLES)
	doc = frappe.get_doc(DOCTYPE, name)
	_check_read(doc, ctx)
	if doc.status == states.SENT_TO_REGISTRY and ctx.has_any_role(C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_ADMIN):
		# Ask the registry with the caller's own token whether it has applied the change.
		_read_back(doc, get_client(), notify_outcome=True)
		doc.reload()
	return serialize(doc, ctx)


# -- decide --------------------------------------------------------------------------


def _decision_level(row, ctx: ScopeContext) -> int:
	"""1 or 2: the approval this caller may give on the request now. Raises why not.

	Level 1 is a Supervisor of the DA's Woreda; level 2 an OAN Administrator who did
	not give level 1. Nobody decides a request about their own DA record or one they
	sent. ``decide`` acts on this and the ``can_decide`` flag reports it.
	"""
	name, status = row.get("name"), row.get("status")
	if ctx.owns_da(row.get("da_id")) or row.get("requested_by") == ctx.user:
		raise frappe.PermissionError(_("You cannot decide your own change request."))
	if status == states.PENDING_SUPERVISOR:
		if not _supervises(ctx, row.get("woreda")):
			raise frappe.PermissionError(
				_("Only a Supervisor of Woreda {0} can give the first approval of {1}.").format(
					row.get("woreda"), name
				)
			)
		return 1
	if status == states.PENDING_ADMINISTRATOR:
		if not ctx.has_any_role(C.ROLE_ADMIN):
			raise frappe.PermissionError(
				_(
					"Change request {0} was already approved by {1} and waits for an OAN Administrator."
				).format(name, row.get("l1_by"))
			)
		if row.get("l1_by") == ctx.user:
			raise frappe.PermissionError(
				_("You gave the first approval of {0}; another person must give the second.").format(name)
			)
		return 2
	raise AlreadyDecided(
		_("Change request {0} is already decided: {1}.").format(name, STATUS_LABELS.get(status, status))
	)


def _allowed_decisions(row, ctx: ScopeContext) -> list[str]:
	"""The decisions this caller may give on the request now; none when they may not decide."""
	try:
		level = _decision_level(row, ctx)
	except frappe.ValidationError, frappe.PermissionError:
		return []
	return list(DECISIONS) if level == 1 else list(LEVEL2_DECISIONS)


@_one_at_a_time
def decide(
	name: str,
	*,
	decision: str,
	note: str | None = None,
	ctx: ScopeContext | None = None,
	wait_for_registry: bool = True,
) -> dict:
	"""Level 1 (Woreda Supervisor) or level 2 (OAN Administrator), by the request's state.

	``wait_for_registry=False`` (bulk decisions) reads the registry's answer once right
	after the create instead of waiting a few seconds for it; a change still PENDING is
	picked up on a later call.
	"""
	ctx = ctx or require_roles(*DECIDE_ROLES)
	if decision not in DECISIONS:
		frappe.throw(_("decision must be one of {0}.").format(", ".join(DECISIONS)), frappe.ValidationError)
	note = (note or "").strip()
	if decision != "Approve" and len(note) < NOTE_MIN_LENGTH:
		frappe.throw(
			_("A note of at least {0} characters is required to reject or request changes.").format(
				NOTE_MIN_LENGTH
			),
			frappe.ValidationError,
		)

	# Locked: two Supervisors (or two Administrators) of the same queue cannot both decide.
	doc = frappe.get_doc(DOCTYPE, name, for_update=True)
	level = _decision_level(doc, ctx)
	if level == 2 and decision not in LEVEL2_DECISIONS:
		frappe.throw(_("The Administrator can approve or reject."), frappe.ValidationError)

	now = now_datetime()
	doc.update({f"l{level}_by": ctx.user, f"l{level}_at": now, f"l{level}_note": note or None})
	doc.update({"approver": ctx.user, "decided_at": now, "decision_note": note or None})
	if decision != "Approve":
		doc.status = DECISIONS[decision]
	elif level == 1:
		doc.status = states.PENDING_ADMINISTRATOR
	doc.flags.ignore_permissions = True
	doc.save()
	audit.record(
		f"profile_change_level{level}_decided",
		entity_type=doc.doctype,
		entity_name=doc.name,
		details={"decision": decision, "note": note},
	)

	if level == 2 and decision == "Approve":
		_send_to_registry(doc, ctx, wait=wait_for_registry)
		doc.reload()
	elif level == 1 and decision == "Approve":
		_tell_administrators(doc)
	_tell_da_outcome(doc)
	doc.reload()
	return serialize(doc, ctx)


# Each approval is a few registry calls: keep one request well under the gateway timeout.
BULK_LIMIT = 20


def decide_many(
	names: list[str], *, decision: str, note: str | None = None, ctx: ScopeContext | None = None
) -> dict:
	"""The same decision on several requests (the Approvals screen's bulk Approve / Reject).

	Each request is decided on its own, with every rule of ``decide``; one that cannot
	be decided is reported and the others go on. The registry's answer is not waited
	for: a change still PENDING shows as Sent to registry and is read back later.
	"""
	ctx = ctx or require_roles(*DECIDE_ROLES)
	names = list(dict.fromkeys(_many(names)))
	if not names:
		frappe.throw(_("ids must name at least one change request."), frappe.ValidationError)
	if len(names) > BULK_LIMIT:
		frappe.throw(
			_("Decide at most {0} change requests at once.").format(BULK_LIMIT), frappe.ValidationError
		)
	results = []
	for name in names:
		try:
			row = decide(name, decision=decision, note=note, ctx=ctx, wait_for_registry=False)
		except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as exc:
			frappe.db.rollback()  # only this request: the ones before it are committed
			frappe.clear_messages()  # frappe.throw queued the message for the response; it goes in results
			results.append(
				{
					"name": name,
					"ok": False,
					"status": None,
					"status_label": None,
					"code": error_code(exc),
					"message": str(exc),
				}
			)
			continue
		results.append(
			{
				"name": name,
				"ok": True,
				"status": row["status"],
				"status_label": row["status_label"],
				"code": None,
				"message": decision_message(row),
			}
		)
	done = sum(1 for r in results if r["ok"])
	return {"results": results, "decided": done, "failed": len(results) - done}


def error_code(exc: Exception) -> str:
	"""The API error code of a refusal: its own where it has one, else by its kind."""
	own = getattr(exc, "api_code", None)
	if own:
		return own
	if isinstance(exc, frappe.DoesNotExistError):
		return "NOT_FOUND"
	if isinstance(exc, frappe.PermissionError):
		return "PERMISSION_DENIED"
	return "VALIDATION_ERROR"


def decision_message(row: dict) -> str:
	"""The answer shown to the person who just acted on a request."""
	status = row.get("status")
	if status == states.REGISTRY_FAILED:
		if row.get("registry_sync_state") == "Unavailable":
			return _("Approved, but the DA Registry is unavailable. Retry the request later.")
		return _("Approved here, but the DA Registry refused it: {0}").format(row.get("registry_error") or "")
	return {
		states.PENDING_ADMINISTRATOR: _("Approved; the request now waits for an OAN Administrator."),
		states.SENT_TO_REGISTRY: _("Approved and sent; waiting for the DA Registry to apply it."),
		states.APPLIED: _("Approved and applied in the DA Registry."),
		states.REJECTED: _("Change request rejected."),
		states.CHANGES_REQUESTED: _("Changes requested from the DA."),
		states.CLOSED: _("Change request closed."),
	}.get(status, _("Change request {0}.").format(str(status).lower()))


# -- the registry push (level-2 approval, retry) -------------------------------------


def _send_to_registry(doc, ctx: ScopeContext, *, wait: bool = True) -> None:
	"""Create the registry change request with the Administrator's token; record the answer.

	Only an OAN Administrator's request reaches this. Nothing is retried in the
	background: a refusal or an outage leaves the row at Registry failed for the
	Administrator to retry or close.
	"""
	if not ctx.has_any_role(C.ROLE_ADMIN) or ctx.user != frappe.session.user:
		frappe.throw(
			_("Only an OAN Administrator can send a change to the DA Registry."), frappe.PermissionError
		)
	client = get_client()
	doc.db_set(
		{"push_attempts": int(doc.push_attempts or 0) + 1, "last_push_at": now_datetime()},
		update_modified=False,
	)
	try:
		changes = profile_fields.registry_changes(doc.section, _changes(doc), client)
		result = client.push_profile_update(
			ProfileUpdate(da_id=doc.da_id, changes=changes, approver=ctx.user, reference=doc.name)
		)
	except DARegistryUnavailable as exc:
		_push_failed(doc, "Unavailable", None, str(exc))
		_persist()
		return
	except DARegistryError as exc:
		_push_failed(doc, "Refused", getattr(exc, "code", None), str(exc))
		_persist()
		return
	except profile_fields.InvalidProfileChange as exc:
		# The location no longer matches Master Data.
		_push_failed(doc, "Refused", None, str(exc))
		_persist()
		return
	if not result.accepted:
		_push_failed(doc, "Refused", result.error_code, result.message or "refused")
		_persist()
		return
	doc.db_set(
		{
			"registry_sync_state": "Accepted",
			"registry_reference": result.external_reference,
			"registry_approval": "Pending",
			"registry_error_code": None,
			"registry_error": None,
			"status": states.SENT_TO_REGISTRY,
		}
	)
	audit.record(
		"profile_change_registry_result",
		entity_type=doc.doctype,
		entity_name=doc.name,
		details={"outcome": "sent", "change_request_id": result.external_reference, "by": ctx.user},
	)
	_persist()
	_settle(doc, client, wait=wait)


def _persist() -> None:
	"""Commit the approval and the registry's answer as soon as the push has been tried.

	The registry write cannot be rolled back, so its record (the change request id, or
	the refusal) must survive whatever happens later in the request. It also releases
	the row lock before the few seconds spent waiting for the registry's answer.
	"""
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit


def _push_failed(doc, state: str, code: str | None, message: str) -> None:
	message = (message or "").strip() or "no reason given"
	doc.db_set(
		{
			"registry_sync_state": state,
			"registry_error_code": code,
			"registry_error": message[:500],
			"status": states.REGISTRY_FAILED,
		}
	)
	audit.record(
		"profile_change_registry_result",
		entity_type=doc.doctype,
		entity_name=doc.name,
		details={"outcome": state.lower(), "error_code": code, "error": message[:200]},
	)
	frappe.logger("da_services").warning(
		"%s %s: DA Registry %s (%s): %s", DOCTYPE, doc.name, state.lower(), code or "no code", message
	)


def _settle(doc, client, *, wait: bool = True) -> None:
	"""Read the registry's answer a few times right after the create (once without ``wait``).

	The registry applies a change a few seconds after it is created; until its own
	approval stage is switched off it stays PENDING and is read again later.
	"""
	budget = float(getattr(client, "change_request_settle_seconds", 0) or 0) if wait else 0
	deadline = time.monotonic() + budget
	while True:
		if _read_back(doc, client, notify_outcome=False):
			return
		remaining = deadline - time.monotonic()
		if remaining <= 0:
			return
		time.sleep(min(READ_BACK_STEP_SECONDS, remaining))


@_one_at_a_time
def retry_registry_push(name: str, ctx: ScopeContext | None = None) -> dict:
	"""An Administrator sends a request the registry refused or could not take again."""
	ctx = ctx or require_roles(C.ROLE_ADMIN)
	if not ctx.has_any_role(C.ROLE_ADMIN):
		frappe.throw(_("Only an OAN Administrator can retry."), frappe.PermissionError)
	doc = frappe.get_doc(DOCTYPE, name, for_update=True)
	if doc.status != states.REGISTRY_FAILED:
		raise AlreadyDecided(
			_("Only a request the DA Registry did not apply can be retried ({0} is {1}).").format(
				name, STATUS_LABELS.get(doc.status, doc.status)
			)
		)
	if ctx.owns_da(doc.da_id):
		frappe.throw(_("You cannot send a change to your own DA record."), frappe.PermissionError)
	audit.record(
		"profile_change_retried",
		entity_type=doc.doctype,
		entity_name=doc.name,
		details={"attempt": int(doc.push_attempts or 0) + 1, "previous_error": doc.registry_error},
	)
	if _registry_already_has(doc):
		# The earlier attempt reached the registry although its answer was lost (timeout),
		# or the record was corrected there: a second change request would be a duplicate.
		doc.db_set({"registry_sync_state": "Accepted"}, update_modified=False)
		_mark_applied(doc, outcome="already_applied")
		_persist()
	else:
		doc.db_set({"registry_reference": None, "registry_approval": None}, update_modified=False)
		_send_to_registry(doc, ctx)
	doc.reload()
	_tell_da_outcome(doc)
	return serialize(doc, ctx)


def _registry_already_has(doc) -> bool:
	"""True when the DA's registry record already holds every value this request asks for."""
	try:
		ref = get_client().get_da(doc.da_id)
	except DARegistryError:
		return False
	return profile_fields.already_applied(doc.section, _changes(doc), ref, get_client())


@_one_at_a_time
def sync_local_after_transfer(name: str, ctx: ScopeContext | None = None) -> dict:
	"""An Administrator repeats the DA Services follow-up of an applied location change
	(the move of the DA's DA RBAC grant) after it failed."""
	ctx = ctx or require_roles(C.ROLE_ADMIN)
	if not ctx.has_any_role(C.ROLE_ADMIN):
		frappe.throw(_("Only an OAN Administrator can repeat this."), frappe.PermissionError)
	doc = frappe.get_doc(DOCTYPE, name, for_update=True)
	if not _local_update_failed(doc):
		raise AlreadyDecided(_("Change request {0} has no failed local update to repeat.").format(name))
	_follow_transfer(doc)
	doc.reload()
	return serialize(doc, ctx)


@_one_at_a_time
def close_change_request(name: str, *, note: str | None = None, ctx: ScopeContext | None = None) -> dict:
	"""An Administrator closes a request the registry did not apply; the DA may send a new one."""
	ctx = ctx or require_roles(C.ROLE_ADMIN)
	if not ctx.has_any_role(C.ROLE_ADMIN):
		frappe.throw(_("Only an OAN Administrator can close a request."), frappe.PermissionError)
	note = (note or "").strip()
	if len(note) < NOTE_MIN_LENGTH:
		frappe.throw(
			_("A note of at least {0} characters is required to close a request.").format(NOTE_MIN_LENGTH),
			frappe.ValidationError,
		)
	doc = frappe.get_doc(DOCTYPE, name, for_update=True)
	if doc.status != states.REGISTRY_FAILED:
		raise AlreadyDecided(
			_("Only a request the DA Registry did not apply can be closed ({0} is {1}).").format(
				name, STATUS_LABELS.get(doc.status, doc.status)
			)
		)
	doc.update(
		{"status": states.CLOSED, "closed_by": ctx.user, "closed_at": now_datetime(), "close_note": note}
	)
	doc.flags.ignore_permissions = True
	doc.save()
	audit.record(
		"profile_change_closed", entity_type=doc.doctype, entity_name=doc.name, details={"note": note}
	)
	_tell_da(
		doc,
		_("Your {0} change was closed; you can send a corrected request").format(doc.section.lower()),
		body=note,
		event="profile_change_closed",
	)
	return serialize(doc, ctx)


# -- registry read-back ----------------------------------------------------------------


def sync_registry_approvals(limit: int = 200) -> int:
	"""Read the registry's answer on changes DA Services has sent (scheduled, 15 minutes).

	Returns the number of rows that got a final answer. In user mode a scheduled job has
	no signed-in user and so no token: the read-back happens on the DA's, Supervisor's
	or Administrator's own calls instead.
	"""
	if str(frappe.conf.get("da_registry_auth") or "service").lower() == "user":
		return 0
	names = frappe.get_all(
		DOCTYPE,
		filters={"status": states.SENT_TO_REGISTRY, "registry_sync_state": "Accepted"},
		pluck="name",
		order_by="last_push_at asc",
		limit=limit,
	)
	if not names:
		return 0
	client = get_client()
	decided = sum(
		1 for name in names if _read_back(frappe.get_doc(DOCTYPE, name), client, notify_outcome=True)
	)
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
	return decided


def _refresh_registry_results(da_id: str) -> bool:
	"""Read the registry's answer on this DA's sent changes with the caller's own token.

	Returns True when a change was applied, so the caller can read the profile again.
	"""
	names = frappe.get_all(
		DOCTYPE,
		filters={"da_id": da_id, "status": states.SENT_TO_REGISTRY, "registry_sync_state": "Accepted"},
		pluck="name",
	)
	if not names:
		return False
	client = get_client()
	outcomes = [_read_back(frappe.get_doc(DOCTYPE, name), client, notify_outcome=True) for name in names]
	return "Approved" in outcomes


def _read_back(doc, client, *, notify_outcome: bool) -> str | None:
	"""Record the registry's answer (approval_status) on one sent change, once.

	Returns "Approved" or "Rejected" once the registry has decided, or None while it is
	still PENDING or cannot be asked right now.

	Several calls may read the same answer at the same moment (the profile screen
	loading twice, the DA and the Administrator together): only the one holding the
	request's lock records it; the others skip. What is recorded is committed here,
	because the read-back mostly runs inside GET requests, whose writes Frappe discards.
	"""
	held = None
	if frappe.db.db_type == "mariadb":
		held = f"da_services:profile_change:request:{doc.name}"
		# No waiting: whoever holds it (another read-back, a decision) is dealing with it.
		if not frappe.db.sql("select get_lock(%s, 0)", (held,))[0][0]:
			return None
	try:
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		doc.reload()
		if doc.status != states.SENT_TO_REGISTRY:
			return "Approved" if doc.status == states.APPLIED else None  # recorded a moment ago
		outcome = _record_registry_answer(doc, client, notify_outcome=notify_outcome)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		return outcome
	finally:
		_unlock(held)


def _record_registry_answer(doc, client, *, notify_outcome: bool) -> str | None:
	if not doc.registry_reference:
		_push_failed(doc, "Refused", None, "The DA Registry gave no change request id")
		outcome = "Rejected"
	else:
		try:
			outcome = client.change_request_status(doc.registry_reference)
		except DANotFound:
			_push_failed(doc, "Refused", None, "The DA Registry no longer has this change request")
			outcome = "Rejected"
		except DARegistryError:
			# Down, or this caller may not read it: try again on the next call.
			frappe.log_error(title=f"{DOCTYPE} {doc.name}: registry status check failed")
			return None
		if outcome == "Pending":
			return None
		if outcome == "Approved":
			_mark_applied(doc)
		else:
			doc.db_set("registry_approval", "Rejected", update_modified=False)
			_push_failed(doc, "Refused", None, "The DA Registry rejected the change request")
	doc.reload()
	if notify_outcome:
		_tell_da_outcome(doc, event="profile_change_registry_decided")
		if outcome != "Approved" and doc.l2_by:
			_send(
				doc.l2_by,
				doc,
				_("The DA Registry did not apply change {0} of DA {1}").format(doc.name, doc.da_id),
				body=doc.registry_error,
				event="profile_change_registry_decided",
			)
	return outcome


def _mark_applied(doc, *, outcome: str = "applied") -> None:
	doc.db_set(
		{
			"registry_approval": "Approved",
			"applied_at": now_datetime(),
			"status": states.APPLIED,
			"registry_error_code": None,
			"registry_error": None,
		}
	)
	audit.record(
		"profile_change_registry_result",
		entity_type=doc.doctype,
		entity_name=doc.name,
		details={"outcome": outcome, "change_request_id": doc.registry_reference},
	)
	if doc.section == "Location":
		_follow_transfer(doc)


def _follow_transfer(doc) -> bool:
	"""The registry moved the DA: move their DA RBAC grant, then tell the people concerned.

	The move is all or nothing. If it fails the registry change still stands (the row
	stays Applied): the failure is recorded on the row (``local_sync_state = Failed``),
	the Administrator who approved is told, and it can be repeated
	(``sync_local_after_transfer``). Kebele Assignments are left to an Administrator.
	"""
	diff = _changes(doc)
	new = {level: (diff.get(level) or {}).get("to") for level in ("region", "zone", "woreda", "kebele")}
	if not new["woreda"]:
		return True
	savepoint = "profile_transfer_followup"
	frappe.db.savepoint(savepoint)
	try:
		_move_da_grant(doc, new)
	except Exception as exc:
		frappe.db.rollback(save_point=savepoint)
		frappe.clear_messages()
		frappe.log_error(title=f"{DOCTYPE} {doc.name}: local update after the location change failed")
		doc.db_set(
			{"local_sync_state": "Failed", "local_sync_error": f"{exc.__class__.__name__}: {exc}"[:500]},
			update_modified=False,
		)
		if doc.l2_by:
			_send(
				doc.l2_by,
				doc,
				_("DA {0} moved in the registry, but their DA Services scope was not updated").format(
					doc.da_id
				),
				body=str(exc)[:300],
				event="profile_change_transfer",
			)
		return False
	doc.db_set({"local_sync_state": "Done", "local_sync_error": None}, update_modified=False)

	title = _("DA {0} has moved into your Woreda").format(doc.da_id)
	for user in _woreda_supervisors(new["woreda"]):
		_send(user, doc, title, event="profile_change_transfer")
	old_kebele = (diff.get("kebele") or {}).get("from")
	# The Administrator who approved the move reviews the Kebele Assignment (all of them
	# only when the approver is unknown, as on rows from before the two levels).
	for user in [doc.l2_by] if doc.l2_by else _administrators():
		_send(
			user,
			doc,
			_("DA {0} moved to Kebele {1}: check their Kebele Assignment").format(doc.da_id, new["kebele"]),
			body=_("Previous Kebele: {0}").format(old_kebele or "-"),
			event="profile_change_transfer",
		)
	return True


def _move_da_grant(doc, new: dict) -> None:
	"""End the DA's grant (kept as history) and start one with the new Region / Zone /
	Woreda codes today, so the new Woreda's Supervisor sees the DA from now on.
	Safe to repeat: a grant already on the new codes is left alone."""
	for name in frappe.get_all(
		"DA RBAC Assignment", filters={"role": C.ROLE_DA, "da_id": doc.da_id, "active": 1}, pluck="name"
	):
		old = frappe.get_doc("DA RBAC Assignment", name)
		if (old.region_scope, old.zone_scope, old.woreda_scope) == (
			new["region"],
			new["zone"],
			new["woreda"],
		):
			continue
		moved = frappe.copy_doc(old)
		moved.update(
			{
				"region_scope": new["region"],
				"zone_scope": new["zone"],
				"woreda_scope": new["woreda"],
				"effective_from": today(),
				"effective_to": None,
				"active": 1,
				"notes": f"Moved by profile change {doc.name}",
			}
		)
		moved.insert(ignore_permissions=True)
		if doc.l2_by:
			moved.db_set("assigned_by", doc.l2_by, update_modified=False)
		old.active = 0
		old.effective_to = max(str(old.effective_from or today()), today())
		old.notes = ((old.notes or "") + f"\nEnded by profile change {doc.name}").strip()
		old.flags.ignore_permissions = True
		old.save()


# -- who is told -------------------------------------------------------------------------


def _woreda_supervisors(woreda: str | None) -> list[str]:
	"""Users with a live Supervisor grant on this Woreda."""
	if not woreda:
		return []
	rows = frappe.get_all(
		"DA RBAC Assignment",
		filters={
			"role": C.ROLE_SUPERVISOR,
			"woreda_scope": woreda,
			"active": 1,
			"effective_from": ("<=", today()),
		},
		or_filters=[["effective_to", "is", "not set"], ["effective_to", ">=", today()]],
		pluck="user",
	)
	return sorted({u for u in rows if u})


def _administrators() -> list[str]:
	users = frappe.get_all(
		"Has Role", filters={"role": C.ROLE_ADMIN, "parenttype": "User"}, pluck="parent", distinct=True
	)
	enabled = (
		set(frappe.get_all("User", filters={"name": ("in", users), "enabled": 1}, pluck="name"))
		if users
		else set()
	)
	return sorted(enabled - {"Administrator", "Guest"})


def _send(user: str, doc, title: str, *, body: str | None = None, event: str) -> None:
	try:
		notify(
			user,
			kind="Approval",
			title=title[:140],
			body=body,
			href=f"/profile/change-requests/{doc.name}",
			event=event,
			reference_doctype=doc.doctype,
			reference_name=doc.name,
			da_id=doc.da_id,
		)
	except Exception:
		frappe.log_error(title=f"{DOCTYPE} {doc.name}: notification failed")


def _tell_administrators(doc) -> None:
	title = _("Profile change of DA {0} ({1}) waits for your approval").format(doc.da_id, doc.section)
	for user in _administrators():
		if user not in (doc.l1_by, doc.requested_by):
			_send(user, doc, title, event="profile_change_waiting")


def _tell_da(doc, title: str, *, body: str | None = None, event: str = "profile_change_decided") -> None:
	"""Tell the DA; a notification problem never undoes the decision."""
	try:
		notify_da(
			doc.da_id,
			kind="Approval",
			title=title[:140],
			body=body,
			href="/profile",
			event=event,
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(title=f"{DOCTYPE} {doc.name}: notification failed")


def _tell_da_outcome(doc, *, event: str = "profile_change_decided") -> None:
	"""What really happened to the DA's request, in one notification."""
	section = (doc.section or "").lower()
	body = doc.decision_note
	if doc.status == states.PENDING_ADMINISTRATOR:
		title = _("Your Supervisor approved your {0} change; it waits for the Administrator").format(section)
	elif doc.status == states.SENT_TO_REGISTRY:
		title = _("Your {0} change was approved; waiting for the DA Registry").format(section)
	elif doc.status == states.APPLIED:
		title = _("Your {0} change was approved and is now in the DA Registry").format(section)
	elif doc.status == states.REGISTRY_FAILED and doc.registry_sync_state == "Unavailable":
		title = _(
			"Your {0} change was approved; the DA Registry is unavailable, the Administrator will retry"
		).format(section)
		body = None
	elif doc.status == states.REGISTRY_FAILED:
		title = _("Your {0} change was approved, but the DA Registry refused it").format(section)
		body = doc.registry_error
	elif doc.status == states.REJECTED:
		title = _("Your {0} change was rejected").format(section)
	elif doc.status == states.CHANGES_REQUESTED:
		title = _("Changes requested on your {0} change").format(section)
	else:
		return
	_tell_da(doc, title, body=body, event=event)
