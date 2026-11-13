"""In-memory DA Registry for development and tests.

Fixture DAs cover the states Part 2 has to handle: an active published DA, one on
leave, one separated, and one still awaiting Woreda review (not yet Active, so no
lifecycle action should be allowed on it).
"""

from __future__ import annotations

from datetime import UTC, datetime

from da_services.services import constants as C

from .client import DANotFound, DARegistryClient
from .mapper import to_da_reference
from .schemas import DAReference, IntegrationResult, LifecycleOutcome, ProfileUpdate

FIXTURE_DAS: dict[str, dict] = {
	"DA-000001": {
		"da_id": "DA-000001",
		"full_name": "Abebe Kebede",
		"active_status": C.ACTIVE_STATUS_ACTIVE,
		"approval_status": "Published",
		"region": "ET04",
		"zone": "ET04-Z01",
		"woreda": "ET04-W01",
		"assigned_kebele": "ET04-W01-K03",
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
		"region": "ET04",
		"zone": "ET04-Z01",
		"woreda": "ET04-W01",
		"assigned_kebele": "ET04-W01-K07",
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
		"region": "ET03",
		"zone": "ET03-Z02",
		"woreda": "ET03-W02",
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
		"region": "ET03",
		"zone": "ET03-Z02",
		"woreda": "ET03-W02",
		"assigned_kebele": "ET03-W02-K01",
		"education_tier": "Degree",
		"fayda_status": "Pending",
		"phone": "+251911000004",
		"specialisation": "Crop production",
		"joined_at": "2026-07-01",
		"farmer_count": 0,
		"version": 1,
	},
}


# Master Data geography of the demo site; setup/seed_demo.py seeds the same list: (code, level, parent, name)
GEO_LEVELS = ("region", "zone", "woreda", "kebele")
FIXTURE_GEO = (
	("ET04", "region", None, "Oromia"),
	("ET04-Z01", "zone", "ET04", "West Shewa"),
	("ET04-W01", "woreda", "ET04-Z01", "Bako Tibe"),
	("ET04-W01-K01", "kebele", "ET04-W01", "Bako 01"),
	("ET04-W01-K02", "kebele", "ET04-W01", "Bako 02"),
	("ET04-W01-K03", "kebele", "ET04-W01", "Dambi Gobu"),
	("ET04-W01-K04", "kebele", "ET04-W01", "Koye Feche"),
	("ET04-W01-K05", "kebele", "ET04-W01", "Amarti Gibe"),
	("ET04-W01-K07", "kebele", "ET04-W01", "Gombo"),
	("ET03", "region", None, "Amhara"),
	("ET03-Z02", "zone", "ET03", "North Shewa"),
	("ET03-W02", "woreda", "ET03-Z02", "Ankober"),
	("ET03-W02-K01", "kebele", "ET03-W02", "Ankober 01"),
)


# Master Data code lists of the demo site, {attribute_id: {value_id: label}}
FIXTURE_ATTRIBUTES: dict[str, dict[str, str]] = {
	"DA_EDUCATION_LEVEL": {
		"CERTIFICATE": "Certificate",
		"DIPLOMA": "Diploma",
		"DEGREE": "Degree",
		"MASTERS": "Masters",
	},
	"DA_SPECIALISATION": {
		"CROP_PRODUCTION": "Crop production",
		"LIVESTOCK": "Livestock",
		"NATURAL_RESOURCES": "Natural resources",
	},
	"DA_POSITION": {"DEVELOPMENT_AGENT": "Development agent", "SENIOR_DA": "Senior DA"},
	"PREFIX": {"ATO": "Ato", "WROS": "W/ro", "DR": "Dr"},
	"GENDER": {"MALE": "Male", "FEMALE": "Female"},
	"MARITAL_STATUS": {
		"SINGLE": "Single",
		"MARRIED": "Married",
		"DIVORCED": "Divorced",
		"WIDOWED": "Widowed",
	},
}
COUNTRY = ("ET", "Ethiopia")

