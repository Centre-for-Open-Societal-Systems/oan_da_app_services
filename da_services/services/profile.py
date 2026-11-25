"""DA profile changes: request, two approvals in DA Services, then the DA Registry (FSD 3.7).

The DA Registry owns the DA record. Nobody edits it from DA Services directly: each edit
is a `DA Profile Change Request`.

1. A Supervisor asks for a change on a DA of their Woreda.
2. Level 1: a Supervisor of the DA's Woreda approves, rejects or asks for changes. A
   Supervisor's own request has its level 1 already (the Supervisor who sent it).
3. Level 2: an OAN Administrator approves or rejects; never the person who gave level 1.
4. On the level-2 approval, in the Administrator's own request, the change is sent to the
   registry as one change request, with the Administrator's token (user mode). The
   registry applies it without an approval of its own (or, until that is switched off,
   holds it at its own stages); DA Services reads the answer back.

No registry write is ever made in a DA's or a Supervisor's request: with the planned
registry roles their tokens can read only. Nobody decides a request they sent.

Who may do what:
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
from da_services.services import profile_fields, profile_lists
from da_services.services.da_lookup import get_da_reference
from da_services.services.notifications import notify, notify_da
from da_services.utils import audit
from da_services.utils.permissions import require_permission

DOCTYPE = "DA Profile Change Request"
DECISIONS = {"Approve": "Approved", "Reject": "Rejected", "Request changes": "Changes requested"}
LEVEL2_DECISIONS = ("Approve", "Reject")
NOTE_MIN_LENGTH = 5
READ_BACK_STEP_SECONDS = 2


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


def serialize(row, names: dict[str, str] | None = None) -> dict:
	"""One change request as the API answers it, for the session user.

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
	out.update(_actions(out))
	return out


def serialize_many(rows) -> list[dict]:
	names = _full_names(rows)
	return [serialize(row, names) for row in rows]


def _actions(row: dict) -> dict:
	"""What this caller may do with the request now, by the same rules the actions apply."""
	admin = _administers()
	failed = row.get("status") == states.REGISTRY_FAILED
	allowed = _allowed_decisions(row)
	return {
		"can_decide": bool(allowed),
		"allowed_decisions": allowed,
		"can_cancel": (
			row.get("requested_by") == frappe.session.user and row.get("status") in states.PENDING_STATES
		),
		"can_retry": admin and failed,
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


def _administers() -> bool:
	"""The Administrator level (level 2, retry, close): `write` on the request doctype."""
	return frappe.has_permission(DOCTYPE, "write", user=frappe.session.user)


def _named_da(target: str) -> DAReference:
	"""The DA named; a DA-ID is required."""
	if not target:
		frappe.throw(_("da_id is required."), frappe.ValidationError)
	return get_da_reference(target)


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
		# DA Registry #66: view only here; changes to these lists come later.
		"qualifications": ref.qualifications,
		"family_members": ref.dependents,
		# The earlier name of family_members, kept until the portal reads the new one.
		"dependents": ref.dependents,
		# The main education record's level and passout year, as the registry copies them
		# onto the DA record: read only, changed through the education records.
		"education_tier": ref.education_tier,
		"passout_year": ref.passout_year,
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
		"home_address": ref.home_address,
		"position": ref.position,
		"position_code": ref.position_code,
		"reports_to": ref.reports_to,
		"country": ref.country,
		"region_name": ref.region_name,
		"zone_name": ref.zone_name,
		"woreda_name": ref.woreda_name,
		"kebele_name": ref.kebele_name,
		# The record itself: the registry's id and status, its last approval and its origin
		"record_id": ref.record_id,
		"record_status": ref.record_status,
		"last_approved_at": ref.last_approved_at,
		"last_approved_by": ref.last_approved_by,
		"source": ref.source,
	}


def _reports_to_person(da_id: str | None) -> tuple[str | None, str | None]:
	"""(name, position) of the person a DA reports to, read from the registry by DA-ID.

	None, None when there is none or the registry cannot say now: the DA-ID is still shown.
	"""
	if not da_id:
		return None, None
	try:
		boss = get_client().get_da(da_id)
	except DARegistryError:
		return None, None
	return boss.full_name or None, boss.position


