"""HTTP client for live Farmer Registry (OpenG2P-based).

Connects to the Farmer Registry Staff API at the configured base URL.
Supports configurable API routes and versioning via site_config.json.

Configuration Example:
    {
        "farmer_registry_backend": "http",
        "farmer_registry_url": "http://farmer-registry:8080",
        "farmer_registry_api_key": "your-api-key",
        "farmer_registry_api_version": "v1",
        "farmer_registry_endpoints": {
            "get_farmer": "/api/{version}/registry/farmer/{farmer_id}",
            "list_farmers": "/api/{version}/registry/farmer",
            "get_plots": "/api/{version}/registry/land",
            "update_farmer": "/api/{version}/registry/farmer/{farmer_id}"
        }
    }
"""

import secrets
import threading
import time
import uuid
from typing import ClassVar

import frappe
import requests
from frappe import _

from .client import (
	FarmerRegistryClient,
	FarmerRegistryClientError,
	FarmerRegistryError,
	FarmerRegistryServerError,
)
from .schemas import Farmer, FarmerListItem, Plot


class HTTPFarmerRegistryClient(FarmerRegistryClient):
	"""HTTP client for live Farmer Registry with configurable routes."""

	# Default endpoint templates (can be overridden in config)
	DEFAULT_ENDPOINTS: ClassVar[dict] = {
		"get_farmer": "/api/{version}/registry/farmer/{farmer_id}",
		"list_farmers": "/api/{version}/registry/farmer",
		"get_plots": "/api/{version}/registry/land",
		"update_farmer": "/api/{version}/registry/farmer/{farmer_id}",
	}

	def __init__(self):
		"""Initialize HTTP client with configurable endpoints."""
		# Base configuration
		self.base_url = frappe.conf.get("farmer_registry_url", "http://farmer-registry:8080")
		self.api_key = frappe.conf.get("farmer_registry_api_key")
		self.timeout = frappe.conf.get("farmer_registry_timeout", 30)  # seconds

		# API version (configurable for v1, v2, etc.)
		self.api_version = frappe.conf.get("farmer_registry_api_version", "v1")

		# Configurable endpoint paths with defaults
		self.endpoints = frappe.conf.get("farmer_registry_endpoints", self.DEFAULT_ENDPOINTS)

		# HTTP headers
		self.headers = {
			"Content-Type": "application/json",
			"Accept": "application/json",
		}

		# Add authentication if a static API key / token is configured.
		# When Keycloak client-credentials are configured instead (see
		# _get_access_token), the per-request token takes precedence.
		if self.api_key:
			self.headers["Authorization"] = f"Bearer {self.api_key}"

		# Ingest pipeline configuration (farmer registration via ingest-data).
		# These identify the target data model / register / intake form in the
		# OpenG2P Farmer Registry; defaults match the dev FARMER_ODK_MODEL.
		ingest = frappe.conf.get("farmer_registry_ingest", {}) or {}
		self.ingest_data_model = ingest.get("data_model", "FARMER_ODK_MODEL")
		self.ingest_register_id = ingest.get("register_id")
		self.ingest_intake_form_id = ingest.get("intake_form_id")

		# Keycloak client-credentials for a DA Services service account.
		# When present, a token is fetched and cached per worker; the lock keeps
		# concurrent threads from refreshing the same token at once.
		self.keycloak = frappe.conf.get("farmer_registry_keycloak", {}) or {}
		self._token_cache: dict = {}
		self._token_lock = threading.Lock()

		# Fail fast: a missing or partial config should surface when the client
		# is built, not on the first request from the field.
		self._validate_config()

	def _validate_config(self) -> None:
		"""Raise FarmerRegistryError if the live client cannot possibly work."""
		problems = []
		if not str(self.base_url or "").startswith(("http://", "https://")):
			problems.append("farmer_registry_url must be an http(s) URL")

		kc_keys = ("token_url", "client_id", "client_secret")
		kc_set = [k for k in kc_keys if self.keycloak.get(k)]
		if kc_set and len(kc_set) != len(kc_keys):
			problems.append(
				"farmer_registry_keycloak needs token_url, client_id and client_secret "
				f"(found only: {', '.join(kc_set)})"
			)
		if not kc_set and not self.api_key:
			problems.append("set farmer_registry_keycloak (service account) or farmer_registry_api_key")

		if not self.ingest_data_model:
			problems.append("farmer_registry_ingest.data_model is required")

		if problems:
			raise FarmerRegistryError("Farmer Registry HTTP client misconfigured: " + "; ".join(problems))

	def _get_access_token(self) -> str | None:
		"""Return a bearer token for the Staff API.

		Preference order:
		1. Keycloak client-credentials (service account), cached until near expiry.
		2. Static ``farmer_registry_api_key`` (set on self.headers in __init__).

		Returns None when neither is configured (caller then relies on whatever
		is already on self.headers). Refresh is serialised with a lock so two
		threads cannot both hit Keycloak and race on the cache.
		"""
		kc = self.keycloak
		token_url = kc.get("token_url")
		client_id = kc.get("client_id")
		client_secret = kc.get("client_secret")
		if not (token_url and client_id and client_secret):
			return None

		def _fresh(cache: dict) -> bool:
			return bool(cache.get("token")) and cache.get("expires_at", 0) > time.time() + 30

		# Fast path without the lock; re-check inside it (double-checked locking).
		if _fresh(self._token_cache):
			return self._token_cache["token"]

		with self._token_lock:
			if _fresh(self._token_cache):
				return self._token_cache["token"]

			try:
				resp = requests.post(
					token_url,
					data={
						"grant_type": "client_credentials",
						"client_id": client_id,
						"client_secret": client_secret,
					},
					timeout=self.timeout,
				)
			except requests.RequestException as e:
				frappe.log_error(
					title="Farmer Registry Auth Error",
					message=f"Keycloak unreachable at {token_url}: {e!s}",
				)
				raise FarmerRegistryServerError("Could not reach Keycloak for a registry token") from e

			if resp.status_code >= 400:
				frappe.log_error(
					title="Farmer Registry Auth Error",
					message=f"Keycloak token request failed: HTTP {resp.status_code} {resp.text[:300]}",
				)
				exc = FarmerRegistryClientError if resp.status_code < 500 else FarmerRegistryServerError
				raise exc(
					f"Keycloak refused the token request (HTTP {resp.status_code})",
					status_code=resp.status_code,
				)

			data = resp.json()
			token = data.get("access_token")
			if not token:
				raise FarmerRegistryServerError("Keycloak response had no access_token")

			self._token_cache = {
				"token": token,
				"expires_at": time.time() + int(data.get("expires_in", 300)),
			}
			return token

	@staticmethod
	def _raise_for_status(response: requests.Response, action: str) -> None:
		"""Map HTTP errors to retryable vs non-retryable registry exceptions.

		4xx: the request is wrong (auth, roles, validation) — retrying the same
		call cannot succeed. 5xx: the registry is unhealthy — safe to retry.
		"""
		code = response.status_code
		if code < 400:
			return

		detail = ""
		try:
			detail = response.json().get("response_header", {}).get("response_error_message") or ""
		except ValueError:
			pass
		snippet = detail or (response.text or "")[:300]

		if code < 500:
			frappe.log_error(
				title="Farmer Registry rejected request",
				message=f"{action}: HTTP {code} {response.url}\n{snippet}",
			)
			raise FarmerRegistryClientError(
				f"Farmer Registry rejected {action} (HTTP {code}): {snippet}",
				status_code=code,
				body=snippet,
			)

		frappe.log_error(
			title="Farmer Registry server error",
			message=f"{action}: HTTP {code} {response.url}\n{snippet}",
		)
		raise FarmerRegistryServerError(
			f"Farmer Registry failed during {action} (HTTP {code})", status_code=code
		)

	def _auth_and_csrf_headers(self) -> tuple[dict, dict]:
		"""Build headers and cookies for a state-changing (POST) staff call.

		Adds the bearer token and a double-submit CSRF pair (matching header +
		cookie), which the registry's CsrfMiddleware requires on POST.
		"""
		headers = dict(self.headers)
		token = self._get_access_token()
		if token:
			headers["Authorization"] = f"Bearer {token}"

		csrf = secrets.token_urlsafe(24)
		headers["X-CSRF-Token"] = csrf
		cookies = {"X-CSRF-Token": csrf}
		return headers, cookies

	@staticmethod
	def _staff_envelope(request_payload: dict) -> dict:
		"""Wrap a payload in the OpenG2P staff request envelope."""
		return {
			"request_header": {
				"sender_app_mnemonic": "DA_SERVICES",
				"sender_app_url": "",
				"request_id": str(uuid.uuid4()),
				"request_timestamp": frappe.utils.now(),
				"instance_id": "",
			},
			"request_body": {
				"request_payload": request_payload,
				"pagination_request": {
					"current_page": 1,
					"page_size": 50,
					"sort_by": "",
					"filter_by": "",
					"search_text": "",
				},
			},
		}

	def _build_url(self, endpoint_key: str, **params) -> str:
		"""Build URL from configurable endpoint template.

		Args:
		    endpoint_key: Key in self.endpoints dict (e.g., "get_farmer")
		    **params: URL parameters to substitute (e.g., farmer_id="123")

		Returns:
		    Complete URL with base_url + endpoint path

		Example:
		    _build_url("get_farmer", farmer_id="farmer-uuid-001")
		    → "http://farmer-registry:8080/api/v1/registry/farmer/farmer-uuid-001"
		"""
		template = self.endpoints.get(endpoint_key, self.DEFAULT_ENDPOINTS.get(endpoint_key))

		if not template:
			frappe.throw(_("Endpoint '{0}' not configured").format(endpoint_key))

		# Replace {version} placeholder
		path = template.replace("{version}", self.api_version)

		# Replace other placeholders like {farmer_id}
		try:
			path = path.format(**params)
		except KeyError as e:
			frappe.throw(_("Missing URL parameter: {0}").format(str(e)))

		return f"{self.base_url}{path}"

	def get_farmer(self, farmer_id: str) -> Farmer | None:
		"""Get farmer details via HTTP GET.

		Args:
		    farmer_id: Farmer Registry internal_record_id

		Returns:
		    Farmer object or None if not found

		Raises:
		    requests.RequestException: If HTTP request fails
		"""
		url = self._build_url("get_farmer", farmer_id=farmer_id)

		try:
			response = requests.get(url, headers=self.headers, timeout=self.timeout)

			# Handle 404 - farmer not found
			if response.status_code == 404:
				return None

			# Raise for other errors (4xx, 5xx)
			response.raise_for_status()

			# Parse JSON response and convert to Farmer schema
			data = response.json()
			return Farmer(**data)

		except requests.RequestException as e:
			frappe.log_error(
				title="Farmer Registry HTTP Error",
				message=f"Failed to get farmer {farmer_id} from {url}: {e!s}",
			)
			raise

	def list_farmers(
		self,
		*,
		woreda: str | None = None,
		kebele: str | None = None,
		state: str | None = None,
		limit: int = 50,
		offset: int = 0,
	) -> tuple[list[FarmerListItem], int]:
		"""List farmers via HTTP GET with query parameters.

		Args:
		    woreda: Filter by woreda code
		    kebele: Filter by kebele name
		    state: Filter by registration state
		    limit: Maximum number of results
		    offset: Pagination offset

		Returns:
		    Tuple of (list of farmers, total count)

		Raises:
		    requests.RequestException: If HTTP request fails
		"""
		url = self._build_url("list_farmers")

		# Build query parameters
		params = {
			"limit": limit,
			"offset": offset,
		}

		if woreda:
			params["woreda"] = woreda
		if kebele:
			params["kebele"] = kebele
		if state:
			params["state"] = state

		try:
			response = requests.get(url, headers=self.headers, params=params, timeout=self.timeout)
			response.raise_for_status()

			data = response.json()

			# Parse response - expecting {"results": [...], "total": N}
			farmers = [FarmerListItem(**f) for f in data.get("results", [])]
			total = data.get("total", 0)

			return farmers, total

		except requests.RequestException as e:
			frappe.log_error(
				title="Farmer Registry HTTP Error", message=f"Failed to list farmers from {url}: {e!s}"
			)
			raise

	def get_farmer_plots(self, farmer_id: str) -> list[Plot]:
		"""Get farmer's land plots via HTTP GET.

		Args:
		    farmer_id: Farmer Registry internal_record_id

		Returns:
		    List of Plot objects

		Raises:
		    requests.RequestException: If HTTP request fails
		"""
		url = self._build_url("get_plots")

		params = {
			"link_internal_record_id": farmer_id,
			"limit": 100,  # Get all plots for this farmer
		}

		try:
			response = requests.get(url, headers=self.headers, params=params, timeout=self.timeout)
			response.raise_for_status()

			data = response.json()

			# Parse plots from response
			plots = [Plot(**p) for p in data.get("results", [])]

			return plots

		except requests.RequestException as e:
			frappe.log_error(
				title="Farmer Registry HTTP Error",
				message=f"Failed to get plots for {farmer_id} from {url}: {e!s}",
			)
			raise

	def update_farmer(self, farmer_id: str, updates: dict, expected_version: int | None = None) -> dict:
		"""Update farmer via HTTP PATCH with version checking (DA-41).

		Args:
		    farmer_id: Farmer Registry internal_record_id
		    updates: Dictionary of fields to update
		    expected_version: Expected current version for optimistic concurrency

		Returns:
		    Update result dict with version info or conflict flag

		Raises:
		    requests.RequestException: If HTTP request fails
		"""
		url = self._build_url("update_farmer", farmer_id=farmer_id)

		# Add version header if provided
		headers = dict(self.headers)
		if expected_version is not None:
			headers["If-Match"] = f'"{expected_version}"'

		try:
			response = requests.patch(url, headers=headers, json=updates, timeout=self.timeout)

			# Handle version conflict (409 Conflict)
			if response.status_code == 409:
				conflict_data = response.json()
				return {
					"success": False,
					"conflict": True,
					"farmer_id": farmer_id,
					"expected_version": expected_version,
					"current_version": conflict_data.get("current_version"),
					"current_data": conflict_data.get("current_data", {}),
					"message": conflict_data.get("message", "Version conflict"),
				}

			# Raise for other errors
			response.raise_for_status()

			# Success
			result = response.json()
			return {
				"success": True,
				"conflict": False,
				"farmer_id": farmer_id,
				"version": result.get("version"),
				"message": result.get("message", "Update successful"),
			}

		except requests.RequestException as e:
			frappe.log_error(
				title="Farmer Registry HTTP Error",
				message=f"Failed to update farmer {farmer_id} at {url}: {e!s}",
			)
			raise

	def register_farmer(self, payload: dict) -> dict:
		"""Register a new farmer via the Staff API ingest-data endpoint.

		POST /input-mechanism-data/ingest-data?data_model&register_id&intake_form_id
		Body is the ingest envelope (header + message.payload) built by
		mapper.da_form_to_ingest_payload(). Async: returns a correlation_id.
		"""
		if not isinstance(payload, dict) or not payload.get("message"):
			raise FarmerRegistryError("Invalid ingest payload: missing 'message'")

		url = f"{self.base_url}/input-mechanism-data/ingest-data"
		params = {"data_model": self.ingest_data_model}
		if self.ingest_register_id:
			params["register_id"] = self.ingest_register_id
		if self.ingest_intake_form_id:
			params["intake_form_id"] = self.ingest_intake_form_id

		headers, cookies = self._auth_and_csrf_headers()

		try:
			response = requests.post(
				url,
				headers=headers,
				cookies=cookies,
				params=params,
				json=payload,
				timeout=self.timeout,
			)
		except requests.RequestException as e:
			# Network-level failure (timeout, DNS, connection): transient, retryable.
			frappe.log_error(
				title="Farmer Registry HTTP Error",
				message=f"Could not reach {url} to register farmer: {e!s}",
			)
			raise FarmerRegistryServerError(f"Farmer Registry unreachable: {e!s}") from e

		self._raise_for_status(response, "register farmer")
		data = response.json()

		header = data.get("response_header", {}) or {}
		body = data.get("response_body", {}) or {}
		status = header.get("response_status")
		correlation_id = (body.get("response_payload") or {}).get("correlation_id")
		return {
			"success": status == "SUCCESS",
			"correlation_id": correlation_id,
			"status": status,
			"error_code": header.get("response_error_code"),
			"error_message": header.get("response_error_message"),
		}

	def get_ingest_status(self, correlation_id: str) -> list[dict]:
		"""Search ingestion rows for a correlation_id.

		POST /ingestion-data/search_in_ingestion_data with the staff envelope,
		using correlation_id as the search text.
		"""
		url = f"{self.base_url}/ingestion-data/search_in_ingestion_data"
		envelope = self._staff_envelope({})
		envelope["request_body"]["pagination_request"]["search_text"] = correlation_id

		headers, cookies = self._auth_and_csrf_headers()

		try:
			response = requests.post(
				url,
				headers=headers,
				cookies=cookies,
				json=envelope,
				timeout=self.timeout,
			)
		except requests.RequestException as e:
			frappe.log_error(
				title="Farmer Registry HTTP Error",
				message=f"Could not reach {url} for ingest status: {e!s}",
			)
			raise FarmerRegistryServerError(f"Farmer Registry unreachable: {e!s}") from e

		self._raise_for_status(response, "get ingest status")
		data = response.json()

		payload = (data.get("response_body", {}) or {}).get("response_payload")
		if isinstance(payload, list):
			return payload
		return [payload] if payload else []
