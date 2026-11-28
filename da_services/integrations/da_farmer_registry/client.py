"""Farmer Registry reads, called with the signed-in user's bearer token and CSRF token.

The domain is ``farmer_registry_domain`` and the register ``farmer_registry_form_register_id``
of DA Services Settings.

- ``search_in_a_register``: ``POST /register-data/search_in_a_register`` with
  ``request_payload.register_id``. The caller's ``pagination_request`` (``current_page``,
  ``page_size``, ``filter_by``, ``search_text``) is sent as given.
- ``get_subject_record``: ``POST /register-data/get_subject_record`` with the register and
  the farmer's ``internal_record_id``: the farmer's record.
- ``get_tab_records``: ``POST /register-data/get_tab_records`` with the register, the
  farmer's ``internal_record_id`` and a tab id: the records of that tab (``LAND_TAB_ID``
  for the farmer's land).

A ``display_fields`` list in the answer's ``response_payload`` is turned into plain keys
(``field_name`` -> ``value``); the rest of the payload and the ``pagination_response`` are
returned unchanged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import frappe
import requests

from .errors import FarmerRegistryError

SETTINGS = "DA Services Settings"
SEARCH_PATH = "/register-data/search_in_a_register"
SUBJECT_RECORD_PATH = "/register-data/get_subject_record"
TAB_RECORDS_PATH = "/register-data/get_tab_records"
# The Lands tab of the Farmer register.
LAND_TAB_ID = "farmer_land_tab"
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


def flatten_display_fields(row):
	"""A row with its ``display_fields`` list turned into keys: each
	``{"field_name": "first_name", "value": "Signoff"}`` becomes ``"first_name": "Signoff"``.
	``display_fields`` itself is removed; a row without it is returned unchanged."""
	if not isinstance(row, dict) or "display_fields" not in row:
		return row
	out = {k: v for k, v in row.items() if k != "display_fields"}
	for field in row.get("display_fields") or []:
		if isinstance(field, dict) and field.get("field_name"):
			out[field["field_name"]] = field.get("value")
	return out


def _plain_keys(payload):
	"""``payload`` with ``display_fields`` turned into keys, on the record or on each row."""
	if isinstance(payload, list):
		return [flatten_display_fields(row) for row in payload]
	return flatten_display_fields(payload)


# pagination_request of a read by id: the registry wants one, with nothing to search or filter.
_BY_ID = {
	"current_page": 1,
	"page_size": DEFAULT_PAGE_SIZE,
	"search_text": "",
	"sort_by": "",
	"filter_by": "",
}


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
		envelope = self._envelope(
			{"register_id": self.register_id},
			{
				"current_page": current_page,
				"page_size": page_size,
				"filter_by": filter_by or {},
				"search_text": search_text or "",
			},
		)
		payload, pagination = self._send(SEARCH_PATH, envelope, action="search the register")
		return _plain_keys(payload), pagination

	def get_subject_record(self, internal_id: str) -> object:
		"""The farmer's record, by ``internal_record_id``; returns the ``response_payload``."""
		envelope = self._envelope(
			{"subject_register_id": self.register_id, "subject_record_id": internal_id}, dict(_BY_ID)
		)
		payload, _pagination = self._send(SUBJECT_RECORD_PATH, envelope, action="get the farmer")
		return _plain_keys(payload)

	def get_tab_records(self, internal_id: str, tab_id: str) -> tuple[object, dict]:
		"""The records of one tab of a farmer; returns ``(response_payload, pagination_response)``."""
		envelope = self._envelope(
			{"subject_register_id": self.register_id, "subject_record_id": internal_id, "tab_id": tab_id},
			dict(_BY_ID),
		)
		payload, pagination = self._send(TAB_RECORDS_PATH, envelope, action="get the farmer's tab")
		return _plain_keys(payload), pagination

	def _envelope(self, request_payload: dict, pagination_request: dict) -> dict:
		return {
			"request_header": {
				"sender_app_mnemonic": self.sender_app,
				"sender_app_url": "",
				"request_id": str(uuid.uuid4()),
				"request_timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
			},
			"request_body": {"request_payload": request_payload, "pagination_request": pagination_request},
		}

	def _send(self, path: str, envelope: dict, *, action: str) -> tuple[object, dict]:
		url = self.base_url + path
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
