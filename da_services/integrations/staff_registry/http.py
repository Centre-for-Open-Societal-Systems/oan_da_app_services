"""Shared OpenG2P staff HTTP transport for registry GET/write envelopes.

Modules pass their client/server error classes and a log title. Auth forwards
the FE ``Authorization`` and ``X-CSRF-Token`` (header + cookie). Register id
comes from Settings via the factory — no ``get_all_registers``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import frappe
import requests

from da_services.integrations.staff_api.transport import http_failure, staff_envelope

from .utils import page_count

_DEFAULT_SENDER_APP = "OAN_REGISTRY_API_TEST"
_DEFAULT_TIMEOUT = 30


class StaffRegistryTransport:
	"""Staff POST helper shared by Crop Sown and Livestock clients."""

	def __init__(
		self,
		*,
		staff_url: str,
		register_id: str,
		log_title: str,
		client_error: type[Exception],
		server_error: type[Exception],
		master_data_url: str | None = None,
		sender_app: str = _DEFAULT_SENDER_APP,
		timeout: int = _DEFAULT_TIMEOUT,
		tokens=None,
	):
		self.staff_url = staff_url.rstrip("/")
		self.register_id = (register_id or "").strip()
		self.master_data_url = master_data_url.rstrip("/") if master_data_url else None
		self.sender_app = sender_app
		self.timeout = timeout
		self._tokens = tokens
		self._log_title = log_title
		self._client_error = client_error
		self._server_error = server_error
		self.headers = {"Content-Type": "application/json", "Accept": "application/json"}

	def _log(self, message: str) -> None:
		frappe.log_error(title=self._log_title, message=message)

	def _raise_client(self, message: str, status_code: int | None = None) -> None:
		exc = self._client_error(message, status_code=status_code)
		raise exc

	def _raise_server(self, message: str, status_code: int | None = None) -> None:
		exc = self._server_error(message, status_code=status_code)
		raise exc

	def auth_and_csrf_headers(self) -> tuple[dict[str, str], dict[str, str]]:
		"""Headers + cookies for a staff POST. CSRF always from the FE request."""
		headers = dict(self.headers)
		csrf = frappe.get_request_header("X-CSRF-Token")
		if self._tokens is not None:
			auth = f"Bearer {self._tokens.get_token()}"
		else:
			auth = frappe.get_request_header("Authorization")
		if not auth or not csrf:
			self._raise_client("Missing Authorization or X-CSRF-Token header", status_code=401)
		headers["Authorization"] = auth
		headers["X-CSRF-Token"] = csrf
		return headers, {"X-CSRF-Token": csrf}

	def _envelope(
		self, payload: dict, *, page: int = 1, filter_by: dict | None = None, search_text: str = ""
	) -> dict:
		return staff_envelope(
			payload,
			sender_app=self.sender_app,
			timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
			page=page,
			page_size=100,
			filter_by=filter_by,
			search_text=search_text,
			include_instance_id=False,
			include_sort_by=False,
			empty_filter={},
		)

	def post(
		self,
		path: str,
		payload: dict,
		*,
		action: str,
		page: int = 1,
		filter_by: dict | None = None,
		search_text: str = "",
		base: str | None = None,
	):
		body, _pagination = self.post_page(
			path,
			payload,
			action=action,
			page=page,
			filter_by=filter_by,
			search_text=search_text,
			base=base,
		)
		return body

	def post_page(
		self,
		path: str,
		payload: dict,
		*,
		action: str,
		page: int = 1,
		filter_by: dict | None = None,
		search_text: str = "",
		base: str | None = None,
	) -> tuple[object, dict]:
		url = f"{base or self.staff_url}{path}"
		headers, cookies = self.auth_and_csrf_headers()
		try:
			response = requests.post(
				url,
				headers=headers,
				cookies=cookies,
				json=self._envelope(payload, page=page, filter_by=filter_by, search_text=search_text),
				timeout=self.timeout,
			)
		except requests.RequestException as exc:
			self._log(f"Could not reach {url} ({action}): {exc}")
			self._raise_server(f"{self._log_title} unreachable: {exc}")
			raise  # pragma: no cover
		failure = http_failure(response)
		if failure is not None:
			kind, code, snippet = failure
			if kind == "client":
				self._log(f"{action}: HTTP {code} {response.url}\n{snippet}")
				self._raise_client(
					f"{self._log_title} rejected {action} (HTTP {code}): {snippet}",
					status_code=code,
				)
			self._log(f"{action}: HTTP {code} {response.url}\n{snippet}")
			self._raise_server(
				f"{self._log_title} failed during {action} (HTTP {code})",
				status_code=code,
			)
		try:
			body = response.json()
		except ValueError:
			self._raise_server(f"{self._log_title} returned non-JSON for {action}")
			raise  # pragma: no cover
		if not isinstance(body, dict):
			self._raise_server(f"{self._log_title} returned a non-object for {action}")
		header = body.get("response_header") or {}
		if header.get("response_status") != "SUCCESS":
			message = (
				header.get("response_error_message") or header.get("response_error_code") or "unknown error"
			)
			self._raise_client(f"{self._log_title} rejected {action}: {message}", status_code=200)
		response_body = body.get("response_body") or {}
		pagination = response_body.get("pagination_response") or {}
		return response_body.get("response_payload"), pagination if isinstance(pagination, dict) else {}

	def search_pages(self, path: str, payload: dict, *, action: str, search_text: str) -> list[dict]:
		rows: list[dict] = []
		page = 1
		pages = 1
		while page <= pages:
			payload_page, pagination = self.post_page(
				path, payload, action=action, page=page, search_text=search_text
			)
			if isinstance(payload_page, list):
				rows.extend(row for row in payload_page if isinstance(row, dict))
			elif not payload_page:
				break
			pages = page_count(pagination)
			page += 1
		return rows

	def search_submissions(self, *, search_text: str, action: str) -> list[dict]:
		return self.search_pages(
			"/intake-form-data/search_in_intake_form_submissions",
			{"register_id": self.register_id},
			action=action,
			search_text=search_text,
		)

	def search_register(self, *, search_text: str, action: str) -> list[dict]:
		return self.search_pages(
			"/register-data/search_in_a_register",
			{"register_id": self.register_id},
			action=action,
			search_text=search_text,
		)

	def get_intake_form_submission(self, submission_id: str, *, action: str):
		return self.post(
			"/intake-form-data/get_intake_form_submission",
			{"submission_id": submission_id},
			action=action,
		)

	def get_tab_records(
		self,
		*,
		subject_record_id: str,
		tab_id: str,
		action: str,
		submission_id: str | None = None,
	):
		"""Register tab (``subject_*``) or intake submission tab (``submission_id``)."""
		if submission_id:
			payload = {"submission_id": submission_id, "tab_id": tab_id}
			path = "/intake-form-data/get_tab_records"
		else:
			payload = {
				"subject_register_id": self.register_id,
				"subject_record_id": subject_record_id,
				"tab_id": tab_id,
			}
			path = "/register-data/get_tab_records"
		return self.post(path, payload, action=action)

	def attribute_values(self, attribute_id: str, *, action: str) -> list:
		if not self.master_data_url:
			return []
		rows: list = []
		page = 1
		pages = 1
		while page <= pages:
			payload, pagination = self.post_page(
				"/attributes/get_attribute_values",
				{"attribute_id": attribute_id},
				action=action,
				page=page,
				base=self.master_data_url,
			)
			values = payload.get("attribute_values") if isinstance(payload, dict) else None
			if not isinstance(values, list):
				self._raise_client(f"{self._log_title} code list was not a list")
			rows.extend(values)
			pages = page_count(pagination)
			page += 1
		return rows
