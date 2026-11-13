"""Livestock Registry client interface and factory.

Same staff platform as Crop Sown: discover the Livestock register and its one
intake form, save one section at a time into a draft, then finalize it for
approval. The form's main sections (farmer lookup, survey personnel, location)
are the Livestock register row; animals, health events, vaccinations, vital
events and breeding are list sections on their own registers, linked to it.
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
	"""The registry, IAM or Keycloak rejected the request (HTTP 4xx or envelope ERROR)."""


class LivestockRegistryServerError(LivestockRegistryError):
	"""The registry, IAM or Keycloak failed or was unreachable (HTTP 5xx, timeout, connection)."""


class LivestockRegistryClient(ABC):
	"""Operations against the Livestock registry."""

	@abstractmethod
	def describe_intake_form(self) -> dict:
		"""The intake form for the frontend: sections, widgets, rules and loaded catalog rows."""

	@abstractmethod
	def geo_level_values(self, level_id: str, parent_level_value_id: str | None = None) -> list[dict]:
		"""Values for one geography level. A child level passes the parent ``level_value_id``."""

	@abstractmethod
	def submit_sections(
		self,
		requests: list[dict],
		*,
		submission_id: str | None = None,
		subject_record_id: str | None = None,
		farmer_id: str | None = None,
	) -> dict:
		"""Validate records against the intake metadata, then save each section into one draft.

		``farmer_id`` is the Farmer-ID the draft is for; it fills the farmer lookup field.
		"""

	@abstractmethod
	def finalize_submission(self, submission_id: str) -> dict:
		"""Mark a draft submission FINAL and pending approval."""

	@abstractmethod
	def get_submission(self, submission_id: str) -> LivestockSubmission | None:
		"""Read one intake submission. None when the payload has no submission."""

	@abstractmethod
	def list_for_farmer(self, farmer_id: str) -> list[dict]:
		"""Livestock submissions and register rows of one farmer, by Farmer-ID (``FR-…``)."""


def get_livestock_registry_client() -> LivestockRegistryClient:
	"""Return the configured client. Defaults to the in-memory mock."""
	import frappe

	backend = (frappe.conf.get("livestock_registry_backend") or "mock").lower()
	if backend == "mock":
		from .mock import MockLivestockRegistryClient

		return MockLivestockRegistryClient()
	if backend != "http":
		raise LivestockRegistryError(f"Unknown livestock_registry_backend: {backend!r}")

	from da_services.integrations.staff_api.auth import keycloak_from_conf

	from .http import HTTPLivestockRegistryClient

	return HTTPLivestockRegistryClient(
		staff_url=frappe.conf.get("livestock_registry_staff_url") or "",
		keycloak=keycloak_from_conf(
			frappe.conf.get("livestock_registry_keycloak"),
			frappe.conf.get("farmer_registry_keycloak"),
		),
		api_key=frappe.conf.get("livestock_registry_api_key"),
		register_mnemonic=frappe.conf.get("livestock_registry_register_mnemonic") or "Livestock",
		register_id=frappe.conf.get("livestock_registry_register_id") or None,
		form_name_contains=frappe.conf.get("livestock_registry_form_name_contains") or "Livestock Intake",
		sender_app=frappe.conf.get("livestock_registry_sender_app") or "OAN_REGISTRY_API_TEST",
		timeout=int(frappe.conf.get("livestock_registry_timeout") or 30),
		master_data_url=frappe.conf.get("livestock_registry_master_data_url") or None,
	)