# ProfileUpdate.changes key -> (fixture key, Master Data list whose label the fixture holds)
_PROFILE_KEYS = {
	"phone": ("phone", None),
	"telegram_number": ("telegram_number", None),
	"alternate_number": ("alternate_number", None),
	"email": ("email", None),
	"education_tier": ("education_tier", "DA_EDUCATION_LEVEL"),
	"specialisation": ("specialisation", "DA_SPECIALISATION"),
	"position": ("position", "DA_POSITION"),
	"joined_date": ("joined_at", None),
	"prefix": ("salutation", "PREFIX"),
	"first_name": ("first_name", None),
	"father_name": ("father_name", None),
	"grandfather_name": ("grandfather_name", None),
	"fayda_id": ("fayda_id", None),
	"gender": ("gender", "GENDER"),
	"marital_status": ("marital_status", "MARITAL_STATUS"),
	"birth_month_ec": ("birth_month_ec", None),
	"birth_year_ec": ("birth_year_ec", None),
	"language": ("language", None),
	"photo_url": ("photo_url", None),
}


class MockDARegistryClient(DARegistryClient):
	_instance: MockDARegistryClient | None = None

	def __init__(self, records: dict[str, dict] | None = None):
		# Copy so tests that mutate never leak into each other.
		self._records = {k: dict(v) for k, v in (records or FIXTURE_DAS).items()}
		self._outcomes: dict[str, IntegrationResult] = {}
		self.imports: list[tuple[str, bytes]] = []
		self.import_exceptions: list[dict] = []
		self.pushed: list[ProfileUpdate] = []  # every profile change request created

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
		self._apply_profile_changes(payload, update.changes)
		payload["version"] = int(payload.get("version") or 0) + 1
		self.pushed.append(update)
		result = IntegrationResult(
			accepted=True,
			external_reference=f"MOCK-{update.reference}",
			message="accepted by mock registry",
			raw={"applied_at": datetime.now(UTC).isoformat(), "version": payload["version"]},
		)
		self._outcomes[update.reference] = result
		return result

	@staticmethod
	def _apply_profile_changes(payload: dict, changes: dict) -> None:
		"""Write the registry-side change onto the fixture, as the registry would."""
		for key, value in changes.items():
			if key == "kebele":
				payload["assigned_kebele"] = value
			elif key == "geo_code_hierarchy_json":
				for row in (value or {}).get("hierarchy") or []:
					if row.get("level") in ("region", "zone", "woreda"):
						payload[row["level"]] = row.get("level_value_id")
			elif key in _PROFILE_KEYS:
				field, attribute = _PROFILE_KEYS[key]
				labels = FIXTURE_ATTRIBUTES.get(attribute) or {}
				payload[field] = labels.get(value, value)
			else:
				payload[key] = value
		names = [payload.get(k) for k in ("first_name", "father_name", "grandfather_name")]
		if any(k in changes for k in ("first_name", "father_name", "grandfather_name")) and any(names):
			payload["full_name"] = " ".join(n for n in names if n)

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

	def attribute_values(self, attribute_id: str) -> dict[str, str]:
		return dict(FIXTURE_ATTRIBUTES.get(attribute_id) or {})

	def geo_levels(self) -> list[dict]:
		return [{"level_id": "l0", "mnemonic": "country", "parent_level_id": None}] + [
			{"level_id": f"l{i + 1}", "mnemonic": mnemonic, "parent_level_id": f"l{i}"}
			for i, mnemonic in enumerate(GEO_LEVELS)
		]

	def geo_level_values(self, level_id: str, parent: str | None = None) -> list[dict]:
		index = int(str(level_id).lstrip("l")) - 1
		if index == -1:
			return [{"code": COUNTRY[0], "name": COUNTRY[1], "parent": None}]
		if not 0 <= index < len(GEO_LEVELS):
			return []
		return [
			{"code": code, "name": name, "parent": parent_code}
			for code, level, parent_code, name in FIXTURE_GEO
			if level == GEO_LEVELS[index] and (parent is None or parent_code == parent)
		]