def _document_urls(document_ids: list[str]) -> dict[str, str]:
	"""Short-lived links for several documents in one registry call, {document_id: url}.

	Empty while the registry cannot give them: the profile still shows, and
	GET /profile/documents/<id> gives one link later.
	"""
	if not document_ids:
		return {}
	try:
		return get_client().document_links(document_ids)
	except DARegistryError:
		return {}


def _profile_view(ref: DAReference, *, certificate_links: bool = False, own: bool = False) -> dict:
	"""``certificate_links`` puts a short-lived ``url`` on every certificate (None when the
	registry cannot give it now). ``own``: the DA reads their own record, so the fields only
	their Supervisor may ask to change are ``locked_fields``, not ``editable_fields``."""
	open_rows = frappe.get_all(
		DOCTYPE,
		filters={"da_id": ref.da_id, "status": ("in", list(OPEN_STATES))},
		fields=FIELDS,
		order_by="creation desc",
	)
	profile = _profile(ref)
	# The record answers the photo's link itself; only without one is it asked for, in
	# the same call as the certificates.
	wanted = [ref.photo_document_id] if not profile["photo"] and ref.photo_document_id else []
	if certificate_links:
		wanted += [f["document_id"] for q in ref.qualifications for f in q.get("certificates") or []]
	links = _document_urls(wanted)
	if not profile["photo"] and ref.photo_document_id:
		profile["photo"] = links.get(ref.photo_document_id)
	if certificate_links:
		profile["qualifications"] = [
			{
				**q,
				"certificates": [
					{**f, "url": links.get(f["document_id"])} for f in q.get("certificates") or []
				],
			}
			for q in ref.qualifications
		]
	profile["reports_to_name"], profile["reports_to_position"] = _reports_to_person(ref.reports_to)
	fields = profile_fields.catalog(ref, get_client())
	if "reports_to" in fields:
		fields["reports_to"]["current_label"] = profile["reports_to_name"]
	editable, locked = profile_fields.editable_fields(own=own)
	return {
		"profile": profile,
		"editable_fields": editable,
		"fields": fields,
		"note_min_length": profile_fields.NOTE_MIN_LENGTH,
		"list_options": profile_lists.form_options(get_client()),
		"locked_fields": locked,
		"pending_requests": serialize_many(open_rows),
	}


def _own_da() -> DAReference:
	"""The signed-in user's own DA record, read from the registry.

	Who the caller is comes from their User alone: ``da_registry_id``, which IAM fills
	from the ``da_id`` claim at sign-in. Nothing the caller sends is used, so a DA can only
	reach their own record. The claim carries the registry's own id of the record (read
	directly, one call); a DA-ID (``DA-...``), as a User set by hand may hold, is looked
	up instead. 404 for a user with no such id (an account that is not a DA) and for an
	id the registry does not know.
	"""
	own_id = (frappe.db.get_value("User", frappe.session.user, "da_registry_id") or "").strip()
	if not own_id:
		raise frappe.DoesNotExistError(_("Your account is not linked to a DA Registry record."))
	client = get_client()
	try:
		if own_id.upper().startswith("DA-"):
			return client.get_da(own_id)
		return client.get_da_by_record_id(own_id)
	except DANotFound:
		raise frappe.DoesNotExistError(_("Your record was not found in the DA Registry.")) from None
	except DARegistryUnavailable:
		frappe.throw(_("The DA Registry is unavailable. Try again shortly."), frappe.ValidationError)


def my_profile() -> dict:
	"""The signed-in DA's own profile, as the DA Registry holds it now, with open requests.

	``validation`` says what the registry answered for the caller's record: it was found
	(or this call is a 404), its status, and whether it is in service. Every certificate
	carries a short-lived ``url``.
	"""
	ref = _own_da()
	if _refresh_registry_results(ref.da_id):
		ref = _own_da()
	out = _profile_view(ref, certificate_links=True, own=True)
	out["validation"] = {
		"record_found": True,
		"record_status": ref.record_status,
		"is_active": ref.record_status == "ACTIVE",
	}
	return out


