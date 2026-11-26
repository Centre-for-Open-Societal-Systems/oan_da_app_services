"""A2C (Access to Credit) client interface.

A2C is a separate service behind the API Gateway. Integration is via HTTP API,
never direct database access.

Configuration (site_config.json):
{
    "a2c_backend": "mock",  // "mock" or "http"
    "a2c_api_base_url": "https://a2c-service.development.oanstaging.com",
    "a2c_timeout_seconds": 30  // optional, positive number of seconds
}

Read-only operations for the DA Credit page (verified paths only):
    GET /v1/loan-applications/summary
    GET /v1/loan-applications?status={status}&page={page}
    GET /v1/loan-applications/{application_id}/documents

The upstream JSON body is returned unchanged. A2C has not supplied a response
schema, so this client never invents one.

Authentication: the caller's current ``Authorization`` header is forwarded on
the request that needs it. No login is performed, no token is cached, and no
token is stored on the singleton client, so one request cannot leak a token
into another. Cookies, CSRF values and unrelated headers are not forwarded.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

import frappe
import requests

# Application IDs are one URL path segment. Keep this conservative: letters,
# digits, dot, underscore and hyphen, with an alphanumeric first and last
# character. Nothing here can introduce another path segment or scheme.
_APPLICATION_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
_STATUS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$")


class A2CError(Exception):
	"""Base exception for A2C integration errors."""

	http_status_code = 502


class A2CConfigurationError(A2CError):
	"""A2C mode or endpoint configuration is missing or invalid."""

	http_status_code = 503


class A2CValidationError(A2CError):
	"""Caller input cannot be used to build a safe A2C request."""

	http_status_code = 400


class A2CNotFound(A2CError):
	"""A2C answered HTTP 404 for the requested resource."""

	http_status_code = 404


class A2CRejectedError(A2CError):
	"""A2C rejected the request (HTTP 401/403 or another 4xx)."""

	http_status_code = 502


class A2CUnavailable(A2CError):
	"""A2C is unreachable, timed out, or answered with HTTP 5xx."""

	http_status_code = 503


class A2CResponseError(A2CError):
	"""A2C answered with a body this client cannot use as JSON data."""

	http_status_code = 502


def validate_application_id(application_id: object) -> str:
	"""Return a safe single-segment A2C application ID or raise.

	A2C application IDs have not been documented in this repository. The
	accepted shape is therefore deliberately conservative and rejects any
	value that could alter the upstream path.
	"""
	value = str(application_id or "").strip()
	if not value or ".." in value or not _APPLICATION_ID_RE.fullmatch(value):
		raise A2CValidationError("A valid A2C application ID is required.")
	return value


def validate_page(page: object) -> int:
	"""Return a positive integer page number (default 1) or raise."""
	if page is None:
		return 1
	if isinstance(page, bool):
		raise A2CValidationError("page must be a positive integer.")
	text = str(page).strip()
	if not text or not text.isascii() or not text.isdigit() or int(text) < 1:
		raise A2CValidationError("page must be a positive integer.")
	return int(text)


def validate_status(status: object) -> str | None:
	"""Return a basic, safely encoded status filter, or raise.

	The documented status values have not been supplied, so this does not
	invent an enum. It only refuses control characters and separators that do
	not belong in a single query value.
	"""
	if status in (None, ""):
		return None
	value = str(status).strip()
	if not value:
		return None
	if not _STATUS_RE.fullmatch(value):
		raise A2CValidationError("status is not a valid A2C application status.")
	return value


def _positive_timeout(value: object, default: float = 30) -> float:
	try:
		timeout = float(value)  # type: ignore[arg-type]
	except (TypeError, ValueError):
		return default
	return timeout if timeout > 0 else default


def _log_failure(action: str, path: str, *, code: int | None = None, error: Exception | None = None) -> None:
	"""Log technical context without tokens, bodies, or farmer information."""
	detail = f"HTTP {code}" if code is not None else (error.__class__.__name__ if error else "unknown")
	frappe.log_error(title="A2C request failed", message=f"{action}: {detail} ({path})")


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

	@abstractmethod
	def get_loan_application_summary(self) -> dict | list:
		"""Read GET /v1/loan-applications/summary.

		Returns:
		    The upstream JSON body unchanged.
		"""

	@abstractmethod
	def list_loan_applications(self, *, status: str | None = None, page: int = 1) -> dict | list:
		"""Read GET /v1/loan-applications?status=&page=.

		Args:
		    status: Optional A2C application status filter.
		    page: Positive page number; defaults to 1.

		Returns:
		    The upstream JSON body unchanged.
		"""

	@abstractmethod
	def get_loan_application_documents(self, application_id: str) -> dict | list:
		"""Read GET /v1/loan-applications/{application_id}/documents.

		Args:
		    application_id: A safe single-segment A2C application ID.

		Returns:
		    The upstream JSON body unchanged.
		"""


class MockA2CClient(A2CClient):
	"""Mock A2C client for testing without A2C service.

	The mock backend covers the existing DA-345 active-application contract.
	The Credit page reads are intentionally not fabricated: they require the
	HTTP backend so a development or test environment cannot mistake fake
	national loan data for real data.
	"""

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

	def get_loan_application_summary(self) -> dict | list:
		raise A2CConfigurationError("The A2C read APIs need the HTTP backend (a2c_backend='http').")

	def list_loan_applications(self, *, status: str | None = None, page: int = 1) -> dict | list:
		raise A2CConfigurationError("The A2C read APIs need the HTTP backend (a2c_backend='http').")

	def get_loan_application_documents(self, application_id: str) -> dict | list:
		raise A2CConfigurationError("The A2C read APIs need the HTTP backend (a2c_backend='http').")


class HttpA2CClient(A2CClient):
	"""HTTP client for A2C service integration."""

	def __init__(self):
		"""Initialize HTTP client with configuration."""
		self.base_url = str(frappe.conf.get("a2c_api_base_url") or "").strip().rstrip("/")
		if not self.base_url.startswith(("http://", "https://")):
			raise A2CConfigurationError("a2c_api_base_url must be an http(s) URL in site_config.json")
		self.timeout = _positive_timeout(frappe.conf.get("a2c_timeout_seconds"))

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

	# ------------------------------------------------------------------ reads

	@staticmethod
	def _caller_authorization() -> str | None:
		"""The current request's Authorization header, or None outside a request.

		The value is read per call and never stored on the client instance.
		"""
		try:
			return frappe.get_request_header("Authorization")
		except (AttributeError, RuntimeError):
			# AttributeError: no request is bound (background job, bench console).
			return None

	def _headers(self) -> dict[str, str]:
		headers = {"Accept": "application/json"}
		authorization = self._caller_authorization()
		if authorization:
			headers["Authorization"] = authorization
		return headers

	def _get(self, path: str, *, action: str, params: dict | None = None) -> dict | list:
		url = f"{self.base_url}{path}"
		try:
			response = requests.get(
				url,
				headers=self._headers(),
				params=params,
				timeout=self.timeout,
			)
		except requests.Timeout as exc:
			_log_failure(action, path, error=exc)
			raise A2CUnavailable("A2C did not answer in time.") from exc
		except requests.RequestException as exc:
			_log_failure(action, path, error=exc)
			raise A2CUnavailable("A2C is unreachable.") from exc

		code = response.status_code
		if code >= 500:
			_log_failure(action, path, code=code)
			raise A2CUnavailable("A2C is unavailable.")
		if code == 404:
			_log_failure(action, path, code=code)
			raise A2CNotFound("A2C resource not found.")
		if code >= 400:
			# 401/403 are A2C's decision about the forwarded token or role. They
			# are not proof that the caller's DA Services session is invalid, so
			# they surface as an upstream rejection rather than a DA sign-out.
			_log_failure(action, path, code=code)
			raise A2CRejectedError("A2C rejected the request.")

		try:
			body = response.json()
		except ValueError as exc:
			_log_failure(action, path, code=code, error=exc)
			raise A2CResponseError("A2C returned a non-JSON response.") from exc
		if not isinstance(body, (dict, list)):
			_log_failure(action, path, code=code)
			raise A2CResponseError("A2C returned an unexpected response shape.")
		return body

	def get_loan_application_summary(self) -> dict | list:
		return self._get("/v1/loan-applications/summary", action="read loan application summary")

	def list_loan_applications(self, *, status: str | None = None, page: int = 1) -> dict | list:
		page = validate_page(page)
		status = validate_status(status)
		params: dict = {"page": page}
		if status is not None:
			params["status"] = status
		return self._get("/v1/loan-applications", action="list loan applications", params=params)

	def get_loan_application_documents(self, application_id: str) -> dict | list:
		application_id = validate_application_id(application_id)
		return self._get(
			f"/v1/loan-applications/{application_id}/documents",
			action="read loan application documents",
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
	    A2CConfigurationError: If backend is not configured or invalid
	"""
	global _client_instance

	if _client_instance is not None:
		return _client_instance

	backend = str(frappe.conf.get("a2c_backend") or "mock").lower()  # Default to mock

	if backend == "mock":
		_client_instance = MockA2CClient()
	elif backend == "http":
		_client_instance = HttpA2CClient()
	else:
		raise A2CConfigurationError(f"Invalid a2c_backend: '{backend}'. Must be 'mock' or 'http'")

	return _client_instance


def reset_client():
	"""Reset cached client instance (for testing)."""
	global _client_instance
	_client_instance = None
