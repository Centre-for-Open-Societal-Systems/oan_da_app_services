"""Farmer Registry client interface and factory.

Provides a boundary to the Farmer Registry (OpenG2P-based). The client interface
defines the operations; implementations (mock, HTTP) are swapped via factory.
"""

from abc import ABC, abstractmethod

import frappe

from .schemas import Farmer, FarmerListItem, Plot


class FarmerRegistryError(Exception):
	"""Base exception for Farmer Registry integration errors."""

	pass


class FarmerRegistryClientError(FarmerRegistryError):
	"""The registry rejected the request (HTTP 4xx). Retrying the same call will not help."""

	def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
		super().__init__(message)
		self.status_code = status_code
		self.body = body


class FarmerRegistryServerError(FarmerRegistryError):
	"""The registry failed or was unreachable (HTTP 5xx, timeout, connection). Safe to retry."""

	def __init__(self, message: str, status_code: int | None = None):
		super().__init__(message)
		self.status_code = status_code


class FarmerRegistryClient(ABC):
	"""Abstract interface to the Farmer Registry."""

	@abstractmethod
	def get_farmer(self, farmer_id: str) -> Farmer | None:
		"""Get a single farmer by ID.

		Args:
		    farmer_id: Farmer Registry internal_record_id

		Returns:
		    Farmer object or None if not found
		"""
		pass

	@abstractmethod
	def list_farmers(
		self,
		*,
		woreda: str | None = None,
		kebele: str | None = None,
		state: str | None = None,
		limit: int = 50,
		offset: int = 0,
	) -> tuple[list[FarmerListItem], int]:
		"""List farmers with filters.

		Args:
		    woreda: Filter by woreda code
		    kebele: Filter by kebele code
		    state: Filter by farmer state (APPROVED, etc.)
		    limit: Page size
		    offset: Page offset

		Returns:
		    (list of farmers, total count)
		"""
		pass

	@abstractmethod
	def search_farmers(
		self,
		*,
		search_text: str = "",
		filter_by: dict | None = None,
		page: int = 1,
		page_size: int = 50,
	) -> dict:
		"""Search the Farmer register live (DA-272).

		Args:
		    search_text: Free text the registry matches on name / ids
		    filter_by: {woreda, kebele, state} as DA codes; translated to the
		        registry's *_name filters by the implementation
		    page: 1-based page number (registry current_page)
		    page_size: Rows per page

		Returns:
		    {"items": [FarmerListItem], "page", "page_size", "total", "number_of_pages"}
		"""
		pass

	@abstractmethod
	def get_farmer_plots(self, farmer_id: str) -> list[Plot]:
		"""Get land plots for a farmer.

		Args:
		    farmer_id: Farmer Registry internal_record_id

		Returns:
		    List of Plot objects
		"""
		pass

	@abstractmethod
	def get_change_requests(
		self, farmer_id: str, *, tab: str | None = None, status: str | None = None
	) -> list[dict]:
		"""Change requests raised against a farmer (DA-272).

		Calls ``/change-requests/get_change_requests`` per register tab and
		returns the raw rows, newest first.

		Args:
		    farmer_id: Farmer Registry internal_record_id
		    tab: One tab key (farmer, location, household, land, crop, livestock);
		        all tabs when omitted
		    status: Keep only this approval_status (PENDING, APPROVED, REJECTED)
		"""
		pass

	@abstractmethod
	def update_farmer(self, farmer_id: str, updates: dict, expected_version: int | None = None) -> dict:
		"""Update farmer data (for DA-41).

		Args:
		    farmer_id: Farmer Registry internal_record_id
		    updates: Dict of field:value pairs to update
		    expected_version: Expected current version (for optimistic concurrency)

		Returns:
		    Update result dict:
		        {
		            "success": bool,
		            "farmer_id": str,
		            "version": int,  # New version after update
		            "conflict": bool,  # True if version mismatch
		            "current_version": int,  # Server version (if conflict)
		            "current_data": dict,  # Server data (if conflict)
		        }

		Raises:
		    FarmerRegistryError: Registry unavailable or error
		"""
		pass

	@abstractmethod
	def register_farmer(self, payload: dict) -> dict:
		"""Register a new farmer through the registry ingest pipeline.

		Submits a farmer record to the Farmer Registry Staff API
		``/input-mechanism-data/ingest-data`` endpoint. Ingestion is async:
		the registry classifies and transforms the payload, creates an intake
		form submission, and routes it for Woreda approval. The call returns a
		correlation_id to track that pipeline, not a finished farmer.

		Args:
		    payload: Ingest envelope (header + message.payload) as produced by
		        mapper.da_form_to_ingest_payload().

		Returns:
		    {
		        "success": bool,
		        "correlation_id": str | None,  # tracks the async ingestion
		        "status": str,                 # SUCCESS / ERROR
		        "error_code": str | None,
		        "error_message": str | None,
		    }

		Raises:
		    FarmerRegistryError: Registry unavailable or error
		"""
		pass

	@abstractmethod
	def get_ingest_status(self, correlation_id: str) -> list[dict]:
		"""Look up ingestion pipeline rows for a correlation_id.

		Calls ``/ingestion-data/search_in_ingestion_data`` to report where a
		submitted farmer is in the pipeline (classification → transformation →
		ingestion) and the resulting intake_form_submission_id once created.

		Args:
		    correlation_id: Value returned by register_farmer().

		Returns:
		    List of ingestion row dicts (may be empty while still queued).

		Raises:
		    FarmerRegistryError: Registry unavailable or error
		"""
		pass

	# -- Intake form registration ------------------------------------------
	# Section-by-section registration, the path the staff portal uses:
	# upload the identity document, save each form section into one draft
	# submission, then finalize it for approval. See intake.py.

	@abstractmethod
	def render_intake_form(self) -> dict:
		"""Return the configured intake form: {form_id, register_id, tabs: [{sections: [...]}]}."""
		pass

	@abstractmethod
	def get_registers(self) -> list[dict]:
		"""Return every register as [{register_id, register_mnemonic, ...}]."""
		pass

	@abstractmethod
	def get_intake_forms(self) -> list[dict]:
		"""Return the intake forms of the Farmer register: [{form_id, form_mnemonic, register_id, ...}]."""
		pass

	@abstractmethod
	def get_attribute_values(self, attribute_id: str) -> list[dict]:
		"""Return a Master Data code list: [{value_id, value_code, value_display, ...}]."""
		pass

	@abstractmethod
	def get_geo_levels(self) -> list[dict]:
		"""Return Master Data geo levels: [{level_id, level_mnemonic}] (region, zone, woreda, kebele)."""
		pass

	@abstractmethod
	def get_geo_level_values(self, level_id: str, parent_level_value_id: str | None = None) -> list[dict]:
		"""Return the places on one geo level, optionally under a parent place."""
		pass

	@abstractmethod
	def upload_document(self, filename: str, content: bytes, content_type: str) -> str:
		"""Upload a file to the registry document store and return its document_id."""
		pass

	@abstractmethod
	def save_intake_section(self, section: dict) -> dict:
		"""Save one section into a draft intake submission.

		Args:
		    section: {submission_id (None for the first section), form_id,
		        register_id, section_id, section_register_id, section_payload,
		        documents (optional)}.

		Returns:
		    {"success", "submission_id", "status", "error_code", "error_message"}
		"""
		pass

	@abstractmethod
	def finalize_intake_submission(self, submission_id: str) -> dict:
		"""Finalize a draft submission so it goes for approval.

		Returns:
		    {"success", "submission_id", "draft_status", "approval_status",
		     "status", "error_code", "error_message"}
		"""
		pass

	@abstractmethod
	def get_intake_submission(self, submission_id: str) -> dict | None:
		"""Read a submission back (draft_status, approval_status, section_payloads)."""
		pass


def get_farmer_registry_client() -> FarmerRegistryClient:
	"""Factory: returns the configured Farmer Registry client.

	Selection order:
	1. Site config `farmer_registry_backend` ("mock" or "http")
	2. Default: "mock"

	The mock backend is safe for development and testing without a live
	Farmer Registry. Switch to "http" once the registry is deployed.
	"""
	backend = frappe.conf.get("farmer_registry_backend", "mock")

	if backend == "http":
		from .http import HTTPFarmerRegistryClient

		return HTTPFarmerRegistryClient()
	else:
		from .mock import MockFarmerRegistryClient

		return MockFarmerRegistryClient()