def agent_profile(da_id: str) -> dict:
	"""A DA's profile for a Supervisor, Executive or Administrator in scope, with open requests.

	``can_request`` says whether this caller may send a change request for the DA: only
	a Supervisor of the DA's Woreda may.
	"""
	ref = get_da_reference(da_id)  # scope: the registry's own posting of the DA
	if _refresh_registry_results(ref.da_id):
		ref = get_da_reference(da_id)
	out = _profile_view(ref)
	out["can_request"] = _supervises(ref.woreda)
	return out


def _documents_of(ref: DAReference) -> dict[str, str | None]:
	"""The registry documents on a DA's record, {document_id: file name}."""
	docs: dict[str, str | None] = {}
	if ref.photo_document_id:
		docs[ref.photo_document_id] = "photo"
	for record in ref.qualifications:
		for f in record.get("certificates") or []:
			docs[f["document_id"]] = f.get("name")
	return docs


def document_link(document_id: str, *, da_id: str | None = None) -> dict:
	"""A short-lived link to one of a DA's registry documents: a certificate or the photo.

	The caller names the DA (``da_id``), or names none for their own record. Registry
	documents are not scoped. Only a document on that DA's record opens, so a guessed id
	leads nowhere.
	"""
	target = (da_id or "").strip()
	ref = _named_da(target) if target else _own_da()
	names = _documents_of(ref)
	if document_id not in names:
		raise frappe.DoesNotExistError(_("DA {0} has no document {1}.").format(ref.da_id, document_id))
	try:
		url = get_client().document_links([document_id]).get(document_id)
	except DARegistryUnavailable:
		frappe.throw(_("The DA Registry is unavailable. Try again shortly."), frappe.ValidationError)
	if not url:
		frappe.throw(_("The document cannot be opened right now. Try again shortly."), frappe.ValidationError)
	return {"da_id": ref.da_id, "document_id": document_id, "name": names[document_id], "url": url}


def _supervises(woreda: str | None) -> bool:
	"""Level-1 decider for this Woreda: a User Permission on exactly this DA Woreda.
	Explicit, not "no User Permission at all": a user with none (an Administrator) would
	otherwise cover every Woreda and could give level 1 in place of the Supervisor."""
	if not woreda:
		return False
	allowed = frappe.permissions.get_user_permissions(frappe.session.user).get("DA Woreda", [])
	return any(row.get("doc") == woreda for row in allowed)


# -- submit / cancel -----------------------------------------------------------------


def submit_change_request(
	*,
	changes: dict,
	reason: str | None = None,
	client_op_id: str | None = None,
	da_id: str | None = None,
) -> dict:
	"""A Supervisor asks for a change on a DA of their Woreda (``da_id``)."""
	require_permission("DA Profile Change Request", "create")
	client_op_id = (client_op_id or "").strip() or None
	if client_op_id:
		existing = frappe.db.get_value(DOCTYPE, {"client_op_id": client_op_id}, "name")
		if existing:
			return serialize(frappe.get_doc(DOCTYPE, existing))

	ref, as_supervisor = _target(da_id)
	section, diff = profile_fields.validate_changes(
		changes, ref=ref, reason=reason, client=get_client(), as_supervisor=as_supervisor
	)
	return _submit(ref, as_supervisor, section, diff, reason, client_op_id)


def _target(da_id: str | None) -> tuple[DAReference, bool]:
	"""(the DA a request is about, True: a Supervisor asks), checked for scope.

	The caller names the DA and must supervise their Woreda (``_supervises``).
	"""
	ref = _named_da((da_id or "").strip())
	if not _supervises(ref.woreda):
		frappe.throw(_("DA {0} is not posted in your Woreda.").format(ref.da_id), frappe.PermissionError)
	if _refresh_registry_results(ref.da_id):
		ref = get_da_reference(ref.da_id)  # an earlier change just landed: diff against it
	return ref, True


