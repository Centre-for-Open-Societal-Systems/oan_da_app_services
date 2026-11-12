"""Crop Sown Registry client interface and factory.

Reads discover the CropSown register and one intake form, then read a
submission and its tabs. Writes follow the collection: save one intake
section at a time, then finalize that submission. Crop Sown has no ingest API.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import frappe

from .schemas import CropSubmission


def _parse_config_ids(raw: object) -> frozenset[str]:
	"""Comma- or newline-separated ids from DA Services Settings."""
	text = str(raw or "").strip()
	if not text:
		return frozenset()
	return frozenset(part.strip() for part in text.replace(",", "\n").splitlines() if part.strip())


def get_crop_list_sources() -> frozenset[str]:
	"""Allowed ``source`` values from DA Services Settings (Crop List Sources).

	Defaults live on the DocType field — not duplicated here.
	"""
	sources = _parse_config_ids(frappe.db.get_single_value("DA Services Settings", "crop_list_sources"))
	if not sources:
		raise CropRegistryClientError("Crop List Sources is not set in DA Services Settings")
	return sources


def get_crop_register_tab_ids() -> frozenset[str]:
	"""Allowed register ``tab_id`` values from DA Services Settings.

	Defaults live on the DocType field — not duplicated here.
	"""
	tabs = _parse_config_ids(frappe.db.get_single_value("DA Services Settings", "crop_register_tab_ids"))
	if not tabs:
		raise CropRegistryClientError("Crop Register Tab Ids is not set in DA Services Settings")
	return tabs


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
	def list_for_farmer(self, farmer_id: str, *, reg_id: str | None = None, source: str) -> list[dict]:
		"""Search Crop Sown for one farmer.

		``source`` is ``submission`` or ``register`` (one staff search only).
		``farmer_id`` is the internal id (register ``link_internal_record_id``).
		``reg_id`` is the Farmer-ID (submission ``record_name``).
		"""

	@abstractmethod
	def get_register_tab(self, internal_record_id: str, tab_id: str) -> dict:
		"""One register tab via ``/register-data/get_tab_records``.

		Returns ``{internal_record_id, register_id, tab_id, tabs}``.
		"""

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

	@abstractmethod
	def describe_intake_form(self, *, form_name: str | None = None) -> dict:
		"""One intake form: sections, register fields, labels, and loaded catalog rows.

		``form_name`` selects planning, cultivation, sowing, or harvesting.
		The HTTP intake-form route always passes it. Omitted here, the client
		uses the configured form. Response ``intake_forms`` lists only the
		selected form. Crop Sown identity and location are planning sections.
		"""

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
		form_name: str | None = None,
	) -> dict:
		"""Validate records against intake metadata, then save each section."""


def get_crop_registry_client() -> CropRegistryClient:
	"""Return the configured client. Defaults to the in-memory mock."""
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
		register_id=frappe.conf.get("crop_registry_register_id") or None,
		form_name_contains=frappe.conf.get("crop_registry_form_name_contains") or "Planning",
		sender_app=frappe.conf.get("crop_registry_sender_app") or "OAN_REGISTRY_API_TEST",
		registry_client_id=(frappe.conf.get("crop_registry_client_id") or "cropsown-registry-staff-portal"),
		timeout=int(frappe.conf.get("crop_registry_timeout") or 30),
		master_data_url=frappe.conf.get("crop_registry_master_data_url") or None,
	)
