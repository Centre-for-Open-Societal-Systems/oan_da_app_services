"""DA self-service profile: view, request a change, Supervisor decision (FSD 3.7; data-model 4.4).

The DA Registry owns the DA record. A DA never edits it directly: each edit becomes a
`DA Profile Change Request`, the DA's Woreda Supervisor approves, rejects or sends it back,
and an approved change is pushed to the Registry (`push_profile_update`). The profile
keeps showing the Registry's values until the change is applied there.

Who may do what:
- Development Agent: view own profile, submit / cancel own requests, list own requests.
- Supervisor: list and decide requests of DAs in their Woreda (never their own).
- Executive: read requests in their Region.
- OAN Administrator: everything.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

from da_services.da_services.doctype.da_profile_change_request.da_profile_change_request import OPEN_STATES
from da_services.integrations.da_registry.client import (
	DARegistryContractPending,
	DARegistryError,
	DARegistryUnavailable,
	get_client,
)
from da_services.integrations.da_registry.schemas import ProfileUpdate
from da_services.services import constants as C
from da_services.services.da_lookup import get_da_reference
from da_services.services.notifications import notify_da
from da_services.utils import audit
from da_services.utils.permissions import ScopeContext, require_roles

READ_ROLES = (C.ROLE_DA, C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE, C.ROLE_ADMIN)
DECIDE_ROLES = (C.ROLE_SUPERVISOR, C.ROLE_ADMIN)
DECISIONS = {"Approve": "Approved", "Reject": "Rejected", "Request changes": "Changes requested"}

# Portal field -> (section, registry field). Everything else on the profile is locked:
# name, date of birth, gender and ID are verified through Fayda and change only through
# the Registry's own re-verification.
EDITABLE = {
	"mobile": ("Contact", "phone"),
	"email": ("Contact", "email"),
	"language": ("Contact", "language"),
	"photo": ("Contact", "photo_url"),
	"education_tier": ("Qualifications", "education_tier"),
	"specialisation": ("Qualifications", "specialisation"),
}
LOCKED = ("full_name", "first_name", "last_name", "date_of_birth", "dob", "gender", "fayda_id", "id_number")
LANGUAGES = ("en", "am", "om", "ti")
EDUCATION_TIERS = ("Certificate", "Diploma", "Degree", "Masters")

FIELDS = [
	"name",
	"da_id",
	"section",
	"status",
	"requested_by",
	"reason",
	"changes",
	"region",
	"zone",
	"woreda",
	"approver",
	"decided_at",
	"decision_note",
	"registry_sync_state",
	"registry_reference",
	"registry_error",
	"applied_at",
	"client_op_id",
	"creation",
	"modified",
]


def serialize(row) -> dict:
	out = {f: row.get(f) for f in FIELDS}
	if isinstance(out.get("changes"), str):
		out["changes"] = frappe.parse_json(out["changes"])
	for f in ("decided_at", "applied_at", "creation", "modified"):
		if out.get(f) is not None and hasattr(out[f], "isoformat"):
			out[f] = out[f].isoformat()
	return out


def _own_da_id(ctx: ScopeContext) -> str:
	own = sorted(ctx.da_ids)
	if not own:
		frappe.throw(_("Your account is not bound to a DA-ID."), frappe.PermissionError)
	return own[0]


# -- view ----------------------------------------------------------------------------


def my_profile(ctx: ScopeContext | None = None) -> dict:
	"""The DA's profile as the Registry holds it, plus open change requests."""
	ctx = ctx or require_roles(C.ROLE_DA)
	da_id = _own_da_id(ctx)
	ref = get_da_reference(da_id, ctx)
	profile = {
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
	}
	pending = frappe.get_all(
		"DA Profile Change Request",
		filters={"da_id": da_id, "status": ("in", list(OPEN_STATES))},
		fields=FIELDS,
		order_by="creation desc",
	)
	return {
		"profile": profile,
		"editable_fields": sorted(EDITABLE),
		"locked_fields": list(LOCKED),
		"pending_requests": [serialize(r) for r in pending],
	}


# -- submit / cancel -----------------------------------------------------------------


