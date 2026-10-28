"""OAN Grievance Service client boundary.

DA Services never owns grievance data. It talks to the Grievance Service through this
interface only. Two implementations exist:

- ``MockGrievanceClient`` (mock.py): in-memory fixtures for local development and tests.
- ``HttpGrievanceClient``: the Development Agent flow in the grievance Postman collection.
  Login is K1-K5 (agent realm). Reads and the walk-in submit use that bearer token only.

Select the backend per site in ``site_config.json``. See ``docs/grievance-da-http.md``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import requests

from .auth import AgentLoginError, AgentTokenProvider
from .mapper import (
	error_text,
	normalize_case,
	normalize_event,
	normalize_summary,
	ticket_from_duplicate,
	to_grievance_detail,
	to_grievance_result,
	to_grievance_summary,
	to_options,
	to_timeline_entry,
	to_upstream_submission,
	unwrap_data,
)
from .schemas import (
	GrievanceAction,
	GrievanceDetail,
	GrievanceResult,
	GrievanceSubmission,
	GrievanceSummary,
)


class GrievanceServiceError(Exception):
	pass


class GrievanceNotFound(GrievanceServiceError):
	pass


class GrievanceServiceUnavailable(GrievanceServiceError):
	"""Transient: timeout, connection error, 5xx. Callers may retry."""

	http_status_code = 503


class GrievanceRejected(GrievanceServiceError):
	"""Upstream refused the call (4xx). Not queued."""


class GrievanceAlreadyFiled(GrievanceServiceError):
	"""Same client_submission_uuid. ``ticket_number`` is the original case."""

	def __init__(self, ticket_number: str):
		super().__init__(ticket_number)
		self.ticket_number = ticket_number


class GrievanceClient(ABC):
	@abstractmethod
	def submit_grievance(self, submission: GrievanceSubmission) -> GrievanceResult:
		"""Create a grievance (draft → submit). Returns the ticket reference."""

	@abstractmethod
	def list_grievances(
		self,
		*,
		region: str | list[str] | None = None,
		woreda: str | list[str] | None = None,
		complainant_id: str | list[str] | None = None,
		status: str | None = None,
		page: int = 1,
		page_size: int = 20,
		service_category: str | None = None,
		grievance_type: str | None = None,
		search: str | None = None,
		administrative_area: str | None = None,
		from_date: str | None = None,
		to_date: str | None = None,
	) -> tuple[list[GrievanceSummary], int]:
		"""Scoped grievance list. Returns (items, total_count)."""

	@abstractmethod
	def get_grievance(self, ticket_id: str) -> GrievanceDetail:
		"""Fetch full grievance detail. Raises GrievanceNotFound."""

	@abstractmethod
	def take_action(self, action: GrievanceAction) -> GrievanceResult:
		"""Resolve, escalate, reassign, or add a note."""

	@abstractmethod
	def get_timeline(self, ticket_id: str) -> list[dict]:
		"""Fetch the timeline of events for a ticket."""

	@abstractmethod
	def get_options(self) -> dict:
		"""Fetch category / priority / status options for form dropdowns."""


def _csv(value: str | list[str] | None) -> str | None:
	if value is None:
		return None
	if isinstance(value, list):
		return ",".join(value)
	return value


class HttpGrievanceClient(GrievanceClient):
	"""Live client. Paths and the walk-in body follow the grievance collection."""

	def __init__(self, base_url: str, timeout: int = 20, tokens: AgentTokenProvider | None = None):
		self.base_url = base_url.rstrip("/")
		self.timeout = timeout
		self.tokens = tokens or AgentTokenProvider(base_url=self.base_url, timeout=timeout)

	def _url(self, path: str) -> str:
		return f"{self.base_url}{path}"

	def _request(self, method: str, path: str, *, params=None, json_body=None, auth: bool = True):
		headers = {"Accept": "application/json"}
		if json_body is not None:
			headers["Content-Type"] = "application/json"
		if auth:
			try:
				headers["Authorization"] = f"Bearer {self.tokens.get_token()}"
			except AgentLoginError as exc:
				if exc.unavailable:
					raise GrievanceServiceUnavailable(str(exc)) from exc
				raise GrievanceServiceError(str(exc)) from exc
		try:
			response = requests.request(
				method,
				self._url(path),
				params=params,
				json=json_body,
				headers=headers,
				timeout=self.timeout,
			)
		except (requests.ConnectionError, requests.Timeout) as exc:
			raise GrievanceServiceUnavailable(str(exc)) from exc
		return self._handle_response(response)

	def _handle_response(self, response: requests.Response):
		text = response.text or ""
		try:
			body = response.json()
		except ValueError:
			body = {}
		if response.status_code == 404:
			raise GrievanceNotFound(text)
		if response.status_code >= 500:
			raise GrievanceServiceUnavailable(f"HTTP {response.status_code}")
		duplicate = ticket_from_duplicate(error_text(body, text))
		if duplicate and (
			response.status_code >= 400 or (isinstance(body, dict) and body.get("status") == "error")
		):
			raise GrievanceAlreadyFiled(duplicate)
		if response.status_code >= 400 or (isinstance(body, dict) and body.get("status") == "error"):
			raise GrievanceRejected(error_text(body, text) or f"HTTP {response.status_code}")
		data = unwrap_data(body)
		return data if data is not None else {}

	def submit_grievance(self, submission: GrievanceSubmission) -> GrievanceResult:
		try:
			data = self._request("POST", "/v1/grievances", json_body=to_upstream_submission(submission))
		except GrievanceAlreadyFiled as exc:
			return to_grievance_result(
				{
					"accepted": True,
					"ticket_id": exc.ticket_number,
					"ticket_number": exc.ticket_number,
					"message": "Already submitted",
				}
			)
		if not isinstance(data, dict):
			data = {}
		return to_grievance_result({"accepted": True, "message": "Grievance submitted", **data})

	def list_grievances(
		self,
		*,
		region: str | list[str] | None = None,
		woreda: str | list[str] | None = None,
		complainant_id: str | list[str] | None = None,
		status: str | None = None,
		page: int = 1,
		page_size: int = 20,
		service_category: str | None = None,
		grievance_type: str | None = None,
		search: str | None = None,
		administrative_area: str | None = None,
		from_date: str | None = None,
		to_date: str | None = None,
	) -> tuple[list[GrievanceSummary], int]:
		# complainant_id is a DA Registry id. The agent token's own scope is the list.
		del complainant_id
		# Review Officer R1: page, page_size, sort_order. No sort_by.
		params = {"page": page, "page_size": page_size, "sort_order": "desc"}
		for key, value in (
			("region", _csv(region)),
			("woreda", _csv(woreda)),
			("status", status),
			("service_category", service_category),
			("grievance_type", grievance_type),
			("search", search),
			("administrative_area", administrative_area),
			("from_date", from_date),
			("to_date", to_date),
		):
			if value:
				params[key] = value
		data = self._request("GET", "/v1/grievances", params=params)
		if not isinstance(data, dict):
			data = {}
		rows = data.get("items") or data.get("results") or []
		items = [to_grievance_summary(normalize_summary(row)) for row in rows]
		pagination = data.get("pagination") or {}
		total = int(pagination.get("total_count", pagination.get("total", data.get("total", len(items)))))
		return items, total

	def get_grievance(self, ticket_id: str) -> GrievanceDetail:
		try:
			data = self._request("GET", f"/v1/grievances/{ticket_id}")
		except GrievanceNotFound:
			data = self._request("GET", f"/v1/grievances/{ticket_id}/timeline")
		if not isinstance(data, dict):
			raise GrievanceNotFound(ticket_id)
		return to_grievance_detail(normalize_case(data))

	def take_action(self, action: GrievanceAction) -> GrievanceResult:
		"""Officer action. Not exposed on the DA routes. Body matches collection K11/K13."""
		data = self._request(
			"POST",
			f"/v1/grievances/{action.ticket_id}/action",
			json_body={
				"action": action.action_type,
				"reason": action.comment or action.resolution or "",
			},
		)
		if not isinstance(data, dict):
			data = {}
		return to_grievance_result({"accepted": True, "ticket_id": action.ticket_id, **data})

	def get_timeline(self, ticket_id: str) -> list[dict]:
		data = self._request("GET", f"/v1/grievances/{ticket_id}/timeline")
		entries = data.get("timeline") if isinstance(data, dict) else data
		if not isinstance(entries, list):
			entries = []
		return [
			to_timeline_entry(normalize_event(entry)).to_dict()
			for entry in entries
			if isinstance(entry, dict)
		]

	def get_options(self) -> dict:
		data = self._request("GET", "/v1/submitters/options", auth=False)
		if not isinstance(data, dict):
			return {}
		return to_options(data)


def get_client() -> GrievanceClient:
	"""Build the configured client. Defaults to mock outside production."""
	import frappe

	backend = (frappe.conf.get("grievance_backend") or "mock").lower()
	if backend == "mock":
		from .mock import MockGrievanceClient

		return MockGrievanceClient.shared()
	if backend == "http":
		base_url = frappe.conf.get("grievance_base_url")
		if not base_url:
			raise GrievanceServiceError("grievance_base_url is not set in site_config.json")
		iam = frappe.conf.get("grievance_iam") or {}
		timeout = int(frappe.conf.get("grievance_timeout_seconds") or 20)
		static = frappe.conf.get("grievance_api_token") or ""
		if isinstance(static, str) and static.startswith("Bearer "):
			static = static.removeprefix("Bearer ").strip()
		return HttpGrievanceClient(
			base_url,
			timeout,
			AgentTokenProvider(
				base_url=base_url,
				timeout=timeout,
				login_key=iam.get("login_key") or "agent",
				# K1 sends the grievance base URL as return_url, not the Keycloak hint in the environment.
				return_url=iam.get("return_url") or base_url,
				username=iam.get("username") or "",
				password=iam.get("password") or "",
				static_token=static,
			),
		)
	raise GrievanceServiceError(f"Unknown grievance_backend: {backend!r}")
