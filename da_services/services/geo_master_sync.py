"""Keep DA Country / DA Region / DA Zone / DA Woreda / DA Kebele in step with Master Data.

Login: the Master Data Sync account of DA Services Settings (username, password, login
key) signs in through ``sso_login``, the function behind ``POST /sso/login``. Its
``access_token`` goes to Master Data as ``Authorization: Bearer`` and its ``csrf_token``
as ``X-CSRF-Token`` (header and cookie). When Master Data refuses the token part-way
through a run (it expired), the account logs in again once.

Walk, top-down:

1. ``POST /geo/get_all_geo_levels`` maps each level mnemonic (``region``) to its
   ``level_id`` (``l1``).
2. ``POST /geo/get_geo_level_values`` is called with ``level_id`` and
   ``parent_level_value_id`` for every value of the level above, every page.

A value Master Data lists is inserted, or updated when its name, parent or active flag
changed. An active row Master Data no longer lists is made inactive (``is_active = 0``),
never deleted: other records link to it. A row that comes back is made active again.

Daily on the long queue (Scheduled Job Type ``geo_master_sync.sync_geo_masters_scheduled``,
which can also be run with Execute), or by hand::

    bench --site <site> execute da_services.services.geo_master_sync.sync_geo_masters

A run changes nothing (it rolls back) when the login or a Master Data call fails, a level
lists nothing, or a doctype would lose more than a fifth of its active rows. In those
cases the answer is most likely partial.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import frappe
from frappe.utils import get_url, now_datetime

from da_services.integrations.master import MasterDataClient, MasterDataError
from da_services.integrations.master.client import base_url_from_domain
from da_services.integrations.sso import SSOLoginError, sso_login

# Master Data level mnemonic, top first. Each level's parent is the one before it.
LEVELS = ("country", "region", "zone", "woreda", "kebele")
DOCTYPES = {level: f"DA {level.capitalize()}" for level in LEVELS}
MAX_DEACTIVATION_SHARE = 0.2
PAGE_SIZE = 100
MAX_PAGES = 500  # per parent; a server that ignores current_page must not loop forever
WORKERS = 8  # parallel get_geo_level_values calls (one per parent)
_CHUNK = 1000  # names per bulk UPDATE
_UNAUTHORIZED = object()


class GeoSyncError(Exception):
	"""The run was refused; nothing was changed."""


# ---------------------------------------------------------------- Master Data source


class MasterDataSource:
	"""Master Data geography, read with the Master Data Sync account's SSO tokens."""

	def __init__(self, login=sso_login):
		self._sso_login = login
		self._client: MasterDataClient | None = None

	def geo_levels(self) -> list[dict]:
		for attempt in (1, 2):
			try:
				rows = self._get_client().get_geo_levels()
				break
			except MasterDataError as exc:
				if exc.kind != "unauthorized" or attempt == 2:
					raise
				self._login()
		return [
			{"level_id": r["level_id"], "mnemonic": str(r.get("level_mnemonic") or "").strip().lower()}
			for r in rows
			if r.get("level_id")
		]

	def geo_level_values_many(self, level_id: str, parents: list[str | None]) -> dict[str | None, list[dict]]:
		"""{parent: values} for every parent; the calls run in parallel threads."""
		results: dict[str | None, list[dict]] = {}
		pending = list(parents)
		for attempt in (1, 2):
			client = self._get_client()
			with ThreadPoolExecutor(max_workers=min(WORKERS, max(1, len(pending)))) as pool:
				answers = list(pool.map(lambda p: _all_values_or_unauthorized(client, level_id, p), pending))
			failed = [p for p, rows in zip(pending, answers, strict=True) if rows is _UNAUTHORIZED]
			results.update(
				{p: rows for p, rows in zip(pending, answers, strict=True) if rows is not _UNAUTHORIZED}
			)
			if not failed:
				break
			if attempt == 2:
				raise GeoSyncError("Master Data refused the token again after a fresh SSO login")
			# The token expired mid-run. Log in on this thread: the login reads the database.
			self._login()
			pending = failed
		return {p: results[p] for p in parents}

	def _get_client(self) -> MasterDataClient:
		if self._client is None:
			self._login()
		return self._client

	def _login(self) -> None:
		settings = frappe.get_cached_doc("DA Services Settings")
		username = (settings.master_sync_username or "").strip()
		password = settings.get_password("master_sync_password", raise_exception=False) or ""
		key = (settings.master_sync_login_key or "").strip()
		missing = [
			label
			for label, value in (
				("Master Data Domain", settings.master_data_domain),
				("Username", username),
				("Password", password),
				("Login Key", key),
			)
			if not value
		]
		if missing:
			raise GeoSyncError(f"DA Services Settings: set {', '.join(missing)} for the Master Data sync")
		conf = frappe.conf
		result = self._sso_login(
			username=username,
			password=password,
			key=key,
			return_url=conf.get("sso_return_url") or get_url(),
			timeout=int(conf.get("sso_timeout_seconds") or 20),
		)
		self._client = MasterDataClient(
			base_url=base_url_from_domain(settings.master_data_domain),
			token=result.access_token,
			csrf_token=result.csrf_token,
			sender_app=conf.get("da_registry_sender_app") or "da-services",
			timeout=int(conf.get("master_data_timeout_seconds") or 20),
		)


