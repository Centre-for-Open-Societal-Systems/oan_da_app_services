"""Crop Sown Registry client interface and Settings helpers.

Staff host and CropSown ``register_id`` come from DA Services Settings
(``crop_registry_domain``, ``crop_registry_form_register_id``). Catalog host
is Settings ``master_data_domain`` when set. Auth is the caller's SSO bearer
and CSRF. There is no ``get_all_registers`` discovery. The HTTP factory is
``get_crop_registry_client`` in this package. Writes follow the collection:
save one intake section at a time, then finalize. Crop Sown has no ingest API.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import frappe

from da_services.services.utils import SETTINGS_DOCTYPE, base_url_from_domain, required_setting_value

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
	def submit_sections(
		self,
		requests: list[dict],
		*,
		submission_id: str | None = None,
		subject_record_id: str | None = None,
		form_name: str | None = None,
	) -> dict:
		"""Validate records against intake metadata, then save each section."""


def _staff_url_and_register_id() -> tuple[str, str]:
	"""Crop Registry Domain + Form Register ID from DA Services Settings."""
	settings = (
		frappe.db.get_value(
			SETTINGS_DOCTYPE,
			SETTINGS_DOCTYPE,
			["crop_registry_domain", "crop_registry_form_register_id"],
			as_dict=True,
		)
		or frappe._dict()
	)
	return (
		base_url_from_domain(settings.crop_registry_domain, "Crop Registry Domain"),
		required_setting_value(settings.crop_registry_form_register_id, "Crop Registry Form Register ID"),
	)


def _master_data_url() -> str | None:
	"""Optional Master Data Domain from DA Services Settings."""
	domain = (frappe.db.get_single_value(SETTINGS_DOCTYPE, "master_data_domain") or "").strip()
	if not domain:
		return None
	return base_url_from_domain(domain, "Master Data Domain")