def _validate_changes(changes: dict) -> str:
	"""Check field names and values; return the one section the change belongs to."""
	if not isinstance(changes, dict) or not changes:
		frappe.throw(_("changes must be a non-empty object."), frappe.ValidationError)
	locked = sorted(set(changes) & set(LOCKED))
	if locked:
		frappe.throw(
			_("These fields are verified through Fayda and cannot be changed here: {0}").format(
				", ".join(locked)
			),
			frappe.ValidationError,
		)
	unknown = sorted(set(changes) - set(EDITABLE))
	if unknown:
		frappe.throw(_("Unknown field(s): {0}").format(", ".join(unknown)), frappe.ValidationError)
	sections = {EDITABLE[f][0] for f in changes}
	if len(sections) > 1:
		frappe.throw(
			_("Send Contact and Qualifications changes as separate requests."), frappe.ValidationError
		)
	for field, value in changes.items():
		value = (str(value) if value is not None else "").strip()
		if not value:
			frappe.throw(_("{0} cannot be empty.").format(field), frappe.ValidationError)
		if len(value) > 140:
			frappe.throw(_("{0} is too long.").format(field), frappe.ValidationError)
		if field == "email" and not frappe.utils.validate_email_address(value):
			frappe.throw(_("email is not a valid address."), frappe.ValidationError)
		if field == "mobile" and not frappe.utils.validate_phone_number(value, throw=False):
			frappe.throw(_("mobile is not a valid phone number."), frappe.ValidationError)
		if field == "language" and value not in LANGUAGES:
			frappe.throw(
				_("language must be one of {0}.").format(", ".join(LANGUAGES)), frappe.ValidationError
			)
		if field == "education_tier" and value not in EDUCATION_TIERS:
			frappe.throw(
				_("education_tier must be one of {0}.").format(", ".join(EDUCATION_TIERS)),
				frappe.ValidationError,
			)
	return sections.pop()


def submit_change_request(
	*,
	changes: dict,
	reason: str | None = None,
	client_op_id: str | None = None,
	ctx: ScopeContext | None = None,
) -> dict:
	"""A DA asks to change their own profile; goes to their Woreda Supervisor."""
	ctx = ctx or require_roles(C.ROLE_DA)
	if not ctx.has_any_role(C.ROLE_DA):
		frappe.throw(
			_("Only a Development Agent can request a change to their own profile."), frappe.PermissionError
		)
	client_op_id = (client_op_id or "").strip() or None
	if client_op_id:
		existing = frappe.db.get_value("DA Profile Change Request", {"client_op_id": client_op_id}, "name")
		if existing:
			return serialize(frappe.get_doc("DA Profile Change Request", existing))

	section = _validate_changes(changes)
	da_id = _own_da_id(ctx)
	ref = get_da_reference(da_id, ctx)

	if frappe.db.exists(
		"DA Profile Change Request", {"da_id": da_id, "section": section, "status": ("in", list(OPEN_STATES))}
	):
		frappe.throw(
			_("You already have an open {0} change request. Cancel it or wait for the decision.").format(
				section
			),
			frappe.ValidationError,
		)

	current = {
		"mobile": ref.phone,
		"email": ref.email,
		"language": ref.language,
		"photo": ref.photo_url,
		"education_tier": ref.education_tier,
		"specialisation": ref.specialisation,
	}
	diff = {
		field: {"from": current.get(field), "to": str(value).strip()}
		for field, value in changes.items()
		if str(value).strip() != (current.get(field) or "")
	}
	if not diff:
		frappe.throw(
			_("Nothing to change: the new values match your current profile."), frappe.ValidationError
		)

	doc = frappe.get_doc(
		{
			"doctype": "DA Profile Change Request",
			"da_id": da_id,
			"section": section,
			"status": "Pending",
			"requested_by": ctx.user,
			"reason": (reason or "").strip() or None,
			"changes": frappe.as_json(diff),
			"region": ref.region,
			"zone": ref.zone,
			"woreda": ref.woreda,
			"client_op_id": client_op_id,
		}
	)
	doc.insert(ignore_permissions=True)
	audit.record("profile_change_requested", entity_type=doc.doctype, entity_name=doc.name, details=diff)
	return serialize(doc)


def cancel_change_request(name: str, ctx: ScopeContext | None = None) -> dict:
	ctx = ctx or require_roles(C.ROLE_DA)
	doc = frappe.get_doc("DA Profile Change Request", name)
	if not ctx.owns_da(doc.da_id):
		frappe.throw(_("You can only cancel your own change requests."), frappe.PermissionError)
	if doc.status != "Pending":
		frappe.throw(_("Only a Pending request can be cancelled."), frappe.ValidationError)
	doc.status = "Cancelled"
	doc.flags.ignore_permissions = True
	doc.save()
	return serialize(doc)


# -- list / get ----------------------------------------------------------------------


def _check_read(doc, ctx: ScopeContext) -> None:
	if ctx.unrestricted:
		return
	if ctx.has_any_role(C.ROLE_DA) and ctx.owns_da(doc.da_id):
		return
	if ctx.has_any_role(C.ROLE_SUPERVISOR, C.ROLE_EXECUTIVE) and ctx.covers(doc.region, doc.woreda, doc.zone):
		return
	frappe.throw(_("Change request {0} is outside your scope.").format(doc.name), frappe.PermissionError)


