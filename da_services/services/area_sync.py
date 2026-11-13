"""Keep DA Administrative Area in step with Master Data's geography.

Region / Zone / Woreda / Kebele and their P-codes are owned by the platform's Master Data
service; DA Services keeps a copy for scope checks, the Kebele picker and the assignment
screens (``/master/areas``, ``/master/kebeles``). ``sync_areas`` walks Master Data level
by level and upserts the rows. An area Master Data no longer lists is deactivated, never
deleted: assignments and visits link to it.

Run once after a deploy, then daily by the scheduler (long queue: a full tree is about a
thousand calls)::

    bench --site <site> execute da_services.services.area_sync.sync_areas

Master Data takes the same staff token as the DA Registry. A scheduled job or a bench
command has no signed-in user, so the sync always uses the service identity
(``da_registry_keycloak`` in site_config.json); with the mock backend it loads the demo
geography.

A run that fails half-way, finds a level empty, or would deactivate more than a fifth of
the areas it owns (Master Data answered partially) changes nothing.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from da_services.integrations.da_registry.client import DARegistryClient, DARegistryError, get_client

SOURCE = "master_data"
# Master Data level mnemonic -> DA Administrative Area level, top first. Country is not kept.
LEVELS = {"region": "Region", "zone": "Zone", "woreda": "Woreda", "kebele": "Kebele"}
_COMPARED = ("level", "parent_area", "name_en", "active")
# A country's geography does not lose a fifth of its areas in a day: refuse such a run.
MAX_DEACTIVATION_SHARE = 0.2
_CHUNK = 1000  # names per bulk UPDATE


def sync_areas(client: DARegistryClient | None = None) -> dict:
	"""Upsert every Region / Zone / Woreda / Kebele from Master Data; returns the counts.

	Raises DARegistryError, with nothing changed, when Master Data cannot be read, lists
	no areas on a level, or would deactivate more than MAX_DEACTIVATION_SHARE of the rows
	the sync owns.
	"""
	client = client or get_client(auth_mode="service")
	try:
		counts = _sync(client)
	except Exception:
		frappe.db.rollback()
		raise
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
	return counts


def sync_areas_scheduled() -> None:
	"""Daily. Logs and skips when Master Data cannot be read or the site has no service identity."""
	try:
		sync_areas()
	except DARegistryError:
		frappe.log_error(title="Administrative area sync skipped")


def _sync(client: DARegistryClient) -> dict:
	counts = {"inserted": 0, "updated": 0, "unchanged": 0, "deactivated": 0}
	synced_at = now_datetime()
	version = synced_at.isoformat(timespec="seconds")
	existing = _existing_rows()
	seen: dict[str, None] = {}  # row names, in walk order
	unchanged: list[str] = []
	# (Master Data id to ask the children for, our row name for parent_area); a Region has none.
	parents: list[tuple[str | None, str | None]] = [(None, None)]
	for level in _ordered_levels(client.geo_levels()):
		level_name = LEVELS[level["mnemonic"]]
		areas_by_parent = client.geo_level_values_many(level["level_id"], [p[0] for p in parents])
		next_parents: list[tuple[str | None, str | None]] = []
		for md_parent, parent_row in parents:
			for area in areas_by_parent.get(md_parent) or []:
				code = str(area["code"]).strip().upper()
				if code in seen:
					continue  # listed twice: the first parent wins
				seen[code] = None
				name = str(area.get("name") or code).strip()
				outcome = _upsert(existing.get(code), code, level_name, parent_row, name, version, synced_at)
				counts[outcome] += 1
				if outcome == "unchanged":
					unchanged.append(code)
				next_parents.append((str(area["code"]), code))
		if not next_parents:
			raise DARegistryError(
				f"Master Data listed no {level_name} areas; the administrative areas were left as they were"
			)
		parents = next_parents

	stale = [
		code for code, row in existing.items() if row.source == SOURCE and row.active and code not in seen
	]
	if stale and len(stale) > MAX_DEACTIVATION_SHARE * (len(stale) + len(seen)):
		raise DARegistryError(
			f"Master Data no longer lists {len(stale)} of the {len(stale) + len(seen)} areas DA Services holds; "
			"the answer looks partial, the administrative areas were left as they were"
		)
	_bulk_set(
		unchanged,
		{"source": SOURCE, "source_version": version, "last_synced_at": synced_at},
		update_modified=False,
	)
	_bulk_set(stale, {"active": 0, "last_synced_at": synced_at})
	counts["deactivated"] = len(stale)
	return counts


def _ordered_levels(levels: list[dict]) -> list[dict]:
	by_mnemonic = {lvl.get("mnemonic"): lvl for lvl in levels if lvl.get("mnemonic") in LEVELS}
	missing = [m for m in LEVELS if m not in by_mnemonic]
	if missing:
		raise DARegistryError(f"Master Data has no {', '.join(missing)} level")
	return [by_mnemonic[m] for m in LEVELS]


def _existing_rows() -> dict[str, dict]:
	rows = frappe.get_all(
		"DA Administrative Area", fields=["name", "source", *_COMPARED], limit_page_length=0
	)
	return {row.name: row for row in rows}


def _upsert(existing, code: str, level: str, parent: str | None, name: str, version: str, synced_at) -> str:
	"""Insert or update one area; returns "inserted", "updated" or "unchanged"."""
	wanted = {"level": level, "parent_area": parent, "name_en": name, "active": 1}
	stamp = {"source": SOURCE, "source_version": version, "last_synced_at": synced_at}
	if existing is None:
		frappe.get_doc({"doctype": "DA Administrative Area", "code": code, **wanted, **stamp}).insert(
			ignore_permissions=True
		)
		return "inserted"
	changed = {k: v for k, v in wanted.items() if (existing.get(k) or None) != (v or None)}
	if not changed:
		return "unchanged"  # stamped in bulk at the end
	frappe.db.set_value("DA Administrative Area", code, {**changed, **stamp})
	return "updated"


def _bulk_set(names: list[str], values: dict, *, update_modified: bool = True) -> None:
	for start in range(0, len(names), _CHUNK):
		frappe.db.set_value(
			"DA Administrative Area",
			{"name": ("in", names[start : start + _CHUNK])},
			values,
			update_modified=update_modified,
		)
