"""In-app notifications (data-model 5.10): the bell on the portal and mobile app.

Other services call ``notify(...)`` (or ``notify_da(...)`` when they only know the DA-ID);
the API reads a user's own rows. SMS / Telegram delivery is a later concern recorded on
the same row (channel, delivery_status) so one event can go out on several channels.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime, today

from da_services.services import constants as C
from da_services.utils import audit as audit_log
from da_services.utils.permissions import ScopeContext, require_roles

KINDS = (
	"Visit",
	"Task",
	"Sync",
	"Grievance",
	"Broadcast",
	"Issue",
	"Assignment",
	"Onboarding",
	"Approval",
	"System",
)

FIELDS = [
	"name",
	"kind",
	"title",
	"body",
	"href",
	"read",
	"read_at",
	"event",
	"reference_doctype",
	"reference_name",
	"creation",
]


def serialize(row) -> dict:
	out = {f: row.get(f) for f in FIELDS}
	out["id"] = out.pop("name")
	out["read"] = bool(out["read"])
	out["at"] = out.pop("creation")
	for f in ("at", "read_at"):
		if out.get(f) is not None and hasattr(out[f], "isoformat"):
			out[f] = out[f].isoformat()
	return out


def user_for_da(da_id: str) -> str | None:
	"""The user bound to a DA-ID through an active Development Agent assignment."""
	rows = frappe.get_all(
		"DA RBAC Assignment",
		filters={"da_id": da_id, "role": C.ROLE_DA, "active": 1},
		fields=["user"],
		order_by="modified desc",
		limit_page_length=1,
	)
	return rows[0].user if rows else None


def notify(
	user: str,
	*,
	kind: str,
	title: str,
	body: str | None = None,
	href: str | None = None,
	event: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	da_id: str | None = None,
	channel: str = "App",
) -> str:
	"""Create one notification row for `user`. Returns its name."""
	if kind not in KINDS:
		frappe.throw(_("Unknown notification kind {0}.").format(kind))
	doc = frappe.get_doc(
		{
			"doctype": "DA Notification",
			"user": user,
			"kind": kind,
			"title": title[:140],
			"body": body,
			"href": href,
			"event": event,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"da_id": da_id,
			"channel": channel,
			"delivery_status": "Not sent",
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


def notify_da(da_id: str, **kwargs) -> str | None:
	"""notify() addressed by DA-ID; skips silently when no user is bound yet (pre-provisioning)."""
	user = user_for_da(da_id)
	if not user:
		return None
	return notify(user, da_id=da_id, **kwargs)


def list_notifications(
	ctx: ScopeContext | None = None, *, unread: bool = False, limit: int = 20, start: int = 0
) -> tuple[list[dict], dict]:
	"""A user's own notifications, newest first, plus meta {unread_count, limit, start}."""
	ctx = ctx or require_roles(*C.DA_SERVICES_ROLES)
	filters: dict = {"user": ctx.user}
	if unread:
		filters["read"] = 0
	limit = max(1, min(int(limit), 100))
	start = max(0, int(start))
	rows = frappe.get_all(
		"DA Notification",
		filters=filters,
		fields=FIELDS,
		order_by="creation desc",
		limit_page_length=limit,
		limit_start=start,
	)
	unread_count = frappe.db.count("DA Notification", {"user": ctx.user, "read": 0})
	return [serialize(r) for r in rows], {"unread_count": unread_count, "limit": limit, "start": start}


def mark_read(name: str, ctx: ScopeContext | None = None) -> dict:
	ctx = ctx or require_roles(*C.DA_SERVICES_ROLES)
	doc = frappe.get_doc("DA Notification", name)
	if doc.user != ctx.user and not ctx.unrestricted:
		frappe.throw(_("Notification {0} is not yours.").format(name), frappe.PermissionError)
	if not doc.read:
		doc.read = 1
		doc.read_at = now_datetime()
		doc.flags.ignore_permissions = True
		doc.save()
	return serialize(doc)


def mark_all_read(ctx: ScopeContext | None = None) -> dict:
	ctx = ctx or require_roles(*C.DA_SERVICES_ROLES)
	names = frappe.get_all("DA Notification", filters={"user": ctx.user, "read": 0}, pluck="name")
	now = now_datetime()
	for name in names:
		frappe.db.set_value("DA Notification", name, {"read": 1, "read_at": now}, update_modified=True)
	return {"updated": len(names)}


# ---------------------------------------------------------------------------
# Broadcast (Communications Officer -> DAs)
# ---------------------------------------------------------------------------

BROADCAST_SCOPES = ("all", "region", "zone", "woreda")
BROADCAST_SENT = "notification.broadcast.sent"


def _broadcast_recipients(scope: str, scope_value: str | None) -> list[str]:
	"""Users holding an active Development Agent grant inside the scope, de-duplicated."""
	filters: dict = {
		"role": C.ROLE_DA,
		"active": 1,
		"effective_from": ("<=", today()),
	}
	if scope == "region":
		filters["region_scope"] = scope_value
	elif scope == "zone":
		filters["zone_scope"] = scope_value
	elif scope == "woreda":
		filters["woreda_scope"] = scope_value

	rows = frappe.get_all(
		"DA RBAC Assignment",
		filters=filters,
		or_filters=[["effective_to", "is", "not set"], ["effective_to", ">=", today()]],
		fields=["user"],
	)
	return sorted({r.user for r in rows if r.user})