def _submit(
	ref: DAReference,
	as_supervisor: bool,
	section: str,
	diff: dict,
	reason: str | None,
	client_op_id: str | None,
	on_created=None,
) -> dict:
	"""Store a checked request. ``on_created(doc)`` adds what belongs to it (a file)."""
	# A transaction reads the database as it was when it began: start a new one, so the
	# check below sees a request another call has just committed.
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
	if client_op_id:
		existing = frappe.db.get_value(DOCTYPE, {"client_op_id": client_op_id}, "name")
		if existing:
			return serialize(frappe.get_doc(DOCTYPE, existing))  # the same operation, sent twice
	out = _create_request(ref, section, diff, reason, client_op_id, as_supervisor, on_created)
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
	return out


def _submitter(client_op_id: str | None) -> tuple[str | None, dict | None]:
	"""(client_op_id, the request it already made with that id or None)."""
	require_permission("DA Profile Change Request", "create")
	client_op_id = (client_op_id or "").strip() or None
	if client_op_id:
		existing = frappe.db.get_value(DOCTYPE, {"client_op_id": client_op_id}, "name")
		if existing:
			return client_op_id, serialize(frappe.get_doc(DOCTYPE, existing))
	return client_op_id, None


# -- lists, certificates, photo (DA Registry #66; services/profile_lists.py) ---------------


def submit_record_change(
	*,
	action: str,
	record_id: str | None = None,
	values: dict | None = None,
	reason: str | None = None,
	client_op_id: str | None = None,
	da_id: str | None = None,
) -> dict:
	"""Add, change or remove one of a DA's Education & Professional records."""
	client_op_id, done = _submitter(client_op_id)
	if done:
		return done
	ref, as_supervisor = _target(da_id)
	section, diff = profile_lists.record_change(
		ref, get_client(), action=action, record_id=record_id, values=values or {}
	)
	return _submit(ref, as_supervisor, section, diff, reason, client_op_id)


def submit_family_change(
	*,
	action: str,
	member_id: str | None = None,
	values: dict | None = None,
	reason: str | None = None,
	client_op_id: str | None = None,
	da_id: str | None = None,
) -> dict:
	"""Add, change or remove one of a DA's family members."""
	client_op_id, done = _submitter(client_op_id)
	if done:
		return done
	ref, as_supervisor = _target(da_id)
	section, diff = profile_lists.member_change(
		ref, get_client(), action=action, member_id=member_id, values=values or {}
	)
	return _submit(ref, as_supervisor, section, diff, reason, client_op_id)


def submit_certificate(
	*,
	record_id: str | None,
	filename: str | None,
	content: bytes | None,
	title: str | None = None,
	issuer: str | None = None,
	issued_on: str | None = None,
	expires_on: str | None = None,
	replaces: str | None = None,
	reason: str | None = None,
	client_op_id: str | None = None,
	da_id: str | None = None,
) -> dict:
	"""Add a certificate to an Education & Professional record, or a new file in place of one.

	The file stays in DA Services, private, until the request is approved at both levels;
	the certificate counts as verified once the registry holds it.
	"""
	client_op_id, done = _submitter(client_op_id)
	if done:
		return done
	file_name, _type = profile_lists.check_file(filename, content, photo=False)
	ref, as_supervisor = _target(da_id)
	section, diff = profile_lists.certificate_change(
		ref,
		record_id=record_id,
		file_name=file_name,
		title=title,
		issuer=issuer,
		issued_on=issued_on,
		expires_on=expires_on,
		replaces=replaces,
	)
	entry = next(iter(diff.values()))

	def keep(doc):
		profile_lists.attach_file(doc, file_name, content)
		profile_lists.save_certificate(doc, entry)

	return _submit(ref, as_supervisor, section, diff, reason, client_op_id, keep)


def submit_certificate_removal(
	*,
	record_id: str | None,
	document_id: str | None,
	reason: str | None = None,
	client_op_id: str | None = None,
	da_id: str | None = None,
) -> dict:
	"""Take a certificate off an Education & Professional record (the registry keeps the file)."""
	client_op_id, done = _submitter(client_op_id)
	if done:
		return done
	ref, as_supervisor = _target(da_id)
	section, diff = profile_lists.certificate_removal(ref, record_id=record_id, document_id=document_id)
	return _submit(ref, as_supervisor, section, diff, reason, client_op_id)


