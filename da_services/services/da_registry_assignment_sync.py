"""Create a DA's RBAC and Kebele assignments from their DA Registry record.

Runs in the request (no background job) from the User ``on_update`` hook, when a User is
created with ``da_registry_id`` set or its ``da_registry_id`` changes:

1. Sign in the Master Data Sync account (``services/utils.sync_account_login``), as the
   geo master sync does.
2. ``POST {da_registry_domain}/register-data/get_subject_record`` with
   ``subject_register_id`` = ``da_registry_form_register_id`` of DA Services Settings and
   ``subject_record_id`` = the User's ``da_registry_id``.
3. From the record:

   - a **DA RBAC Assignment**: role Development Agent, active, scope from ``region_code``
     / ``zone_code`` / ``woreda_code``, effective today. Skipped when the record has no
     ``region_code``;
   - a **DA Kebele Assignment** when the record has a ``kebele_code``: state Effective,
     mode Automatic, effective today. Inserting it supersedes the DA's earlier live
     Kebele assignments (see DA Kebele Assignment);
   - the User's **DA Hierarchy** node: Region / Zone / Woreda / Kebele from the
     ``*_code`` fields (designation and country are not set), and the parent from
     ``reports_to`` (the User whose ``username`` it is). A boss with no node yet gets a
     group node under the root. Saving the node updates the hierarchy User Permissions on
     ``User`` (services/hierarchy_user_permissions.py).

An assignment that already says the same thing is not created again. An earlier active
Development Agent RBAC assignment this sync created for the User, with a different scope,
is made inactive, so a DA moved to another area does not keep the old one. Assignments
made by hand are left alone.

A failure (settings missing, login or registry refused, a code not in the geo masters,
...) never blocks saving the User: it is logged (Error Log "DA Registry assignment sync
failed"), shown as a message, and whatever this sync had created is rolled back.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import today

from da_services.da_services.doctype.da_hierarchy.da_hierarchy import ROOT, ensure_root
from da_services.da_services.doctype.da_kebele_assignment.da_kebele_assignment import LIVE_STATES
from da_services.services import constants as C
from da_services.services import utils
from da_services.services.kebele_user_permissions import sync_user_permissions

API_NOTE = "it is created from api"
GET_SUBJECT_RECORD = "/register-data/get_subject_record"
RBAC = "DA RBAC Assignment"
KEBELE_ASSIGNMENT = "DA Kebele Assignment"
HIERARCHY = "DA Hierarchy"
# DA Hierarchy field -> DA Registry record key.
HIERARCHY_FIELDS = {
	"region": "region_code",
	"zone": "zone_code",
	"woreda": "woreda_code",
	"kebele": "kebele_code",
}
_SAVEPOINT = "da_registry_assignment_sync"


class AssignmentSyncError(Exception):
	"""The registry record cannot be turned into assignments."""


# ---------------------------------------------------------------- hook


def on_user_update(doc, method=None) -> None:
	"""User ``after_insert`` and ``on_update``: sync when the User is created with a DA-ID or
	its DA-ID changes.

	On create Frappe runs ``after_insert`` and then ``on_update`` (with ``flags.in_insert``);
	the flag set here makes the second one skip, so the registry is asked once per save.
	"""
	inserting = method == "after_insert" or doc.flags.in_insert
	if (
		method != "after_insert"
		and doc.flags.in_insert
		and doc.flags.pop("da_registry_synced_on_insert", False)
	):
		return  # on_update of an insert whose after_insert already synced
	da_id = (doc.get("da_registry_id") or "").strip()
	if not da_id:
		return
	if not inserting and not doc.has_value_changed("da_registry_id"):
		return
	if method == "after_insert":
		doc.flags.da_registry_synced_on_insert = True
	sync_user_assignments(doc.name, da_id)


def sync_user_assignments(user: str, da_id: str, *, fetch=None) -> dict | None:
	"""Create the assignments for ``user`` from DA Registry record ``da_id``.

	Returns ``{"rbac": name | None, "kebele": name | None, "hierarchy": name}`` (rbac /
	kebele None when nothing new was needed), or None when the sync failed; a failure is
	logged, shown, and rolled back.
	``fetch`` replaces the registry call (``fetch(da_id) -> record``), for tests.
	"""
	frappe.db.savepoint(_SAVEPOINT)
	try:
		record = (fetch or fetch_registry_record)(da_id)
		result = {
			"rbac": _ensure_rbac_assignment(user, record),
			"kebele": _ensure_kebele_assignment(user, record),
			"hierarchy": _ensure_hierarchy(user, record),
		}
	except Exception as exc:
		frappe.db.rollback(save_point=_SAVEPOINT)
		frappe.log_error(
			title="DA Registry assignment sync failed",
			message=f"user={user} da_registry_id={da_id}\n\n{frappe.get_traceback()}",
		)
		frappe.msgprint(
			_("Could not create the DA assignments from the DA Registry: {0}").format(_user_message(exc)),
			title=_("DA Registry"),
			indicator="orange",
			alert=True,
		)
		return None
	return result


def _user_message(exc: Exception) -> str:
	"""What the person saving the User is told. Settings and record problems are shown as
	they are; anything else (it can name internal hosts) points to the Error Log."""
	if isinstance(exc, AssignmentSyncError) or (
		isinstance(exc, utils.StaffAPIError) and exc.kind == "config"
	):
		return str(exc)
	if isinstance(exc, frappe.ValidationError):
		return frappe.utils.strip_html(str(exc))
	return _("see the Error Log")


# ---------------------------------------------------------------- registry


def fetch_registry_record(da_id: str) -> dict:
	"""The DA Registry record ``da_id`` (``response_payload`` of get_subject_record)."""
	settings = frappe.get_cached_doc(utils.SETTINGS_DOCTYPE)
	register_id = (settings.get("da_registry_form_register_id") or "").strip()
	tokens = utils.sync_account_login(
		also_required={
			"DA Registry Domain": settings.get("da_registry_domain"),
			"DA Registry Form Register ID": register_id,
		}
	)
	base_url = utils.base_url_from_domain(settings.get("da_registry_domain"), "DA Registry Domain")
	record = utils.post_staff_api(
		base_url + GET_SUBJECT_RECORD,
		{"subject_register_id": register_id, "subject_record_id": da_id},
		tokens=tokens,
		action="get the DA Registry record",
		timeout=int(frappe.conf.get("da_registry_timeout_seconds") or 20),
	)
	if not isinstance(record, dict) or not record:
		raise AssignmentSyncError(f"The DA Registry has no record {da_id}")
	return record


def _code(record: dict, key: str) -> str | None:
	value = str(record.get(key) or "").strip()
	return value or None


# ---------------------------------------------------------------- assignments


def _ensure_rbac_assignment(user: str, record: dict) -> str | None:
	"""The Development Agent RBAC assignment for the record's area; None if already in place,
	or when the record has no ``region_code`` (no RBAC assignment is made then)."""
	scope = {
		"region_scope": _code(record, "region_code"),
		"zone_scope": _code(record, "zone_code"),
		"woreda_scope": _code(record, "woreda_code"),
	}
	if not scope["region_scope"]:
		return None

	live = frappe.get_all(
		RBAC,
		filters={"user": user, "role": C.ROLE_DA, "active": 1},
		fields=["name", "notes", *scope],
	)
	if any(all((row.get(k) or None) == v for k, v in scope.items()) for row in live):
		return None
	# A DA moved to another area: end what this sync granted before, never a manual grant.
	for row in live:
		if (row.notes or "").strip() == API_NOTE:
			old = frappe.get_doc(RBAC, row.name)
			old.active = 0
			old.save(ignore_permissions=True)

	doc = frappe.get_doc(
		{
			"doctype": RBAC,
			"user": user,
			"role": C.ROLE_DA,
			"active": 1,
			**scope,
			"effective_from": today(),
			"notes": API_NOTE,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _ensure_kebele_assignment(user: str, record: dict) -> str | None:
	"""An Effective Kebele assignment when the record has a kebele_code; None otherwise."""
	kebele = _code(record, "kebele_code")
	if not kebele:
		return None
	# Same Kebele under the User's current DA-ID; a row under an old DA-ID is replaced, or a
	# later Effective row for the new DA-ID would not supersede it.
	da_id = frappe.db.get_value("User", user, "da_registry_id")
	if frappe.db.exists(
		KEBELE_ASSIGNMENT, {"user": user, "kebele": kebele, "state": "Effective", "da_id": da_id}
	):
		return None
	doc = frappe.get_doc(
		{
			"doctype": KEBELE_ASSIGNMENT,
			"user": user,
			"kebele": kebele,
			"state": "Effective",
			"mode": "Automatic",
			"effective_date": today(),
			"reason": API_NOTE,
		}
	)
	doc.insert(ignore_permissions=True)
	_supersede_other_da_ids(user, doc.name)
	return doc.name


def _supersede_other_da_ids(user: str, new_name: str) -> None:
	"""Supersede the User's live Kebele assignments made under another DA-ID.

	Inserting an Effective assignment supersedes the earlier live rows of the same
	``da_id`` only; when the User's DA-ID changed, their rows under the old one would stay
	Effective, and the User would keep the old Kebele's User Permission.
	"""
	new_da_id = frappe.db.get_value(KEBELE_ASSIGNMENT, new_name, "da_id")
	stale = frappe.get_all(
		KEBELE_ASSIGNMENT,
		filters={
			"user": user,
			"state": ("in", LIVE_STATES),
			"da_id": ("!=", new_da_id),
			"name": ("!=", new_name),
		},
		pluck="name",
	)
	for name in stale:
		frappe.db.set_value(KEBELE_ASSIGNMENT, name, {"state": "Superseded", "superseded_by": new_name})
	if stale:
		sync_user_permissions(user)


# ---------------------------------------------------------------- hierarchy


def _ensure_hierarchy(user: str, record: dict) -> str:
	"""Create or update ``user``'s DA Hierarchy node from the record. Returns its name."""
	ensure_root()
	values = {field: _code(record, key) for field, key in HIERARCHY_FIELDS.items()}
	values["parent_da_hierarchy"] = _boss_node(user, _code(record, "reports_to")) or ROOT
	if frappe.db.exists(HIERARCHY, {"user": user}):
		doc = frappe.get_doc(HIERARCHY, {"user": user})
		doc.update(values)
	else:
		doc = frappe.get_doc({"doctype": HIERARCHY, "user": user, **values})
	# A group when anyone reports to the user; a group stays one.
	doc.is_group = 1 if doc.is_group or _has_reports(user) else 0
	doc.save(ignore_permissions=True)
	return doc.name


def _boss_node(user: str, reports_to: str | None) -> str | None:
	"""The DA Hierarchy node of the User whose ``username`` is ``reports_to``, made a group
	(created under the root when missing). None when there is no such User."""
	if not reports_to:
		return None
	boss = frappe.db.get_value("User", {"username": reports_to})
	if not boss or boss == user:
		return None
	name = frappe.db.get_value(HIERARCHY, {"user": boss})
	if not name:
		node = frappe.get_doc(
			{"doctype": HIERARCHY, "user": boss, "is_group": 1, "parent_da_hierarchy": ROOT}
		)
		node.insert(ignore_permissions=True)
		return node.name
	if not frappe.db.get_value(HIERARCHY, name, "is_group"):
		node = frappe.get_doc(HIERARCHY, name)
		node.is_group = 1
		node.save(ignore_permissions=True)
	return name


def _has_reports(user: str) -> bool:
	node = frappe.db.get_value(HIERARCHY, {"user": user})
	return bool(node and frappe.db.exists(HIERARCHY, {"parent_da_hierarchy": node}))
