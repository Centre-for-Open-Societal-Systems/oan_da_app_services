"""Crop Sown Registry client interface and factory.

Reads discover the CropSown register and its Planning intake form, then read
a submission and its tabs. Writes follow the collection: save one intake
section at a time, then finalize that submission. Crop Sown has no ingest API.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .schemas import CropSubmission


class CropRegistryError(Exception):
	"""Base exception for Crop Sown Registry integration errors."""


class CropRegistryClientError(CropRegistryError):
	"""The registry rejected the request (HTTP 4xx or envelope ERROR)."""

	def __init__(self, message: str, status_code: int | None = None):
		super().__init__(message)
		self.status_code = status_code


class CropRegistryServerError(CropRegistryError):
	"""The registry failed or was unreachable (HTTP 5xx, timeout, connection)."""

	def __init__(self, message: str, status_code: int | None = None):
		super().__init__(message)
		self.status_code = status_code


class CropRegistryClient(ABC):
	"""Read operations against the Crop Sown registry."""

	@abstractmethod
	def get_submission(self, submission_id: str) -> CropSubmission | None:
		"""Read one intake submission. None when the payload has no submission."""

	@abstractmethod
	def get_tabs(self, submission_id: str) -> list[dict]:
		"""Read every tab of a submission. Each item is {tab_id, records}."""

	@abstractmethod
	def list_for_farmer(self, farmer_id: str) -> list[dict]:
		"""Search the Crop Sown register and return rows linked to this farmer."""

	@abstractmethod
	def save_section(
		self,
		*,
		section_id: str,
		section_register_id: str,
		section_payload: list[dict],
		submission_id: str | None = None,
		form_id: str | None = None,
		register_id: str | None = None,
	) -> dict:
		"""Save one intake section. The first save omits submission_id."""

	@abstractmethod
	def finalize_submission(self, submission_id: str) -> dict:
		"""Mark a draft submission FINAL and pending approval."""


def get_crop_registry_client() -> CropRegistryClient:
	"""Return the configured client. Defaults to the in-memory mock."""
	import frappe

	backend = (frappe.conf.get("crop_registry_backend") or "mock").lower()
	if backend == "mock":
		from .mock import MockCropRegistryClient

		return MockCropRegistryClient()
	if backend != "http":
		raise CropRegistryError(f"Unknown crop_registry_backend: {backend!r}")

	staff_url = frappe.conf.get("crop_registry_staff_url") or ""
	from da_services.integrations.staff_api.auth import keycloak_from_conf

	from .http import HTTPCropRegistryClient

	return HTTPCropRegistryClient(
		staff_url=staff_url,
		keycloak=keycloak_from_conf(
			frappe.conf.get("crop_registry_keycloak"),
			frappe.conf.get("farmer_registry_keycloak"),
		),
		api_key=frappe.conf.get("crop_registry_api_key"),
		register_mnemonic=frappe.conf.get("crop_registry_register_mnemonic") or "CropSown",
		form_name_contains=frappe.conf.get("crop_registry_form_name_contains") or "Planning",
		sender_app=frappe.conf.get("crop_registry_sender_app") or "OAN_REGISTRY_API_TEST",
		registry_client_id=(
			frappe.conf.get("crop_registry_client_id") or "cropsown-registry-staff-portal"
		),
		timeout=int(frappe.conf.get("crop_registry_timeout") or 30),
	)