def submit_photo(
	*,
	filename: str | None = None,
	content: bytes | None = None,
	remove: bool = False,
	reason: str | None = None,
	client_op_id: str | None = None,
	da_id: str | None = None,
) -> dict:
	"""Set the DA's photo (a new file) or, with ``remove``, take it off."""
	client_op_id, done = _submitter(client_op_id)
	if done:
		return done
	file_name = None
	if not remove:
		file_name, _type = profile_lists.check_file(filename, content, photo=True)
	ref, as_supervisor = _target(da_id)
	section, diff = profile_lists.photo_change(ref, file_name=file_name)

	def keep(doc):
		if file_name:
			profile_lists.attach_file(doc, file_name, content)

	return _submit(ref, as_supervisor, section, diff, reason, client_op_id, keep)


CERTIFICATE_STATUS = {
	states.PENDING_SUPERVISOR: "Pending verification",
	states.PENDING_ADMINISTRATOR: "Pending verification",
	states.SENT_TO_REGISTRY: "Pending verification",
	states.REGISTRY_FAILED: "Pending verification",
	states.REJECTED: "Rejected",
	# The Supervisor asked the DA for another file: the DA sends a new request.
	states.CHANGES_REQUESTED: "Changes requested",
}


def list_certificates(da_id: str | None = None) -> list[dict]:
	"""A DA's certificates: the ones the registry holds (verified), each with the details DA
	Services keeps, then the ones still waiting for verification or rejected."""
	require_permission("DA Certificate", "read")
	target = (da_id or "").strip()
	ref = _named_da(target)
	ours = frappe.get_all(
		profile_lists.CERTIFICATE,
		filters={"da_id": ref.da_id},
		fields=[
			"name",
			"record_id",
			"title",
			"issuer",
			"issued_on",
			"expires_on",
			"file_name",
			"change_request",
			"registry_document_id",
			"replaces_document_id",
		],
		order_by="creation desc",
	)
	by_document = {c.registry_document_id: c for c in ours if c.registry_document_id}
	statuses = dict(
		frappe.get_all(
			DOCTYPE,
			filters={"name": ("in", [c.change_request for c in ours] or [""])},
			fields=["name", "status"],
			as_list=True,
		)
	)

	def details(c) -> dict:
		if not c:
			return {"title": None, "issuer": None, "issued_on": None, "expires_on": None}
		return {
			"title": c.title,
			"issuer": c.issuer,
			"issued_on": str(c.issued_on) if c.issued_on else None,
			"expires_on": str(c.expires_on) if c.expires_on else None,
		}

	out = []
	for record in ref.qualifications:
		for f in record.get("certificates") or []:
			c = by_document.get(f["document_id"])
			out.append(
				{
					"document_id": f["document_id"],
					"file_name": f.get("name"),
					"record_id": record.get("id"),
					"status": "Verified",
					"change_request": c.change_request if c else None,
					**details(c),
				}
			)
	for c in ours:
		status = CERTIFICATE_STATUS.get(statuses.get(c.change_request))
		if not status:
			continue  # verified (listed above), withdrawn or closed
		out.append(
			{
				"document_id": None,
				"file_name": c.file_name,
				"record_id": c.record_id,
				"status": status,
				"change_request": c.change_request,
				"replaces_document_id": c.replaces_document_id,
				**details(c),
			}
		)
	return out


def request_file(name: str) -> tuple[str, bytes, str]:
	"""(file name, bytes, content type) of the file a certificate or photo request carries,
	for whoever may read the request (to look at it before deciding)."""
	require_permission("DA Profile Change Request", "read")
	doc = frappe.get_doc(DOCTYPE, name)
	_check_read(doc)
	f = profile_lists.request_file(doc) if doc.section in profile_lists.FILE_SECTIONS else None
	if not f:
		raise frappe.DoesNotExistError(_("Change request {0} carries no file.").format(name))
	return f.file_name, _bytes(f), _content_type(f.file_name)


def _bytes(f) -> bytes:
	"""The stored file as bytes: Frappe decodes a file that reads as text unless told not to."""
	content = f.get_content(encodings=[])
	return content.encode() if isinstance(content, str) else content


