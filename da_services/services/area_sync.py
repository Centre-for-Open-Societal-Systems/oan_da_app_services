"""Keep DA Administrative Area in step with Master Data's geography.

Region / Zone / Woreda / Kebele and their P-codes are owned by the platform's Master Data
service; DA Services keeps a copy for scope checks, the Kebele picker and the assignment
screens (``/master/areas``, ``/master/kebeles``). ``sync_areas`` walks Master Data level
by level and upserts the rows. An area Master Data no longer lists is deactivated, never
deleted: assignments and visits link to it.

Run once after a deploy, then daily by the scheduler::

    bench --site <site> execute da_services.services.area_sync.sync_areas

Master Data takes the same staff token as the DA Registry. A scheduled job or a bench
command has no signed-in user, so the sync always uses the service identity
(``da_registry_keycloak`` in site_config.json); with the mock backend it loads the demo
geography.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from da_services.integrations.da_registry.client import DARegistryClient, DARegistryError, get_client

SOURCE = "master_data"
# Master Data level mnemonic -> DA Administrative Area level, top first. Country is not kept.
LEVELS = {"region": "Region", "zone": "Zone", "woreda": "Woreda", "kebele": "Kebele"}
_COMPARED = ("level", "parent_area", "name_en", "active")


def sync_areas(client: DARegistryClient | None = None) -> dict:
	"""Upsert every Region / Zone / Woreda / Kebele from Master Data; returns the counts.

	Raises DARegistryError, with nothing changed, when Master Data cannot be read or
	lists no areas at all.
	"""
	client = client or service_client()
	counts = {"inserted": 0, "updated": 0, "unchanged": 0, "deactivated": 0}
	synced_at = now_datetime()
	version = synced_at.isoformat(timespec="seconds")
	seen: set[str] = set()
	parents: list[str | None] = [None]  # a Region has no parent area
	for level in _ordered_levels(client.geo_levels()):
		codes: list[str] = []
		areas_by_parent = client.geo_level_values_many(level["level_id"], parents)
		for parent in parents:
			for area in areas_by_parent.get(parent) or []:
				code = _upsert(area, LEVELS[level["mnemonic"]], parent, version, synced_at, counts)
				seen.add(code)
				codes.append(code)
		parents = codes
	if not seen:
		frappe.db.rollback()
		raise DARegistryError("Master Data listed no areas; the administrative areas were left as they were")
	counts["deactivated"] = _deactivate_missing(seen, synced_at)
	frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit
	return counts


def sync_areas_scheduled() -> None:
	"""Daily. Logs and skips when Master Data cannot be read or the site has no service identity."""
	try:
		sync_areas()
	except DARegistryError:
		frappe.log_error(title="Administrative area sync skipped")


def service_client() -> DARegistryClient:
	"""The client for a run with no signed-in user: the mock, or http on the service identity."""
	if str(frappe.conf.get("da_registry_backend") or "mock").lower() == "mock":
		return get_client()
	from da_services.integrations.da_registry.http import HttpDARegistryClient

	return HttpDARegistryClient.from_conf(auth_mode="service")


def _ordered_levels(levels: list[dict]) -> list[dict]:
	by_mnemonic = {lvl.get("mnemonic"): lvl for lvl in levels if lvl.get("mnemonic") in LEVELS}
	missing = [m for m in LEVELS if m not in by_mnemonic]
	if missing:
		raise DARegistryError(f"Master Data has no {', '.join(missing)} level")
	return [by_mnemonic[m] for m in LEVELS]


def _upsert(area: dict, level: str, parent: str | None, version: str, synced_at, counts: dict) -> str:
	code = str(area["code"]).strip().upper()
	name = str(area.get("name") or code).strip()
	wanted = {"level": level, "parent_area": parent, "name_en": name, "active": 1}
	stamp = {"source": SOURCE, "source_version": version, "last_synced_at": synced_at}
	existing = frappe.db.get_value("DA Administrative Area", code, list(_COMPARED), as_dict=True)
	if existing is None:
		frappe.get_doc({"doctype": "DA Administrative Area", "code": code, **wanted, **stamp}).insert(
			ignore_permissions=True
		)
		counts["inserted"] += 1
		return code
	changed = {k: v for k, v in wanted.items() if (existing.get(k) or None) != (v or None)}
	frappe.db.set_value("DA Administrative Area", code, {**changed, **stamp}, update_modified=bool(changed))
	counts["updated" if changed else "unchanged"] += 1
	return code


def _deactivate_missing(seen: set[str], synced_at) -> int:
	stale = [
		code
		for code in frappe.get_all(
			"DA Administrative Area", filters={"source": SOURCE, "active": 1}, pluck="name"
		)
		if code not in seen
	]
	for code in stale:
		frappe.db.set_value("DA Administrative Area", code, {"active": 0, "last_synced_at": synced_at})
	return len(stale)
