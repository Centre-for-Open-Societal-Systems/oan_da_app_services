"""Shared test fixtures for DA Services tests.

Geography comes only from live ``DA Region`` / ``DA Zone`` / ``DA Woreda`` / ``DA Kebele``
rows populated by ``geo_master_sync``. Tests must not invent or seed master rows, and
must not wipe entire DocTypes (that would destroy synced / production-like data).
"""

import frappe
from frappe import _

from da_services.integrations.da_registry.mock import LEGACY_DUMMY_GEO, live_fixture_geo
from da_services.services.geo import masters_nonempty

# DocTypes whose JSON can be ahead of a site that has not been migrated yet.
# Integration tests reload them so the DB columns and Select options match the app.
_SCHEMA_DOCTYPES = (
	("da_profile_change_request", "DA Profile Change Request"),
	("da_integration_event", "DA Integration Event"),
	("da_survey_parameter", "DA Survey Parameter"),
)

# Never delete these in tests ΓÇö they are Master Data sync targets.
GEO_MASTER_DOCTYPES = frozenset({"DA Country", "DA Region", "DA Zone", "DA Woreda", "DA Kebele"})


def ensure_doctype_schema(*doctypes: str) -> None:
	"""Reload DocType JSON into the site so missing columns and Select options exist.

	Does not change application logic. Call from setUp when a test hits a field
	that the current site's table may not have yet.
	"""
	wanted = set(doctypes) if doctypes else {name for _file, name in _SCHEMA_DOCTYPES}
	for file_name, name in _SCHEMA_DOCTYPES:
		if name not in wanted:
			continue
		frappe.reload_doc("DA Services", "doctype", file_name, force=True)


def require_geo_masters() -> None:
	"""Fail closed when geography masters are empty (run geo_master_sync first)."""
	if not masters_nonempty():
		frappe.throw(
			_(
				"Geography masters are empty. Run "
				"`bench --site <site> execute da_services.services.geo_master_sync.sync_geo_masters` "
				"before these tests."
			),
			frappe.ValidationError,
		)


def require_named_masters(specs: dict[str, str | list[str] | tuple[str, ...]]) -> None:
	"""Fail closed when specific master Link targets are missing.

	``specs`` maps DocType ΓåÆ document name or names
	(e.g. ``{"DA Region": "ET04", "DA Kebele": ["ET04010101"]}``).
	Does not create masters ΓÇö use ``resolve_test_geo`` when any synced chain is enough.
	"""
	require_geo_masters()
	missing = []
	for doctype, names in specs.items():
		for name in [names] if isinstance(names, str) else names:
			if name and not frappe.db.exists(doctype, name):
				missing.append(f"{doctype}/{name}")
	if missing:
		frappe.throw(
			_(
				"Required geography masters are missing: {0}. "
				"Run geo_master_sync, or use resolve_test_geo() instead of hard-coded codes."
			).format(", ".join(missing)),
			frappe.ValidationError,
		)


def aligned_mock_geo() -> dict:
	"""Geography the mock DA Registry stamps on DA-000001..004 after master alignment.

	Call after ``require_geo_masters``. Returns primary + other chains matching
	``MockDARegistryClient`` / ``fixture_das_aligned``.
	"""
	require_geo_masters()
	primary, other = live_fixture_geo()
	return {
		"region": primary["region"],
		"zone": primary["zone"],
		"woreda": primary["woreda"],
		"kebele": primary["kebele"],
		"kebele_alt": primary["kebele_alt"],
		"other_region": other["region"],
		"other_zone": other["zone"],
		"other_woreda": other["woreda"],
		"other_kebele": other["kebele"],
	}