def _content_type(file_name: str) -> str:
	extension = file_name[file_name.rfind(".") :].lower() if "." in file_name else ""
	return {**profile_lists.CERTIFICATE_TYPES, **profile_lists.PHOTO_TYPES}.get(
		extension, "application/octet-stream"
	)


def _push_list_change(doc, client):
	"""Upload the request's file (once: a retry reuses the id), then the change request."""
	ref = client.get_da(doc.da_id)
	entry = next(iter(_changes(doc).values()))
	document_id = doc.get("uploaded_document_id")
	if (
		doc.section in profile_lists.FILE_SECTIONS
		and entry.get("action") in ("add", "replace", "set")
		and not document_id
	):
		f = profile_lists.request_file(doc)
		if not f:
			raise profile_fields.InvalidProfileChange(_("The file of this request is missing."))
		document_id = client.upload_document(f.file_name, _bytes(f), _content_type(f.file_name))
		doc.db_set("uploaded_document_id", document_id, update_modified=False)
	change = profile_lists.registry_change(doc, ref, document_id=document_id, approver=frappe.session.user)
	return client.push_section_change(change)


def _in_own_transaction(action):
	"""Run an action on one change request (decide, cancel, retry, close ...) in its own
	transaction: it starts a new one, so it reads what an earlier call committed, and
	commits what it did before returning.
	"""

	@functools.wraps(action)
	def wrapper(name: str, *args, **kwargs):
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		out = action(name, *args, **kwargs)
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
		return out

	return wrapper


