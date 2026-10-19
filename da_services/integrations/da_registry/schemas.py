"""Typed shapes exchanged with the DA Registry.

These are *our* projection of registry data, not the registry's contract. Whatever the
final API looks like (OpenG2P Gen 2 directly or the Part 1 FastAPI services, still to be
confirmed), mapper.py translates into these and the rest of the app never sees raw
payloads. Keep them minimal: DA Services holds references and read models, never a
second DA master (HLD 2.2).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class DAReference:
	"""The slice of a DA record Part 2 needs to act on a DA."""

	da_id: str
	full_name: str
	active_status: str  # Active / On-leave / Separated (FSD 3.2.1 col 5)
	approval_status: str  # Draft / Awaiting review / Approved / Published (col 9)
	region: str | None = None
	zone: str | None = None
	woreda: str | None = None
	kebele: str | None = None
	education_tier: str | None = None
	fayda_status: str | None = None  # Verified / Pending / Mismatch (col 4)
	# Shown in the portal header, My Teams and Assignments (docs/api-da-screens.md 1.1, 2.12).
	phone: str | None = None
	specialisation: str | None = None
	joined_at: str | None = None  # ISO date the DA joined service
	farmer_count: int | None = None  # farmers linked to the DA's Kebele(s)
	email: str | None = None
	language: str | None = None  # en / am / om / ti
	photo_url: str | None = None
	source_version: str | None = None  # registry record version, for optimistic checks
	fetched_at: datetime | None = None

	def to_dict(self) -> dict:
		d = asdict(self)
		if self.fetched_at:
			d["fetched_at"] = self.fetched_at.isoformat()
		return d


@dataclass(frozen=True)
class LifecycleOutcome:
	"""An approved Part 2 decision the registry must reflect (FSD 3.2.1: Active Status is
	set by Part 2 lifecycle; HLD 6.1: separation/retirement sync to MoA via the registry)."""

	da_id: str
	transition_type: str  # Transfer / Promotion / Leave / Retirement / Reactivation / Separation ...
	new_active_status: str | None
	effective_date: str  # ISO date
	reason: str
	approver: str
	reference: str  # our Lifecycle Transition name, for idempotency and audit
	target_kebele: str | None = None  # Transfer only


@dataclass(frozen=True)
class ProfileUpdate:
	"""A Supervisor-approved change to a DA's own profile fields (FSD 3.7)."""

	da_id: str
	changes: dict  # registry field name -> new value
	approver: str
	reference: str  # our DA Profile Change Request name, for idempotency and audit


@dataclass(frozen=True)
class IntegrationResult:
	"""What came back from the registry for one command."""

	accepted: bool
	external_reference: str | None = None
	message: str | None = None
	retryable: bool = False
	raw: dict = field(default_factory=dict)