def resolve_test_geo() -> dict:
	"""Pick one active Region ΓåÆ Zone ΓåÆ Woreda ΓåÆ Kebele chain from synced masters.

	Prefers a Woreda that has at least two active Kebeles so tests can cover
	same-Woreda / other-Kebele cases without inventing codes.
	"""
	require_geo_masters()
	counts = frappe.db.sql(
		"""
		SELECT woreda, COUNT(*) AS n
		FROM `tabDA Kebele`
		WHERE is_active = 1 AND IFNULL(woreda, '') != ''
		GROUP BY woreda
		ORDER BY n DESC, woreda ASC
		LIMIT 1
		""",
		as_dict=True,
	)
	if not counts:
		frappe.throw(_("No active DA Kebele found."), frappe.ValidationError)
	woreda = counts[0].woreda
	kebeles = frappe.get_all(
		"DA Kebele",
		filters={"is_active": 1, "woreda": woreda},
		fields=["name", "kebele_name"],
		order_by="name asc",
		limit=2,
	)
	row = kebeles[0]
	zone = frappe.db.get_value("DA Woreda", woreda, "zone")
	region = frappe.db.get_value("DA Zone", zone, "region") if zone else None
	if not (zone and region):
		frappe.throw(
			_("Kebele {0} is missing Woreda/Zone/Region parents.").format(row.name),
			frappe.ValidationError,
		)
	return {
		"region": region,
		"zone": zone,
		"woreda": woreda,
		"kebele": row.name,
		"kebele_name": row.kebele_name,
		"kebele_alt": kebeles[1].name if len(kebeles) > 1 else row.name,
	}


def resolve_sibling_zone_geo(region: str, exclude_zone: str) -> dict:
	"""Another ZoneΓåÆWoredaΓåÆKebele chain under the same Region (for cross-zone tests)."""
	require_geo_masters()
	zones = frappe.get_all(
		"DA Zone",
		filters={"is_active": 1, "region": region, "name": ("!=", exclude_zone)},
		pluck="name",
		order_by="name asc",
	)
	for zone in zones:
		woredas = frappe.get_all(
			"DA Woreda", filters={"is_active": 1, "zone": zone}, pluck="name", order_by="name asc", limit=1
		)
		if not woredas:
			continue
		woreda = woredas[0]
		kebeles = frappe.get_all(
			"DA Kebele",
			filters={"is_active": 1, "woreda": woreda},
			fields=["name", "kebele_name"],
			order_by="name asc",
			limit=1,
		)
		if not kebeles:
			continue
		return {
			"region": region,
			"zone": zone,
			"woreda": woreda,
			"kebele": kebeles[0].name,
			"kebele_name": kebeles[0].kebele_name,
		}
	frappe.throw(
		_("Region {0} has no second Zone with a Woreda and Kebele in the geography masters.").format(region),
		frappe.ValidationError,
	)


def ensure_da_user(da_id: str, *, email: str | None = None) -> str:
	"""Ensure a User exists with User.da_registry_id = da_id; return the user name."""
	existing = frappe.db.get_value("User", {"da_registry_id": da_id}, "name")
	if existing:
		return existing
	email = email or f"{da_id.lower().replace('_', '-')}@test.da.local"
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": da_id,
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
	frappe.db.set_value("User", email, "da_registry_id", da_id)
	return email


def bind_da_user(email: str, da_id: str) -> str:
	"""Make ``email`` the one User bound to ``da_id`` (User.da_registry_id is unique).

	Test modules name their own DA users; release any other User that still holds the id.
	"""
	for other in frappe.get_all(
		"User", filters={"da_registry_id": da_id, "name": ("!=", email)}, pluck="name"
	):
		frappe.db.set_value("User", other, "da_registry_id", None)
	frappe.db.set_value("User", email, "da_registry_id", da_id)
	return email


def ensure_kebele_assignment(da_id: str, kebele: str | None = None, *, mode: str = "Automatic") -> None:
	"""Replace this DA's assignments with one Effective posting.

	``kebele`` must be an existing ``DA Kebele`` name. When omitted, uses ``resolve_test_geo``.
	"""
	require_geo_masters()
	if not kebele:
		kebele = resolve_test_geo()["kebele"]
	if not frappe.db.exists("DA Kebele", kebele):
		frappe.throw(
			_("Kebele {0} is not in DA Kebele masters.").format(kebele),
			frappe.ValidationError,
		)
	user = ensure_da_user(da_id)
	frappe.db.delete("DA Kebele Assignment", {"da_id": da_id})
	frappe.get_doc(
		{
			"doctype": "DA Kebele Assignment",
			"user": user,
			"kebele": kebele,
			"state": "Effective",
			"mode": mode,
			"effective_date": "2026-10-01",
			"registry_sync_state": "Not sent",
		}
	).insert(ignore_permissions=True)