def _create_request(
	ref: DAReference,
	section: str,
	diff: dict,
	reason: str | None,
	client_op_id: str | None,
	as_supervisor: bool,
	on_created=None,
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
			"requested_by": frappe.session.user,
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
		doc.update({"l1_by": frappe.session.user, "l1_at": now, "l1_note": _("Requested by the Supervisor")})
	doc.insert(ignore_permissions=True)
	if on_created:
		on_created(doc)
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
			if user != frappe.session.user:
				_send(user, doc, label, event="profile_change_waiting")
	else:
		frappe.log_error(
			title=f"{DOCTYPE} {doc.name}: no Supervisor for Woreda {doc.woreda}",
			message=f"No active Supervisor assignment has woreda_scope {doc.woreda!r}; the request waits.",
		)
	return serialize(doc)


@_in_own_transaction
def cancel_change_request(name: str) -> dict:
	"""Whoever sent a request (the DA, or the Supervisor) withdraws it while it waits for a decision.

	A DA cannot cancel a request their Supervisor sent about them.
	"""
	require_permission("DA Profile Change Request", "create")
	doc = frappe.get_doc(DOCTYPE, name, for_update=True)
	if doc.requested_by != frappe.session.user:
		frappe.throw(_("You can only cancel a change request you sent."), frappe.PermissionError)
	if doc.status not in states.PENDING_STATES:
		raise AlreadyDecided(_("Only a request waiting for a decision can be cancelled."))
	doc.status = states.CANCELLED
	doc.flags.ignore_permissions = True
	doc.save()
	audit.record("profile_change_cancelled", entity_type=doc.doctype, entity_name=doc.name, details={})
	return serialize(doc)


# -- list / get ----------------------------------------------------------------------


def _check_read(doc) -> None:
	"""Role permission and the caller's User Permissions on this request."""
	require_permission(DOCTYPE, "read", doc=doc, user=frappe.session.user)


def _many(value) -> list[str]:
	"""A filter given as a list or as comma-separated text: "Pending Administrator,Registry failed"."""
	if not value:
		return []
	items = value if isinstance(value, list | tuple) else str(value).split(",")
	return [str(v).strip() for v in items if str(v).strip()]


def list_change_requests(
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
	"""DA: own. Supervisor / Executive: their Woreda / Region (scope from their User Permissions).

	``status`` and ``woreda`` take one value or several (comma-separated); the scope
	rule still applies on top, so a Woreda outside the caller's scope lists nothing.
	``mine`` keeps the requests the caller sent (a Supervisor's own requests wait for an
	Administrator, so they are in no queue of the Supervisor's).
	``no_supervisor_found`` keeps the requests still waiting for a Supervisor in a Woreda
	that had none when they were sent: an Administrator has to assign one.
	"""
	require_permission("DA Profile Change Request", "read")
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
		filters["requested_by"] = frappe.session.user
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
	return serialize_many(rows), total


def get_change_request(name: str) -> dict:
	require_permission("DA Profile Change Request", "read")
	doc = frappe.get_doc(DOCTYPE, name)
	_check_read(doc)
	if doc.status == states.SENT_TO_REGISTRY and any(
		frappe.has_permission(DOCTYPE, ptype, user=frappe.session.user) for ptype in ("create", "write")
	):
		# Ask the registry with the caller's own token whether it has applied the change.
		_read_back(doc, get_client(), notify_outcome=True)
		doc.reload()
	return serialize(doc)


# -- decide --------------------------------------------------------------------------


def _decision_level(row) -> int:
	"""1 or 2: the approval this caller may give on the request now. Raises why not.

	Level 1 is a Supervisor of the DA's Woreda; level 2 an OAN Administrator who did
	not give level 1. Nobody decides a request about their own DA record or one they
	sent. ``decide`` acts on this and the ``can_decide`` flag reports it.
	"""
	name, status = row.get("name"), row.get("status")
	if row.get("requested_by") == frappe.session.user:
		raise frappe.PermissionError(_("You cannot decide your own change request."))
	if status == states.PENDING_SUPERVISOR:
		if not _supervises(row.get("woreda")):
			raise frappe.PermissionError(
				_("Only a Supervisor of Woreda {0} can give the first approval of {1}.").format(
					row.get("woreda"), name
				)
			)
		return 1
	if status == states.PENDING_ADMINISTRATOR:
		if not _administers():
			raise frappe.PermissionError(
				_(
					"Change request {0} was already approved by {1} and waits for an OAN Administrator."
				).format(name, row.get("l1_by"))
			)
		if row.get("l1_by") == frappe.session.user:
			raise frappe.PermissionError(
				_("You gave the first approval of {0}; another person must give the second.").format(name)
			)
		return 2
	raise AlreadyDecided(
		_("Change request {0} is already decided: {1}.").format(name, STATUS_LABELS.get(status, status))
	)


def _allowed_decisions(row) -> list[str]:
	"""The decisions this caller may give on the request now; none when they may not decide."""
	try:
		level = _decision_level(row)
	except frappe.ValidationError, frappe.PermissionError:
		return []
	return list(DECISIONS) if level == 1 else list(LEVEL2_DECISIONS)


@_in_own_transaction
def decide(
	name: str,
	*,
	decision: str,
	note: str | None = None,
	wait_for_registry: bool = True,
) -> dict:
	"""Level 1 (Woreda Supervisor) or level 2 (OAN Administrator), by the request's state.

	``wait_for_registry=False`` (bulk decisions) reads the registry's answer once right
	after the create instead of waiting a few seconds for it; a change still PENDING is
	picked up on a later call.
	"""
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
	level = _decision_level(doc)
	if level == 2 and decision not in LEVEL2_DECISIONS:
		frappe.throw(_("The Administrator can approve or reject."), frappe.ValidationError)

	now = now_datetime()
	doc.update({f"l{level}_by": frappe.session.user, f"l{level}_at": now, f"l{level}_note": note or None})
	doc.update({"approver": frappe.session.user, "decided_at": now, "decision_note": note or None})
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
		_send_to_registry(doc, wait=wait_for_registry)
		doc.reload()
	elif level == 1 and decision == "Approve":
		_tell_administrators(doc)
	_tell_da_outcome(doc)
	doc.reload()
	return serialize(doc)


# Each approval is a few registry calls: keep one request well under the gateway timeout.
BULK_LIMIT = 20


def decide_many(names: list[str], *, decision: str, note: str | None = None) -> dict:
	"""The same decision on several requests (the Approvals screen's bulk Approve / Reject).

	Each request is decided on its own, with every rule of ``decide``; one that cannot
	be decided is reported and the others go on. The registry's answer is not waited
	for: a change still PENDING shows as Sent to registry and is read back later.
	"""
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
			row = decide(name, decision=decision, note=note, wait_for_registry=False)
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


def _send_to_registry(doc, *, wait: bool = True) -> None:
	"""Create the registry change request with the Administrator's token; record the answer.

	Only an OAN Administrator's request reaches this. Nothing is retried in the
	background: a refusal or an outage leaves the row at Registry failed for the
	Administrator to retry or close.
	"""
	if not _administers() or frappe.session.user != frappe.session.user:
		frappe.throw(
			_("Only an OAN Administrator can send a change to the DA Registry."), frappe.PermissionError
		)
	client = get_client()
	doc.db_set(
		{"push_attempts": int(doc.push_attempts or 0) + 1, "last_push_at": now_datetime()},
		update_modified=False,
	)
	try:
		if doc.section in profile_lists.SECTIONS:
			result = _push_list_change(doc, client)
		else:
			changes = profile_fields.registry_changes(doc.section, _changes(doc), client)
			result = client.push_profile_update(
				ProfileUpdate(
					da_id=doc.da_id, changes=changes, approver=frappe.session.user, reference=doc.name
				)
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
		details={
			"outcome": "sent",
			"change_request_id": result.external_reference,
			"by": frappe.session.user,
		},
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


@_in_own_transaction
def retry_registry_push(name: str) -> dict:
	"""An Administrator sends a request the registry refused or could not take again."""
	require_permission("DA Profile Change Request", "write")
	doc = frappe.get_doc(DOCTYPE, name, for_update=True)
	if doc.status != states.REGISTRY_FAILED:
		raise AlreadyDecided(
			_("Only a request the DA Registry did not apply can be retried ({0} is {1}).").format(
				name, STATUS_LABELS.get(doc.status, doc.status)
			)
		)
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
		_send_to_registry(doc)
	doc.reload()
	_tell_da_outcome(doc)
	return serialize(doc)


def _registry_already_has(doc) -> bool:
	"""True when the DA's registry record already holds every value this request asks for."""
	try:
		ref = get_client().get_da(doc.da_id)
	except DARegistryError:
		return False
	if doc.section in profile_lists.SECTIONS:
		return profile_lists.already_applied(doc, ref)
	return profile_fields.already_applied(doc.section, _changes(doc), ref, get_client())


@_in_own_transaction
def sync_local_after_transfer(name: str) -> dict:
	"""An Administrator repeats the DA Services follow-up of an applied location change
	(the move of the DA's DA RBAC grant) after it failed."""
	require_permission("DA Profile Change Request", "write")
	doc = frappe.get_doc(DOCTYPE, name, for_update=True)
	if not _local_update_failed(doc):
		raise AlreadyDecided(_("Change request {0} has no failed local update to repeat.").format(name))
	_follow_transfer(doc)
	doc.reload()
	return serialize(doc)


@_in_own_transaction
def close_change_request(name: str, *, note: str | None = None) -> dict:
	"""An Administrator closes a request the registry did not apply; the DA may send a new one."""
	require_permission("DA Profile Change Request", "write")
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
		{
			"status": states.CLOSED,
			"closed_by": frappe.session.user,
			"closed_at": now_datetime(),
			"close_note": note,
		}
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
	return serialize(doc)


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

	What is recorded is committed here, because the read-back mostly runs inside GET
	requests, whose writes Frappe discards. A request another call has already recorded
	is not recorded again.
	"""
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
	doc.reload()
	if doc.status != states.SENT_TO_REGISTRY:
		return "Approved" if doc.status == states.APPLIED else None  # recorded a moment ago
	outcome = _record_registry_answer(doc, client, notify_outcome=notify_outcome)
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
	return outcome


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
	profile_lists.after_applied(doc)
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
	Safe to repeat: a grant already on the new codes is left alone.

	A grant takes its DA-ID from the User's ``da_registry_id`` and its scope must name
	DA Region / Zone / Woreda master rows: when either is missing the move fails, the
	request is flagged and the Administrator repeats it once the data is in place.
	"""
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
		if frappe.db.get_value("User", old.user, "da_registry_id") != doc.da_id:
			frappe.throw(
				_(
					"User {0} is not bound to DA-ID {1} (User.da_registry_id), so their new assignment "
					"cannot be created. Set it, then repeat the local update."
				).format(old.user, doc.da_id),
				frappe.ValidationError,
			)
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
