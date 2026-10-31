"""DA Registry client boundary.

DA Services never owns DA master data and never reads another system's database. It
talks to the DA Registry through this interface only. Two implementations exist:

- ``MockDARegistryClient`` (mock.py): in-memory fixtures for local development and tests.
- ``HttpDARegistryClient`` (http.py): the DA Registry staff API (OpenG2P), called server
  to server with the ``da-services-backend`` Keycloak service account. Service code is
  written against the interface, so switching is a configuration change.

Select the backend per site in ``site_config.json`` (full example in
``config.example.json``)::

    "da_registry_backend": "mock" | "http",
    "da_registry_base_url": "https://staff-da-registry...",
    "da_registry_timeout_seconds": 10,
    "da_registry_keycloak": {"token_url": "...", "client_id": "...", "client_secret": "..."}
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import frappe

from .schemas import DAReference, IntegrationResult, LifecycleOutcome, ProfileUpdate


class DARegistryError(Exception):
	"""Base class for registry integration failures."""


class DANotFound(DARegistryError):
	pass


class DARegistryUnavailable(DARegistryError):
	"""Transient: timeout, connection error, 5xx. Callers may retry."""


class DARegistryContractPending(DARegistryError):
	"""The HTTP adapter cannot be used until the API contract is confirmed."""


class DARegistryClient(ABC):
	"""What Part 2 needs from the registry, and nothing more."""

	@abstractmethod
	def get_da(self, da_id: str) -> DAReference:
		"""Fetch the DA reference. Raises DANotFound / DARegistryUnavailable."""

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


def get_client() -> DARegistryClient:
	"""Build the configured client. Defaults to the mock outside production."""
	backend = (frappe.conf.get("da_registry_backend") or "mock").lower()
	if backend == "mock":
		from .mock import MockDARegistryClient

		return MockDARegistryClient.shared()
	if backend == "http":
		from .http import HttpDARegistryClient

		return HttpDARegistryClient.from_conf()
	raise DARegistryError(f"Unknown da_registry_backend: {backend!r}")