# Fields that name the test user a row belongs to (one DocType may carry several).
_USER_FIELDS = ("user", "created_by_user", "requested_by", "sent_by")

# DocTypes with no DA / user column of their own: rows are matched by ``owner`` instead.
# "Administrator" is never matched this way ΓÇö it owns real rows on every site.
_OWNER_SCOPED_DOCTYPES = frozenset({"DA Integration Event"})

_DEFAULT_CLEANUP_DOCTYPES = (
	"DA Notification",
	"DA Visit",
	"DA Task",
	"DA Farmer Link",
	"DA Farmer Reference",
	"DA Kebele Assignment",
	"DA RBAC Assignment",
	"DA Profile Change Request",
	"DA Leave Request",
	"DA Broadcast",
	"DA Integration Event",
	"DA Survey Response",
	"DA Survey Template",
	"DA Audit Event",
)


def cleanup_test_rows(
	*,
	users: list[str] | tuple[str, ...] | None = None,
	da_ids: list[str] | tuple[str, ...] | None = None,
	farmer_ids: list[str] | tuple[str, ...] | None = None,
	names: list[str] | tuple[str, ...] | None = None,
	doctypes: list[str] | tuple[str, ...] | None = None,
) -> None:
	"""Delete only rows this test created. Never wipe a DocType or geo masters.

	Filters are OR'd per DocType when multiple are provided (user / da_id / farmer_id / name).
	``users`` matches ``user``, ``created_by_user``, ``requested_by`` and ``sent_by`` columns;
	survey responses of a matched template are removed with it.
	"""
	users = tuple(u for u in (users or ()) if u)
	da_ids = tuple(d for d in (da_ids or ()) if d)
	farmer_ids = tuple(f for f in (farmer_ids or ()) if f)
	names = tuple(n for n in (names or ()) if n)
	if not any((users, da_ids, farmer_ids, names)):
		return

	for doctype in doctypes or _DEFAULT_CLEANUP_DOCTYPES:
		if doctype in GEO_MASTER_DOCTYPES:
			continue
		if not frappe.db.exists("DocType", doctype):
			continue
		meta = frappe.get_meta(doctype)
		filters_list: list[dict] = []
		if users:
			for field in _USER_FIELDS:
				if meta.has_field(field):
					filters_list.append({field: ("in", users)})
			owners = tuple(u for u in users if u != "Administrator")
			if owners and doctype in _OWNER_SCOPED_DOCTYPES:
				filters_list.append({"owner": ("in", owners)})
		if da_ids and meta.has_field("da_id"):
			filters_list.append({"da_id": ("in", da_ids)})
		if farmer_ids and meta.has_field("farmer_id"):
			filters_list.append({"farmer_id": ("in", farmer_ids)})
		if names:
			filters_list.append({"name": ("in", names)})
		if doctype == "DA Survey Template" and frappe.db.exists("DocType", "DA Survey Response"):
			for filters in filters_list:
				templates = frappe.get_all(doctype, filters=filters, pluck="name")
				if templates:
					frappe.db.delete("DA Survey Response", {"survey": ("in", templates)})
		for filters in filters_list:
			frappe.db.delete(doctype, filters)


def remove_unsynced_fixture_geo() -> dict:
	"""Delete invented LEGACY_DUMMY_GEO rows that were never written by geo_master_sync.

	Safe on a site that already has a full Master Data sync: only rows with
	``last_synced_at`` empty and a legacy dummy code are removed.
	"""
	level_to_doctype = {
		"region": "DA Region",
		"zone": "DA Zone",
		"woreda": "DA Woreda",
		"kebele": "DA Kebele",
	}
	removed: list[str] = []
	# Children first so Link parents can be removed last.
	for code, level, _parent, _label in reversed(LEGACY_DUMMY_GEO):
		doctype = level_to_doctype[level]
		if not frappe.db.exists(doctype, code):
			continue
		if frappe.db.get_value(doctype, code, "last_synced_at"):
			continue  # real sync owns this code
		frappe.delete_doc(doctype, code, ignore_permissions=True, force=True)
		removed.append(f"{doctype}/{code}")
	return {"removed": removed}
