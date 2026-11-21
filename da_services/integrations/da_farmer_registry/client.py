"""Farmer Registry register search, called with the signed-in user's bearer token and CSRF token.

``POST {farmer_registry_domain}/register-data/search_in_a_register`` with the
``request_payload.register_id`` of ``farmer_registry_form_register_id`` (DA Services Settings).
The caller's ``pagination_request`` (``current_page``, ``page_size``, ``filter_by``,
``search_text``) is sent as given, and the answer's ``response_payload`` and
``pagination_response`` are returned unchanged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import frappe
import requests

from .errors import FarmerRegistryError

SETTINGS = "DA Services Settings"
SEARCH_PATH = "/register-data/search_in_a_register"
DEFAULT_SENDER_APP = "OAN_REGISTRY_API_TEST"
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1000


def base_url_from_domain(domain: str | None) -> str:
	"""``farmer.example.org`` or ``https://farmer.example.org/`` -> ``https://farmer.example.org``."""
	domain = (domain or "").strip().rstrip("/")
	if not domain:
		raise FarmerRegistryError("Farmer Registry Domain is not set in DA Services Settings", kind="config")
	if not domain.startswith(("http://", "https://")):
		domain = f"https://{domain}"
	return domain


def client_from_settings(token: str, csrf_token: str) -> FarmerRegistryClient:
	"""A client for the domain and register id of DA Services Settings."""
	settings = (
		frappe.db.get_value(
			SETTINGS, SETTINGS, ["farmer_registry_domain", "farmer_registry_form_register_id"], as_dict=True
		)
		or frappe._dict()
	)
	register_id = (settings.farmer_registry_form_register_id or "").strip()
	if not register_id:
		raise FarmerRegistryError(
			"Farmer Registry Form Register ID is not set in DA Services Settings", kind="config"
		)
	return FarmerRegistryClient(
		base_url=base_url_from_domain(settings.farmer_registry_domain),
		register_id=register_id,
		token=token,
		csrf_token=csrf_token,
		sender_app=frappe.conf.get("farmer_registry_sender_app") or DEFAULT_SENDER_APP,
		timeout=int(frappe.conf.get("farmer_registry_timeout_seconds") or 20),
	)


class FarmerRegistryClient:
	def __init__(
		self,
		*,
		base_url: str,
		register_id: str,
		token: str,
		csrf_token: str,
		sender_app: str = DEFAULT_SENDER_APP,
		timeout: int = 20,
	):
		self.base_url = base_url.rstrip("/")
		self.register_id = register_id
		self.token = token
		self.csrf_token = csrf_token
		self.sender_app = sender_app
		self.timeout = timeout

	def search_in_a_register(
		self,
		*,
		current_page: int = 1,
		page_size: int = DEFAULT_PAGE_SIZE,
		filter_by: dict | None = None,
		search_text: str = "",
	) -> tuple[object, dict]:
		"""One page of the register; returns ``(response_payload, pagination_response)``."""
		envelope = {
			"request_header": {
				"sender_app_mnemonic": self.sender_app,
				"sender_app_url": "",
				"request_id": str(uuid.uuid4()),
				"request_timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
			},
			"request_body": {
				"request_payload": {"register_id": self.register_id},
				"pagination_request": {
					"current_page": current_page,
					"page_size": page_size,
					"filter_by": filter_by or {},
					"search_text": search_text or "",
				},
			},
		}
		return self._send(self.base_url + SEARCH_PATH, envelope)

	def _send(self, url: str, envelope: dict) -> tuple[object, dict]:
		action = "search the register"
		# Double submit: a server-to-server call has no browser cookie, so the CSRF value
		# goes in the header and, with the same value, in the cookie.
		try:
			response = requests.post(
				url,
				headers={
					"Authorization": f"Bearer {self.token}",
					"X-CSRF-Token": self.csrf_token,
					"Content-Type": "application/json",
				},
				cookies={"X-CSRF-Token": self.csrf_token},
				json=envelope,
				timeout=self.timeout,
			)
		except requests.RequestException as exc:
			raise FarmerRegistryError(
				f"Farmer Registry is unreachable ({action}): {exc!r}", kind="unavailable"
			) from exc

		if response.status_code in (401, 403):
			raise FarmerRegistryError(
				f"Farmer Registry refused the token ({action}): HTTP {response.status_code}",
				kind="unauthorized",
			)
		if response.status_code >= 500:
			raise FarmerRegistryError(
				f"Farmer Registry failed to {action}: HTTP {response.status_code}", kind="unavailable"
			)
		try:
			data = response.json()
		except ValueError:
			data = None
		if not isinstance(data, dict):
			raise FarmerRegistryError(
				f"Farmer Registry answered {action} with non-JSON (HTTP {response.status_code})"
			)

		header = data.get("response_header") or {}
		if response.status_code >= 400 or header.get("response_status") != "SUCCESS":
			message = header.get("response_error_message") or header.get("response_error_code") or ""
			raise FarmerRegistryError(
				f"Farmer Registry refused to {action}: {message or response.status_code}"
			)
		body = data.get("response_body") or {}
		pagination = body.get("pagination_response")
		return body.get("response_payload"), pagination if isinstance(pagination, dict) else {}