def _all_values_or_unauthorized(client: MasterDataClient, level_id: str, parent: str | None):
	"""Every page of one parent's values, or _UNAUTHORIZED when the token was refused.

	Runs in a worker thread, so it only makes HTTP calls (no database).
	"""
	values: list[dict] = []
	seen: set[str] = set()
	for page in range(1, MAX_PAGES + 1):
		try:
			rows, pagination = client.get_geo_level_values(
				level_id, parent, current_page=page, page_size=PAGE_SIZE
			)
		except MasterDataError as exc:
			if exc.kind == "unauthorized":
				return _UNAUTHORIZED
			raise
		fresh = [r for r in rows if r.get("level_value_id") and r["level_value_id"] not in seen]
		for row in fresh:
			seen.add(row["level_value_id"])
			if parent and row.get("parent_level_value_id") not in (None, parent):
				continue  # Master Data may ignore the parent filter
			values.append(
				{
					"code": row["level_value_id"],
					"name": row.get("level_value_mnemonic") or row["level_value_id"],
					"parent": row.get("parent_level_value_id") or parent,
				}
			)
		total_pages = pagination.get("number_of_pages")
		if total_pages:
			if page >= int(total_pages):
				return values
		elif len(rows) < PAGE_SIZE or not fresh:
			return values  # short page: the last one; no new rows: current_page is ignored
	raise GeoSyncError(f"Master Data listed more than {MAX_PAGES} pages of {level_id} under {parent}")


# ---------------------------------------------------------------- sync


def sync_geo_masters(source: MasterDataSource | None = None) -> dict:
	"""Sync all five levels; returns ``{doctype: {inserted, updated, unchanged, deactivated}}``.

	Raises GeoSyncError, MasterDataError or SSOLoginError, with nothing changed, when the
	run is refused (see module doc).
	"""
	source = source or MasterDataSource()
	try:
		counts = _sync(source)
	except Exception:
		frappe.db.rollback()
		raise
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
	return counts


def sync_geo_masters_scheduled() -> None:
	"""Scheduler entry point. Logs the error and returns when the run is refused."""
	try:
		counts = sync_geo_masters()
	except GeoSyncError, MasterDataError, SSOLoginError:
		frappe.log_error(title="Geo master sync skipped")
		frappe.throw("Geo master sync skipped")
	frappe.logger("da_services").info({"geo_master_sync": counts})


def _sync(source) -> dict:
	synced_at = now_datetime()
	level_ids = _level_ids(source.geo_levels())
	counts: dict[str, dict] = {}
	stale_by_doctype: list[tuple[str, list[str]]] = []  # applied once every level passed
	# Codes of the level above, in walk order; the country level has one parent, None.
	parents: list[str | None] = [None]

	for depth, level in enumerate(LEVELS):
		doctype = DOCTYPES[level]
		parent_level = LEVELS[depth - 1] if depth else None
		by_parent = source.geo_level_values_many(level_ids[level], parents)
		existing = _existing_rows(doctype, level, depth)
		level_counts = {"inserted": 0, "updated": 0, "unchanged": 0, "deactivated": 0}
		seen: dict[str, None] = {}  # codes of this level, in walk order

		for parent_code, values in by_parent.items():
			for value in values or []:
				code = str(value.get("code") or "").strip().upper()
				if not code or code in seen:
					continue  # blank, or listed under two parents: the first wins
				link = {parent_level: parent_code} if parent_level else {}
				name = str(value.get("name") or code).strip()
				outcome = _upsert(
					doctype, level, existing.get(code), code, name, link, level_ids[level], synced_at
				)
				level_counts[outcome] += 1
				seen[code] = None

		if not seen:
			raise GeoSyncError(f"Master Data listed no {level} values; nothing was changed")

		stale = [code for code, row in existing.items() if row.is_active and code not in seen]
		active_total = sum(1 for row in existing.values() if row.is_active)
		if stale and len(stale) > MAX_DEACTIVATION_SHARE * active_total:
			raise GeoSyncError(
				f"Master Data no longer lists {len(stale)} of the {active_total} active {doctype} rows; "
				"the answer looks partial, nothing was changed"
			)
		level_counts["deactivated"] = len(stale)
		stale_by_doctype.append((doctype, stale))
		counts[doctype] = level_counts
		parents = list(seen)

	for doctype, stale in stale_by_doctype:
		_bulk_set(doctype, stale, {"is_active": 0, "last_synced_at": synced_at})
	return counts


def _level_ids(levels: list[dict]) -> dict[str, str]:
	by_mnemonic = {lvl.get("mnemonic"): lvl.get("level_id") for lvl in levels if lvl.get("level_id")}
	missing = [m for m in LEVELS if not by_mnemonic.get(m)]
	if missing:
		raise GeoSyncError(f"Master Data has no {', '.join(missing)} level")
	return {m: by_mnemonic[m] for m in LEVELS}


def _existing_rows(doctype: str, level: str, depth: int) -> dict[str, dict]:
	# A level's doctype links to its parent level only (the country has none).
	fields = ["name", f"{level}_name", "is_active", *LEVELS[max(depth - 1, 0) : depth]]
	return {row.name: row for row in frappe.get_all(doctype, fields=fields, limit_page_length=0)}


def _upsert(doctype, level, existing, code, name, link, level_id, synced_at) -> str:
	"""Insert or update one row; returns "inserted", "updated" or "unchanged"."""
	wanted = {f"{level}_name": name, "is_active": 1, **link}
	if existing is None:
		frappe.get_doc(
			{
				"doctype": doctype,
				f"{level}_code": code,
				**wanted,
				"level_id": level_id,
				"last_synced_at": synced_at,
			}
		).insert(ignore_permissions=True)
		return "inserted"
	changed = {k: v for k, v in wanted.items() if (existing.get(k) or None) != (v or None)}
	if not changed:
		return "unchanged"
	frappe.db.set_value(doctype, code, {**changed, "level_id": level_id, "last_synced_at": synced_at})
	return "updated"


def _bulk_set(doctype: str, names: list[str], values: dict) -> None:
	for start in range(0, len(names), _CHUNK):
		frappe.db.set_value(doctype, {"name": ("in", names[start : start + _CHUNK])}, values)
