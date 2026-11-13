"""A2C (Access to Credit) client interface.

A2C is a separate service behind the API Gateway. Integration is via HTTP API,
never direct database access.

Configuration (site_config.json):
{
    "a2c_backend": "mock",  // "mock" or "http"
    "a2c_api_base_url": "https://a2c.example.com/api"  // Required for "http" mode
}
"""

from abc import ABC, abstractmethod

import frappe
from frappe import _


class A2CClient(ABC):
	"""Abstract interface to A2C service."""

	@abstractmethod
	def has_active_application(self, farmer_id: str) -> bool:
		"""Check if farmer has any active credit application.

		Args:
		    farmer_id: Farmer Registry internal_record_id

		Returns:
		    True if farmer has application under review
		"""

	@abstractmethod
	def has_active_applications(self, farmer_ids: list[str]) -> set[str]:
		"""Batch check for active credit applications.

		Args:
		    farmer_ids: List of Farmer Registry internal_record_ids

		Returns:
		    Set of farmer_ids that have active applications
		"""


class MockA2CClient(A2CClient):
	"""Mock A2C client for testing without A2C service."""

	def __init__(self):
		"""Initialize mock client with configurable test data."""
		# Allow tests to inject farmers with active applications
		self._test_active_farmers = set()

	def set_active_farmers(self, farmer_ids: list[str]):
		"""Configure which farmers have active applications (for testing).

		Args:
		    farmer_ids: List of farmer IDs to mark as having active applications
		"""
		self._test_active_farmers = set(farmer_ids)

	def has_active_application(self, farmer_id: str) -> bool:
		"""Check if farmer has active application (mock).

		Args:
		    farmer_id: Farmer Registry internal_record_id

		Returns:
		    True if farmer is in test active set
		"""
		return farmer_id in self._test_active_farmers

	def has_active_applications(self, farmer_ids: list[str]) -> set[str]:
		"""Batch check for active applications (mock).

		Args:
		    farmer_ids: List of Farmer Registry internal_record_ids

		Returns:
		    Set of farmer_ids in test active set
		"""
		return self._test_active_farmers.intersection(farmer_ids)


class HttpA2CClient(A2CClient):
	"""HTTP client for A2C service integration."""

	def __init__(self):
		"""Initialize HTTP client with configuration."""
		self.base_url = frappe.conf.get("a2c_api_base_url")
		if not self.base_url:
			frappe.throw(_("A2C API base URL not configured (a2c_api_base_url in site_config.json)"))

		self._last_outage_log = None  # Track last time we logged an outage

	def has_active_application(self, farmer_id: str) -> bool:
		"""Check if farmer has active application via A2C API.

		Args:
		    farmer_id: Farmer Registry internal_record_id

		Returns:
		    True if farmer has application under review
		"""
		# Use batch method for single query
		result = self.has_active_applications([farmer_id])
		return farmer_id in result

	def has_active_applications(self, farmer_ids: list[str]) -> set[str]:
		"""Batch check for active applications via A2C API.

		Args:
		    farmer_ids: List of Farmer Registry internal_record_ids

		Returns:
		    Set of farmer_ids with active applications

		Raises:
		    NotImplementedError: A2C API contract is pending
		"""
		# TODO: Implement once A2C API contract is finalized
		# Expected endpoint: POST /api/v1/a2c/applications/check-active
		# Request: {"farmer_ids": ["farmer-1", "farmer-2"]}
		# Response: {"active_farmer_ids": ["farmer-1"]}

		raise NotImplementedError(
			"A2C HTTP API integration pending. "
			"API contract not yet finalized. "
			"Use a2c_backend='mock' for development."
		)


# Singleton instance cache
_client_instance = None


def get_a2c_client() -> A2CClient:
	"""Factory: returns configured A2C client.

	Configuration must be explicit via site_config.json:
	    "a2c_backend": "mock" (default) or "http"

	Returns:
	    A2CClient instance

	Raises:
	    ValueError: If backend is not configured or invalid
	"""
	global _client_instance

	if _client_instance is not None:
		return _client_instance

	backend = frappe.conf.get("a2c_backend", "mock")  # Default to mock

	if backend == "mock":
		_client_instance = MockA2CClient()
	elif backend == "http":
		_client_instance = HttpA2CClient()
	else:
		frappe.throw(
			_(f"Invalid a2c_backend: '{backend}'. Must be 'mock' or 'http'"),
			exc=ValueError,
		)

	return _client_instance


def reset_client():
	"""Reset cached client instance (for testing)."""
	global _client_instance
	_client_instance = None
