"""DA Registry client boundary.

DA Services never owns DA master data and never reads another system's database. It
talks to the DA Registry through this interface only. Two implementations exist:

- ``MockDARegistryClient`` (mock.py): in-memory fixtures for local development and tests.
- ``HttpDARegistryClient`` (http.py): the DA Registry staff API (OpenG2P), called server
  to server. Whose token it sends is set per site: the signed-in user's own staff IAM
  token (``"user"``, a wrapper around the registry) or the ``da-services-backend``
  Keycloak service account (``"service"``). Service code is written against the
  interface, so switching is a configuration change.

Select the backend per site in ``site_config.json`` (full example in
``config.example.json``)::

    "da_registry_backend": "mock" | "http",
    "da_registry_auth": "user" | "service",
    "da_registry_base_url": "https://staff-da-registry...",
    "da_registry_timeout_seconds": 10,
    "da_registry_keycloak": {"token_url": "...", "client_id": "...", "client_secret": "..."}
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import frappe

from .schemas import DAReference, IntegrationResult, LifecycleOutcome, ProfileUpdate, SectionChange


class DARegistryError(Exception):
	"""Base class for registry integration failures.

	``code`` is the registry's ``response_error_code`` when it refused the call with an
	OpenG2P ERROR envelope (HTTP 200), else None.
	"""

	def __init__(self, message: str = "", code: str | None = None):
		super().__init__(message)
		self.code = code or None


class DANotFound(DARegistryError):
	pass


class DARegistryUnavailable(DARegistryError):
	"""Transient: timeout, connection error, 5xx. Callers may retry."""


class DARegistryContractPending(DARegistryError):
	"""The HTTP adapter cannot be used until the API contract is confirmed."""


class DARegistryAuthError(DARegistryError):
	"""The registry refused the caller's own token: 401 (missing, expired or not a staff
	IAM token: sign in again) or 403 (the user lacks the registry role for the call).

	Carries ``http_status_code`` so an API that does not catch it answers with that
	status instead of 500, and the frontend can send the user back to sign-in.
	"""

	def __init__(self, message: str, http_status_code: int = 401):
		super().__init__(message)
		self.http_status_code = http_status_code


class DARegistryClient(ABC):
	"""What Part 2 needs from the registry, and nothing more."""

	# ProfileUpdate.changes keys this backend can apply (the registry names in
	# services/profile.py EDITABLE). None: every portal field.
	editable_profile_fields: frozenset[str] | None = None
	# How long a profile change request may wait, right after it is created, for the
	# registry's own decision (seconds). The registry applies a change in a few seconds
	# once its approval stage is off; until then the answer stays PENDING.
	change_request_settle_seconds: float = 0

	@abstractmethod
	def get_da(self, da_id: str) -> DAReference:
		"""Fetch the DA reference. Raises DANotFound / DARegistryUnavailable."""

	@abstractmethod
	def get_da_by_record_id(self, record_id: str) -> DAReference:
		"""The DA whose registry record has this id (the registry's own id of the record,
		which IAM gives a signed-in DA as ``da_id`` and User.da_registry_id holds).

		Raises DANotFound / DARegistryUnavailable.
		"""

	def validate_da_id(self, da_id: str) -> bool:
		try:
			self.get_da(da_id)
			return True
		except DANotFound:
			return False

	@abstractmethod
	def list_das(self, *, woreda: str | None = None, region: str | None = None) -> list[DAReference]:
		"""DAs posted in a Woreda (or Region). Assignment list, My Teams, Supervisor views.

		The registry's "DAs by Woreda" call is still to be confirmed with the registry
		team; the mock answers from its fixtures.
		"""

	@abstractmethod
	def push_profile_update(self, update: ProfileUpdate) -> IntegrationResult:
		"""Apply a Supervisor-approved change to the DA's own profile fields.

		Must be idempotent on ``update.reference``.
		"""

	@abstractmethod
	def push_lifecycle_outcome(self, outcome: LifecycleOutcome) -> IntegrationResult:
		"""Tell the registry an approved lifecycle decision (status change, transfer).

		Must be idempotent on ``outcome.reference``: replaying the same reference returns
		the original result instead of applying the change twice.
		"""

	@abstractmethod
	def import_agents_file(self, filename: str, content: bytes) -> IntegrationResult:
		"""Hand a bulk-import file (the registry's DA import template) to the registry.

		The registry validates each row in the background: a valid row becomes a
		submission on its two-stage approval, a failed row lands in the exceptions list.
		``external_reference`` is the registry's import file id.
		"""

	@abstractmethod
	def list_import_exceptions(self, *, page: int = 1, page_size: int = 50) -> dict:
		"""Import rows the registry could not take, newest first: {"items": [...], "total": n}."""

	@abstractmethod
	def change_request_status(self, reference: str) -> str:
		"""The registry's approval of a pushed profile change: Pending, Approved or Rejected.

		``reference`` is the IntegrationResult.external_reference push_profile_update
		returned. Raises DANotFound when the registry has no such change request.
		"""

	def attribute_values(self, attribute_id: str) -> dict[str, str]:
		"""One Master Data code list, {value_id: label} (DA_POSITION, GENDER, ...).

		Raises DARegistryUnavailable when the list cannot be read right now.
		"""
		raise DARegistryContractPending(f"Master Data list {attribute_id} is not available")

	def upload_document(self, filename: str, content: bytes, content_type: str) -> str:
		"""Store a file in the registry's document store; return its document_id."""
		raise DARegistryContractPending("Registry documents are not available")

	def push_section_change(self, change: SectionChange) -> IntegrationResult:
		"""Open a registry change request on a list section or the photo (DA Registry #66)."""
		raise DARegistryContractPending("Registry list sections are not available")

	def document_links(self, document_ids: list[str]) -> dict[str, str]:
		"""Short-lived links to registry documents (a photo, a certificate), {document_id: url}.

		An id the registry has no link for is left out. Raises DARegistryUnavailable when
		the registry cannot be asked right now.
		"""
		raise DARegistryContractPending("Registry documents are not available")

	@abstractmethod
	def geo_levels(self) -> list[dict]:
		"""Master Data's geography levels: [{"level_id", "mnemonic", "parent_level_id"}].

		``mnemonic`` is country, region, zone, woreda or kebele.
		"""

	@abstractmethod
	def geo_level_values(self, level_id: str, parent: str | None = None) -> list[dict]:
		"""The areas of one level under ``parent`` (the whole level when None):
		[{"code": "ET010101", "name": "Tahtay Adiyabo", "parent": "ET0101"}].
		"""


def get_client(auth_mode: str | None = None) -> DARegistryClient:
	"""Build the configured client. Defaults to the mock outside production.

	``auth_mode`` overrides ``da_registry_auth`` for the http client: a scheduled job or a
	bench command has no signed-in user and passes ``"service"``.
	"""
	backend = (frappe.conf.get("da_registry_backend") or "mock").lower()
	if backend == "mock":
		from .mock import MockDARegistryClient

		return MockDARegistryClient.shared()
	if backend == "http":
		from .http import HttpDARegistryClient

		return HttpDARegistryClient.from_conf(auth_mode=auth_mode)
	raise DARegistryError(f"Unknown da_registry_backend: {backend!r}")
