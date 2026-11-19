"""Shared by the services that call OAN staff APIs as the Master Data Sync account.

Used by the geo master sync (``geo_master_sync.py``) and by the DA Registry assignment
sync (``da_registry_assignment_sync.py``):

- ``sync_account_login`` signs in the Master Data Sync account of DA Services Settings
  (username, password, login key) through ``sso_login``, the function behind
  ``POST /sso/login``, and returns its ``access_token`` and ``csrf_token``.
- ``base_url_from_domain`` turns a settings domain (``host.example.org`` or a full URL)
  into a base URL.
- ``post_staff_api`` makes one staff-envelope POST with those tokens: the access token as
  ``Authorization: Bearer`` and the CSRF token as ``X-CSRF-Token``, header and cookie
  (double submit; a server-to-server call has no browser cookie).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import frappe
import requests
from frappe.utils import get_url

from da_services.integrations.sso import SSOLoginResult, sso_login

SETTINGS_DOCTYPE = "DA Services Settings"


class StaffAPIError(Exception):
	"""``kind`` is one of config, unauthorized, unavailable, rejected."""

	def __init__(self, message: str, *, kind: str = "rejected"):
		super().__init__(message)
		self.kind = kind


def base_url_from_domain(domain: str | None, label: str) -> str:
	"""``host.example.org`` or ``https://host.example.org/`` -> ``https://host.example.org``."""
	domain = (domain or "").strip().rstrip("/")
	if not domain:
		raise StaffAPIError(f"{SETTINGS_DOCTYPE}: set {label}", kind="config")
	if not domain.startswith(("http://", "https://")):
		domain = f"https://{domain}"
	return domain


def sync_account_login(*, also_required: dict[str, str | None] | None = None, login=None) -> SSOLoginResult:
	"""Sign in the Master Data Sync account of DA Services Settings; returns its tokens.

	``also_required`` maps more settings labels to their values; a blank one is reported
	with the account fields. Raises StaffAPIError (kind ``config``) naming every missing
	setting, before any login is attempted, or SSOLoginError when the login fails.
	"""
	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	username = (settings.master_sync_username or "").strip()
	password = settings.get_password("master_sync_password", raise_exception=False) or ""
	key = (settings.master_sync_login_key or "").strip()
	required = {**(also_required or {}), "Username": username, "Password": password, "Login Key": key}
	missing = [label for label, value in required.items() if not value]
	if missing:
		raise StaffAPIError(f"{SETTINGS_DOCTYPE}: set {', '.join(missing)}", kind="config")
	conf = frappe.conf
	return (login or sso_login)(
		username=username,
		password=password,
		key=key,
		return_url=conf.get("sso_return_url") or get_url(),
		timeout=int(conf.get("sso_timeout_seconds") or 20),
	)


def sender_app() -> str:
	return frappe.conf.get("da_registry_sender_app") or "da-services"


def post_staff_api(
	url: str,
	request_payload: dict,
	*,
	tokens: SSOLoginResult,
	action: str,
	page_size: int = 5,
	timeout: int = 20,
):
	"""One staff-envelope POST to ``url``; returns ``response_body.response_payload``.

	Raises StaffAPIError: ``unauthorized`` for HTTP 401/403, ``unavailable`` when the
	server cannot be reached or answers 5xx, ``rejected`` for anything else that is not a
	JSON envelope with ``response_status`` SUCCESS.
	"""
	envelope = {
		"request_header": {
			"request_id": str(uuid.uuid4()),
			"request_timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
			"sender_app_mnemonic": sender_app(),
			"sender_app_url": "",
		},
		"request_body": {
			"request_payload": request_payload,
			"pagination_request": {
				"current_page": 1,
				"page_size": page_size,
				"search_text": "",
				"sort_by": "",
				"filter_by": "",
			},
		},
	}
	try:
		response = requests.post(
			url,
			headers={
				"Authorization": f"Bearer {tokens.access_token}",
				"X-CSRF-Token": tokens.csrf_token,
				"Content-Type": "application/json",
			},
			cookies={"X-CSRF-Token": tokens.csrf_token},
			json=envelope,
			timeout=timeout,
		)
	except requests.RequestException as exc:
		raise StaffAPIError(f"Could not reach the server to {action}: {exc!r}", kind="unavailable") from exc

	if response.status_code in (401, 403):
		raise StaffAPIError(f"Token refused ({action}): HTTP {response.status_code}", kind="unauthorized")
	if response.status_code >= 500:
		raise StaffAPIError(f"Server failed to {action}: HTTP {response.status_code}", kind="unavailable")
	try:
		data = response.json()
	except ValueError:
		data = None
	if not isinstance(data, dict):
		raise StaffAPIError(f"Non-JSON answer to {action} (HTTP {response.status_code})")
	header = data.get("response_header") or {}
	if response.status_code >= 400 or header.get("response_status") != "SUCCESS":
		message = header.get("response_error_message") or header.get("response_error_code") or ""
		raise StaffAPIError(f"Refused to {action}: {message or response.status_code}")
	return (data.get("response_body") or {}).get("response_payload")