def _assert_broadcast_scope_allowed(ctx: ScopeContext, scope: str, scope_value: str | None) -> None:
	"""A sender may only target what their own grants cover (deny, never widen).

	Admin is unrestricted. A Communications Officer whose grant carries no
	Region / Zone / Woreda is the national comms desk and may target anything;
	one with a scoped grant may only broadcast inside that scope.
	"""
	if ctx.unrestricted:
		return
	if not (ctx.region_scopes or ctx.zone_scopes or ctx.woreda_scopes):
		return
	if scope == "all":
		frappe.throw(
			_("Your grant is limited to a Region / Zone / Woreda; choose a scope you cover."),
			frappe.PermissionError,
		)
	covered = ctx.covers(
		region=scope_value if scope == "region" else None,
		zone=scope_value if scope == "zone" else None,
		woreda=scope_value if scope == "woreda" else None,
	)
	if not covered:
		frappe.throw(
			_("{0} {1} is not in your scope.").format(scope.capitalize(), scope_value), frappe.PermissionError
		)


def broadcast(
	ctx: ScopeContext,
	*,
	title: str | None,
	body: str | None = None,
	href: str | None = None,
	scope: str = "all",
	scope_value: str | None = None,
	client_op_id: str | None = None,
) -> dict:
	"""Send one Broadcast notification to every DA in the scope.

	Returns {broadcast_id, scope, scope_value, recipients, duplicate}. With a
	client_op_id, resending the same request returns the original broadcast
	instead of notifying everyone twice. The send is recorded as a DA Audit
	Event (who, what, which scope, how many).
	"""
	scope = (scope or "all").strip().lower()
	if scope not in BROADCAST_SCOPES:
		frappe.throw(
			_("scope must be one of: {0}").format(", ".join(BROADCAST_SCOPES)), frappe.ValidationError
		)
	if scope != "all" and not scope_value:
		frappe.throw(_("scope_value is required for scope {0}.").format(scope), frappe.ValidationError)
	if scope == "all":
		scope_value = None
	title = (title or "").strip()
	if not title:
		frappe.throw(_("title is required."), frappe.ValidationError)
	if len(title) > 140:
		frappe.throw(_("title must be 140 characters or fewer."), frappe.ValidationError)

	href = _validate_href(href)
	_assert_broadcast_scope_allowed(ctx, scope, scope_value)

	# Idempotency at the database: the DA Broadcast row goes in first and its
	# client_op_id is unique, so two replays racing each other cannot both pass a
	# "does it exist yet" check; the second insert blocks on the first and then
	# fails as a duplicate. Everything after the insert sits behind a savepoint so
	# a failure half-way through the recipient loop leaves neither the record nor
	# a partial set of notifications behind (atomic broadcast).
	frappe.db.savepoint("broadcast")
	record = frappe.get_doc(
		{
			"doctype": "DA Broadcast",
			"title": title,
			"body": body,
			"href": href,
			"scope": scope,
			"scope_value": scope_value,
			"sent_by": ctx.user,
			"sent_at": now_datetime(),
			"client_op_id": client_op_id,
		}
	)
	record.flags.ignore_permissions = True
	try:
		record.insert()
	except frappe.UniqueValidationError, frappe.DuplicateEntryError:
		frappe.db.rollback(save_point="broadcast")
		return _duplicate_broadcast(client_op_id)

	broadcast_id = client_op_id or record.name
	event_key = f"broadcast:{broadcast_id}"
	try:
		recipients = _broadcast_recipients(scope, scope_value)
		if not recipients:
			frappe.throw(
				_("No active Development Agent is assigned to {0} {1}; check the scope value.")
				.format(scope, scope_value or "")
				.strip(),
				frappe.ValidationError,
			)
		for user in recipients:
			notify(user, kind="Broadcast", title=title, body=body, href=href, event=event_key)
		record.db_set("recipients", len(recipients), update_modified=False)
		audit_log.record(
			BROADCAST_SENT,
			entity_type="DA Broadcast",
			entity_name=broadcast_id,
			details={
				"scope": scope,
				"scope_value": scope_value,
				"title": title,
				"recipients": len(recipients),
			},
			user=ctx.user,
		)
	except Exception:
		frappe.db.rollback(save_point="broadcast")
		raise

	return {
		"broadcast_id": broadcast_id,
		"scope": scope,
		"scope_value": scope_value,
		"recipients": len(recipients),
		"duplicate": False,
	}


def _duplicate_broadcast(client_op_id: str | None) -> dict:
	"""The response for a replayed client_op_id: what the original send did."""
	row = frappe.db.get_value(
		"DA Broadcast",
		{"client_op_id": client_op_id},
		["name", "scope", "scope_value", "recipients"],
		as_dict=True,
	)
	if not row:  # the unique index fired on something else; nothing sensible to replay
		frappe.throw(
			_("Broadcast {0} could not be recorded; retry.").format(client_op_id), frappe.ValidationError
		)
	return {
		"broadcast_id": client_op_id or row.name,
		"scope": row.scope,
		"scope_value": row.scope_value,
		"recipients": int(row.recipients or 0),
		"duplicate": True,
	}


def _validate_href(href: str | None) -> str | None:
	"""A broadcast link is a portal path ("/notifications"), never an external URL."""
	href = (href or "").strip()
	if not href:
		return None
	if not href.startswith("/") or href.startswith("//") or any(c.isspace() for c in href) or len(href) > 500:
		frappe.throw(
			_("href must be a portal path starting with '/', for example /notifications."),
			frappe.ValidationError,
		)
	return href
