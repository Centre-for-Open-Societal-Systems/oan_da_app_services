"""In-memory DA Registry for development and tests.

Fixture DAs cover the states Part 2 has to handle: an active published DA, one on
leave, one separated, and one still awaiting Woreda review (not yet Active, so no
lifecycle action should be allowed on it).

Geography on the fixtures is always rewritten from the site's synced ``DA Region`` /
Zone / Woreda / Kebele masters (``geo_master_sync``). If masters are empty, alignment
fails closed — no invented placeholder codes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import frappe
from frappe import _

from da_services.services import constants as C
from da_services.services.geo import masters_nonempty

from .client import DANotFound, DARegistryClient
from .mapper import to_da_reference
from .schemas import DAReference, IntegrationResult, LifecycleOutcome, ProfileUpdate

# Identity + status fixtures. Geography fields are filled by ``fixture_das_aligned``
# from live masters only (never invent codes here).
FIXTURE_DAS: dict[str, dict] = {
	"DA-000001": {
		"da_id": "DA-000001",
		"full_name": "Abebe Kebede",
		"active_status": C.ACTIVE_STATUS_ACTIVE,
		"approval_status": "Published",
		"region": None,
		"zone": None,
		"woreda": None,
		"assigned_kebele": None,
		"education_tier": "Diploma",
		"fayda_status": "Verified",
		"phone": "+251911000001",
		"specialisation": "Crop production",
		"joined_at": "2019-09-01",
		"farmer_count": 128,
		"version": 3,
	},
	"DA-000002": {
		"da_id": "DA-000002",
		"full_name": "Marta Tesfaye",
		"active_status": C.ACTIVE_STATUS_ON_LEAVE,
		"approval_status": "Published",
		"region": None,
		"zone": None,
		"woreda": None,
		"assigned_kebele": None,
		"education_tier": "Degree",
		"fayda_status": "Verified",
		"phone": "+251911000002",
		"specialisation": "Livestock",
		"joined_at": "2017-03-15",
		"farmer_count": 96,
		"version": 5,
	},
	"DA-000003": {
		"da_id": "DA-000003",
		"full_name": "Yonas Alemu",
		"active_status": C.ACTIVE_STATUS_SEPARATED,
		"approval_status": "Published",
		"region": None,
		"zone": None,
		"woreda": None,
		"assigned_kebele": None,
		"education_tier": "Diploma",
		"fayda_status": "Verified",
		"phone": "+251911000003",
		"specialisation": "Natural resources",
		"joined_at": "2012-01-10",
		"farmer_count": 0,
		"version": 9,
	},
	"DA-000004": {
		"da_id": "DA-000004",
		"full_name": "Hana Girma",
		"active_status": C.ACTIVE_STATUS_ACTIVE,
		"approval_status": "Awaiting review",
		"region": None,
		"zone": None,
		"woreda": None,
		"assigned_kebele": None,
		"education_tier": "Degree",
		"fayda_status": "Pending",
		"phone": "+251911000004",
		"specialisation": "Crop production",
		"joined_at": "2026-07-01",
		"farmer_count": 0,
		"version": 1,
	},
}


# Invented codes from the retired Admin Area era. Not a geography source — only a
# denylist for ``remove_unsynced_fixture_geo`` to delete leftover dummy Desk rows.
# Live geography always comes from ``live_fixture_geo`` / synced masters.
GEO_LEVELS = ("region", "zone", "woreda", "kebele")
LEGACY_DUMMY_GEO = (
	("ET04-Z01", "zone", "ET04", "West Shewa"),
	("ET04-W01", "woreda", "ET04-Z01", "Bako Tibe"),
	("ET04-W01-K01", "kebele", "ET04-W01", "Bako 01"),
	("ET04-W01-K02", "kebele", "ET04-W01", "Bako 02"),
	("ET04-W01-K03", "kebele", "ET04-W01", "Dambi Gobu"),
	("ET04-W01-K04", "kebele", "ET04-W01", "Koye Feche"),
	("ET04-W01-K05", "kebele", "ET04-W01", "Amarti Gibe"),
	("ET04-W01-K07", "kebele", "ET04-W01", "Gombo"),
	("ET03-Z02", "zone", "ET03", "North Shewa"),
	("ET03-W02", "woreda", "ET03-Z02", "Ankober"),
	("ET03-W02-K01", "kebele", "ET03-W02", "Ankober 01"),
)

_GEO_DOCTYPE = {
	"region": ("DA Region", "region_name", None),
	"zone": ("DA Zone", "zone_name", "region"),
	"woreda": ("DA Woreda", "woreda_name", "zone"),
	"kebele": ("DA Kebele", "kebele_name", "woreda"),
}


def _chain_from_woreda(woreda: str) -> dict | None:
	zone = frappe.db.get_value("DA Woreda", woreda, "zone")
	region = frappe.db.get_value("DA Zone", zone, "region") if zone else None
	kebeles = frappe.get_all(
		"DA Kebele",
		filters={"is_active": 1, "woreda": woreda},
		pluck="name",
		order_by="name asc",
		limit=2,
	)
	if not (region and zone and kebeles):
		return None
	return {
		"region": region,
		"zone": zone,
		"woreda": woreda,
		"kebele": kebeles[0],
		"kebele_alt": kebeles[1] if len(kebeles) > 1 else kebeles[0],
	}


def live_fixture_geo() -> tuple[dict, dict]:
	"""Primary + other geography chains from synced masters only.

	Fails closed when masters are empty or no usable chain exists — never invents codes.
	"""
	if not masters_nonempty():
		frappe.throw(
			_(
				"Geography masters are empty. Run "
				"`bench --site <site> execute da_services.services.geo_master_sync.sync_geo_masters` "
				"before using the mock DA Registry."
			),
			frappe.ValidationError,
		)

	# Prefer a Woreda with 2+ Kebeles under a Region that also has a second Zone.
	counts = frappe.db.sql(
		"""
		SELECT woreda, COUNT(*) AS n
		FROM `tabDA Kebele`
		WHERE is_active = 1 AND IFNULL(woreda, '') != ''
		GROUP BY woreda
		ORDER BY n DESC, woreda ASC
		LIMIT 20
		""",
		as_dict=True,
	)
	primary = None
	other = None
	for row in counts:
		chain = _chain_from_woreda(row.woreda)
		if not chain:
			continue
		if primary is None:
			primary = chain
			continue
		if chain["region"] != primary["region"] or chain["zone"] != primary["zone"]:
			other = chain
			break
	if primary is None:
		frappe.throw(
			_("No active Region→Zone→Woreda→Kebele chain in geography masters."),
			frappe.ValidationError,
		)
	if other is None:
		# Same region, different zone if possible; else reuse primary for "other".
		zones = frappe.get_all(
			"DA Zone",
			filters={"is_active": 1, "region": primary["region"], "name": ("!=", primary["zone"])},
			pluck="name",
			limit=5,
		)
		for zone in zones:
			woreda = frappe.get_all(
				"DA Woreda", filters={"is_active": 1, "zone": zone}, pluck="name", limit=1
			)
			if not woreda:
				continue
			other = _chain_from_woreda(woreda[0])
			if other:
				break
	if other is None:
		other = dict(primary)
	return primary, other


def fixture_das_aligned() -> dict[str, dict]:
	"""FIXTURE_DAS with region/zone/woreda/kebele from live masters (required)."""
	primary, other = live_fixture_geo()
	records = {k: dict(v) for k, v in FIXTURE_DAS.items()}
	for da_id, geo in (
		("DA-000001", primary),
		("DA-000002", {**primary, "kebele": primary["kebele_alt"]}),
		("DA-000003", {**other, "kebele": None}),
		("DA-000004", other),
	):
		row = records[da_id]
		row["region"] = geo["region"]
		row["zone"] = geo["zone"]
		row["woreda"] = geo["woreda"]
		row["assigned_kebele"] = geo.get("kebele")
	return records


class MockDARegistryClient(DARegistryClient):
	_instance: MockDARegistryClient | None = None

	def __init__(self, records: dict[str, dict] | None = None):
		# Copy so tests that mutate never leak into each other. Default records always
		# come from live masters (``fixture_das_aligned``); empty masters fail closed.
		self._records = {k: dict(v) for k, v in (records or fixture_das_aligned()).items()}
		self._outcomes: dict[str, IntegrationResult] = {}
		self.imports: list[tuple[str, bytes]] = []
		self.import_exceptions: list[dict] = []

	@classmethod
	def shared(cls) -> MockDARegistryClient:
		"""One instance per process so pushed outcomes are visible across calls."""
		if cls._instance is None:
			cls._instance = cls()
		return cls._instance

	@classmethod
	def reset(cls) -> None:
		cls._instance = None

	def get_da(self, da_id: str) -> DAReference:
		payload = self._records.get(da_id)
		if not payload:
			raise DANotFound(da_id)
		return to_da_reference(payload)

	def list_das(self, *, woreda: str | None = None, region: str | None = None) -> list[DAReference]:
		rows = []
		for payload in self._records.values():
			if woreda and payload.get("woreda") != woreda:
				continue
			if region and payload.get("region") != region:
				continue
			rows.append(to_da_reference(payload))
		return sorted(rows, key=lambda r: r.da_id)

	def push_profile_update(self, update: ProfileUpdate) -> IntegrationResult:
		if update.reference in self._outcomes:
			return self._outcomes[update.reference]  # idempotent replay
		payload = self._records.get(update.da_id)
		if not payload:
			raise DANotFound(update.da_id)
		payload.update(update.changes)
		payload["version"] = int(payload.get("version") or 0) + 1
		result = IntegrationResult(
			accepted=True,
			external_reference=f"MOCK-{update.reference}",
			message="accepted by mock registry",
			raw={"applied_at": datetime.now(UTC).isoformat(), "version": payload["version"]},
		)
		self._outcomes[update.reference] = result
		return result

	def push_lifecycle_outcome(self, outcome: LifecycleOutcome) -> IntegrationResult:
		if outcome.reference in self._outcomes:
			return self._outcomes[outcome.reference]  # idempotent replay
		payload = self._records.get(outcome.da_id)
		if not payload:
			raise DANotFound(outcome.da_id)
		if outcome.new_active_status:
			payload["active_status"] = outcome.new_active_status
		if outcome.target_kebele:
			payload["assigned_kebele"] = outcome.target_kebele
		payload["version"] = int(payload.get("version") or 0) + 1
		result = IntegrationResult(
			accepted=True,
			external_reference=f"MOCK-{outcome.reference}",
			message="accepted by mock registry",
			raw={"applied_at": datetime.now(UTC).isoformat(), "version": payload["version"]},
		)
		self._outcomes[outcome.reference] = result
		return result

	def import_agents_file(self, filename: str, content: bytes) -> IntegrationResult:
		self.imports.append((filename, content))
		return IntegrationResult(
			accepted=True,
			external_reference=f"MOCK-IMPORT-{len(self.imports)}",
			message="queued by mock registry",
		)

	def list_import_exceptions(self, *, page: int = 1, page_size: int = 50) -> dict:
		start = (page - 1) * page_size
		return {
			"items": self.import_exceptions[start : start + page_size],
			"total": len(self.import_exceptions),
		}

	def change_request_status(self, reference: str) -> str:
		# The mock applies a profile update at once, so it is already approved. Its own
		# references are known by prefix: the scheduler runs in another process, whose
		# fresh mock never saw the push.
		if not str(reference or "").startswith("MOCK-"):
			raise DANotFound(reference)
		return "Approved"

	def geo_levels(self) -> list[dict]:
		return [{"level_id": "l0", "mnemonic": "country", "parent_level_id": None}] + [
			{"level_id": f"l{i + 1}", "mnemonic": mnemonic, "parent_level_id": f"l{i}"}
			for i, mnemonic in enumerate(GEO_LEVELS)
		]

	def geo_level_values(self, level_id: str, parent: str | None = None) -> list[dict]:
		"""Active master rows for the named level (same tree as Desk Links)."""
		index = int(str(level_id).lstrip("l")) - 1
		if not 0 <= index < len(GEO_LEVELS):
			return []
		level = GEO_LEVELS[index]
		doctype, name_field, parent_field = _GEO_DOCTYPE[level]
		filters: dict = {"is_active": 1}
		if parent_field and parent:
			filters[parent_field] = parent
		fields = ["name as code", f"{name_field} as name"]
		if parent_field:
			fields.append(f"{parent_field} as parent")
		rows = frappe.get_all(doctype, filters=filters, fields=fields, order_by=f"{name_field} asc")
		return [
			{"code": r.code, "name": r.name, "parent": r.get("parent") if parent_field else None}
			for r in rows
		]
