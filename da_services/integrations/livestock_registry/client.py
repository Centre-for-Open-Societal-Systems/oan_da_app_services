"""Livestock Registry client interface.

Staff host and Livestock ``register_id`` come from DA Services Settings via
``get_livestock_registry_client``. Auth is the caller's SSO bearer and CSRF.
There is no ``get_all_registers`` discovery. GET list requires ``source`` (one
staff search); register detail uses ``tab_id`` via ``get_tab_records``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .schemas import LivestockSubmission


class LivestockRegistryError(Exception):
	"""Base exception for Livestock Registry integration errors."""

	def __init__(self, message: str, status_code: int | None = None):
		super().__init__(message)
		self.status_code = status_code


class LivestockRegistryClientError(LivestockRegistryError):
	"""The registry rejected the request (HTTP 4xx or envelope ERROR)."""


class LivestockRegistryServerError(LivestockRegistryError):
	"""The registry failed or was unreachable (HTTP 5xx, timeout, connection)."""


class LivestockRegistryClient(ABC):
	"""Operations against the Livestock registry."""

	@abstractmethod
	def describe_intake_form(self) -> dict:
		"""The intake form for the frontend: sections, widgets, rules and catalogs."""

	@abstractmethod
	def submit_sections(
		self,
		requests: list[dict],
		*,
		submission_id: str | None = None,
		subject_record_id: str | None = None,
		farmer_id: str | None = None,
	) -> dict:
		"""Validate records against intake metadata, then save each section."""

	@abstractmethod
	def finalize_submission(self, submission_id: str) -> dict:
		"""Mark a draft submission FINAL and pending approval."""

	@abstractmethod
	def get_submission(self, submission_id: str) -> LivestockSubmission | None:
		"""Read one intake submission. None when the payload has no submission."""

	@abstractmethod
	def list_for_farmer(self, farmer_id: str, *, source: str) -> list[dict]:
		"""One staff search: ``source`` is ``submission`` or ``register``."""

	@abstractmethod
	def get_register_tab(self, internal_record_id: str, tab_id: str) -> dict:
		"""One register tab via ``/register-data/get_tab_records``."""
