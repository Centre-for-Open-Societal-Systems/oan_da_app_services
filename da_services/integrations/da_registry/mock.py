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


class MockDARegistryClient(DARegistryClient):
	_instance: MockDARegistryClient | None = None

	def __init__(self, records: dict[str, dict] | None = None):
		# Copy so tests that mutate never leak into each other.
		self._records = {k: dict(v) for k, v in (records or FIXTURE_DAS).items()}
		self._outcomes: dict[str, IntegrationResult] = {}

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
