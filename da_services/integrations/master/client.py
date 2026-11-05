"""Master Data geography API, called with the signed-in user's bearer token and CSRF token.

Two calls make one lookup:
  1. ``POST /geo/get_all_geo_levels`` maps a level name (``region``) to its ``level_id`` (``l1``).
  2. ``POST /geo/get_geo_level_values`` lists the values of that level, optionally under one parent.

The base URL is ``master_data_domain`` of DA Services Settings: a bare host
(``master-data.example.org``) or a full URL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import requests

from .errors import MasterDataError

# Master Data level mnemonic, top first. The parent of each is the one before it.
LEVELS = ("country", "region", "zone", "woreda", "kebele")

_PAGE_SIZE = 1000
_MAX_PAGES = 50


def base_url_from_domain(domain: str | None) -> str:
	"""``master-data.example.org`` or ``https://master-data.example.org/`` -> ``https://master-data.example.org``."""
	domain = (domain or "").strip().rstrip("/")
	if not domain:
		raise MasterDataError("Master Data Domain is not set in DA Services Settings", kind="config")
	if not domain.startswith(("http://", "https://")):
		domain = f"https://{domain}"
	return domain


class MasterDataClient:
	def __init__(
		self,
		*,
		base_url: str,
		token: str,
		csrf_token: str,
		sender_app: str = "da-services",
		timeout: int = 20,
	):
		self.base_url = base_url.rstrip("/")
		self.token = token
		self.csrf_token = csrf_token
		self.sender_app = sender_app
		self.timeout = timeout

	def get_geo_levels(self) -> list[dict]:
		return self._post("/geo/get_all_geo_levels", {}, action="read geo levels")

	def get_geo_level_values(
		self, level_id: str, parent_level_value_id: str | None = None, search: str = ""
	) -> list[dict]:
		request = {"level_id": level_id}
		if parent_level_value_id:
			request["parent_level_value_id"] = parent_level_value_id
		return self._post(
			"/geo/get_geo_level_values", request, action=f"read places {level_id}", search=search
		)

	def get_master_data(self, level: str, parent: str | None = None, search: str = "") -> list[dict]:
		"""Values of ``level``; under ``parent`` (a ``level_value_id`` of the level above) when given."""
		if level not in LEVELS:
			raise MasterDataError(f"level must be one of {', '.join(LEVELS)}", kind="invalid")
		by_mnemonic = {str(row.get("level_mnemonic")).lower(): row for row in self.get_geo_levels()}
		if level not in by_mnemonic:
			raise MasterDataError(f"Master Data has no {level} level")
		level_id = by_mnemonic[level]["level_id"]

		rows = self.get_geo_level_values(level_id, parent, search)
		if parent:
			# Master Data may ignore the parent filter; the answer must be the parent's children either way.
			rows = [r for r in rows if r.get("parent_level_value_id") == parent]
		return [
			{
				"code": r.get("level_value_id"),
				"name": r.get("level_value_mnemonic"),
				"level": level,
				"level_id": r.get("level_id") or level_id,
				"parent": r.get("parent_level_value_id"),
			}
			for r in rows
		]

	def _post(self, path: str, request_payload: dict, *, action: str, search: str = "") -> list[dict]:
		"""POST one call, following pages until a short one; returns ``response_payload``."""
		url = self.base_url + path
		# Double submit: a server-to-server call has no browser cookie, so the CSRF value
		# goes in the header and, with the same value, in the cookie.
		headers = {
			"Authorization": f"Bearer {self.token}",
			"X-CSRF-Token": self.csrf_token,
			"Content-Type": "application/json",
		}
		out: list[dict] = []
		for page in range(1, _MAX_PAGES + 1):
			envelope = {
				"request_header": {
					"request_id": str(uuid.uuid4()),
					"request_timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
					"sender_app_mnemonic": self.sender_app,
					"sender_app_url": f"https://{self.sender_app}",
				},
				"request_body": {
					"request_payload": request_payload,
					"pagination_request": {
						"current_page": page,
						"page_size": _PAGE_SIZE,
						"filter_by": {},
						"search_text": search or "",
					},
				},
			}
			rows = self._send(url, headers, envelope, action)
			out.extend(rows)
			if len(rows) < _PAGE_SIZE:
				break
		return out

	def _send(self, url: str, headers: dict, envelope: dict, action: str) -> list[dict]:
		try:
			response = requests.post(
				url,
				headers=headers,
				cookies={"X-CSRF-Token": self.csrf_token},
				json=envelope,
				timeout=self.timeout,
			)
		except requests.RequestException as exc:
			raise MasterDataError(
				f"Master Data is unreachable ({action}): {exc!r}", kind="unavailable"
			) from exc

		if response.status_code in (401, 403):
			raise MasterDataError(
				f"Master Data refused the token ({action}): HTTP {response.status_code}", kind="unauthorized"
			)
		if response.status_code >= 500:
			raise MasterDataError(
				f"Master Data failed to {action}: HTTP {response.status_code}", kind="unavailable"
			)
		try:
			data = response.json()
		except ValueError:
			data = None
		if not isinstance(data, dict):
			raise MasterDataError(
				f"Master Data answered {action} with non-JSON (HTTP {response.status_code})"
			)

		header = data.get("response_header") or {}
		if response.status_code >= 400 or header.get("response_status") != "SUCCESS":
			message = header.get("response_error_message") or header.get("response_error_code") or ""
			raise MasterDataError(f"Master Data refused to {action}: {message or response.status_code}")
		payload = (data.get("response_body") or {}).get("response_payload")
		return [r for r in payload if isinstance(r, dict)] if isinstance(payload, list) else []