def list_change_requests(
	ctx: ScopeContext | None = None,
	*,
	status: str | None = None,
	da_id: str | None = None,
	limit: int = 50,
	start: int = 0,
) -> tuple[list[dict], int]:
	"""DA: own. Supervisor / Executive: their Woreda / Region (scope via the query hook)."""
	ctx = ctx or require_roles(*READ_ROLES)
	filters: dict = {}
	if status:
		filters["status"] = status
	if da_id:
		filters["da_id"] = da_id
	limit = max(1, min(int(limit), 500))
	start = max(0, int(start))
	rows = frappe.get_list(
		"DA Profile Change Request",
		filters=filters,
		fields=FIELDS,
		order_by="creation desc",
		limit_page_length=limit,
		limit_start=start,
	)
	total = len(
		frappe.get_list("DA Profile Change Request", filters=filters, pluck="name", limit_page_length=0)
	)
	return [serialize(r) for r in rows], total


def get_change_request(name: str, ctx: ScopeContext | None = None) -> dict:
	ctx = ctx or require_roles(*READ_ROLES)
	doc = frappe.get_doc("DA Profile Change Request", name)
	_check_read(doc, ctx)
	return serialize(doc)


# -- decide --------------------------------------------------------------------------


def decide(name: str, *, decision: str, note: str | None = None, ctx: ScopeContext | None = None) -> dict:
	"""Supervisor of the DA's Woreda (or Admin) approves, rejects or sends the request back."""
	ctx = ctx or require_roles(*DECIDE_ROLES)
	if decision not in DECISIONS:
		frappe.throw(_("decision must be one of {0}.").format(", ".join(DECISIONS)), frappe.ValidationError)
	note = (note or "").strip()
	if decision != "Approve" and len(note) < 5:
		frappe.throw(_("A note of at least 5 characters is required to reject or request changes."))

	doc = frappe.get_doc("DA Profile Change Request", name)
	if not ctx.unrestricted:
		if not ctx.has_any_role(C.ROLE_SUPERVISOR) or not ctx.covers(doc.region, doc.woreda, doc.zone):
			frappe.throw(_("Change request {0} is outside your Woreda.").format(name), frappe.PermissionError)
	if ctx.owns_da(doc.da_id) or doc.requested_by == ctx.user:
		frappe.throw(_("You cannot decide your own change request."), frappe.PermissionError)
	if doc.status != "Pending":
		frappe.throw(_("Change request {0} is already {1}.").format(name, doc.status), frappe.ValidationError)

	doc.status = DECISIONS[decision]
	doc.approver = ctx.user
	doc.decided_at = now_datetime()
	doc.decision_note = note or None
	doc.flags.ignore_permissions = True
	doc.save()
	audit.record(
		"profile_change_decided",
		entity_type=doc.doctype,
		entity_name=doc.name,
		details={"decision": decision, "note": note},
	)

	if doc.status == "Approved":
		_apply(doc)

	_notify(doc)
	doc.reload()
	return serialize(doc)


def _apply(doc) -> None:
	"""Push the approved change to the DA Registry; record the result on the row."""
	changes = frappe.parse_json(doc.changes) if isinstance(doc.changes, str) else doc.changes
	update = ProfileUpdate(
		da_id=doc.da_id,
		changes={EDITABLE[field][1]: value["to"] for field, value in changes.items()},
		approver=doc.approver,
		reference=doc.name,
	)
	try:
		result = get_client().push_profile_update(update)
	except (DARegistryUnavailable, DARegistryContractPending, DARegistryError) as exc:
		doc.db_set("registry_sync_state", "Failed", update_modified=False)
		doc.db_set("registry_error", f"{exc.__class__.__name__}: {exc}"[:500], update_modified=False)
		return
	if not result.accepted:
		doc.db_set("registry_sync_state", "Failed", update_modified=False)
		doc.db_set("registry_error", (result.message or "rejected")[:500], update_modified=False)
		return
	doc.db_set("registry_sync_state", "Accepted", update_modified=False)
	doc.db_set("registry_reference", result.external_reference, update_modified=False)
	doc.db_set("applied_at", now_datetime(), update_modified=False)
	doc.db_set("status", "Applied", update_modified=True)


def _notify(doc) -> None:
	"""Tell the DA the outcome; a notification problem never undoes the decision."""
	titles = {
		"Applied": _("Your {0} update was approved"),
		"Approved": _("Your {0} update was approved; the registry update is pending"),
		"Rejected": _("Your {0} update was rejected"),
		"Changes requested": _("Changes requested on your {0} update"),
	}
	try:
		notify_da(
			doc.da_id,
			kind="Approval",
			title=titles.get(doc.status, _("Your {0} update changed")).format(doc.section.lower())[:140],
			body=doc.decision_note,
			href="/profile",
			event="profile_change_decided",
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(title=f"DA Profile Change Request {doc.name}: notification failed")
