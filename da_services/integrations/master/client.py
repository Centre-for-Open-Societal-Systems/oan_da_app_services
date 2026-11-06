"""Master Data geography and code-list API, called with the signed-in user's bearer token and CSRF token.

``get_master_data`` answers both kinds of lookup with the same arguments and the same rows.

``geo`` takes two calls:
  1. ``POST /geo/get_all_geo_levels`` maps a level name (``region``) to its ``level_id`` (``l1``).
  2. ``POST /geo/get_geo_level_values`` lists the values of that level, optionally under one parent.

``attribute`` takes one call, ``POST /attributes/get_attribute_values``, for a code list such as
``DOCUMENT_PURPOSE``.

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
DATA_TYPES = ("geo", "attribute")

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1000


def base_url_from_domain(domain: str | None) -> str:
	"""``master-data.example.org`` or ``https://master-data.example.org/`` -> ``https://master-data.example.org``."""
	domain = (domain or "").strip().rstrip("/")
	if not domain:
		raise MasterDataError("Master Data Domain is not set in DA Services Settings", kind="config")
	if not domain.startswith(("http://", "https://")):
		domain = f"https://{domain}"
	return domain


def _first_set(value, fallback):
	return fallback if value is None else value


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
		"""The geo levels, from one call (there are five of them, so one page holds them all)."""
		rows, _pagination = self._page("/geo/get_all_geo_levels", {}, action="read geo levels")
		return rows

	def get_geo_level_values(
		self,
		level_id: str,
		parent_level_value_id: str | None = None,
		search: str = "",
		current_page: int = 1,
		page_size: int = DEFAULT_PAGE_SIZE,
	) -> tuple[list[dict], dict]:
		request = {"level_id": level_id}
		if parent_level_value_id:
			request["parent_level_value_id"] = parent_level_value_id
		return self._page(
			"/geo/get_geo_level_values",
			request,
			action=f"read places {level_id}",
			search=search,
			current_page=current_page,
			page_size=page_size,
		)

	def get_attribute_values(
		self,
		attribute_id: str,
		search: str = "",
		current_page: int = 1,
		page_size: int = DEFAULT_PAGE_SIZE,
	) -> tuple[list[dict], dict]:
		return self._page(
			"/attributes/get_attribute_values",
			{"attribute_id": attribute_id},
			action=f"read code list {attribute_id}",
			search=search,
			current_page=current_page,
			page_size=page_size,
		)

	def get_master_data(
		self,
		data_type: str,
		field_name: str,
		parent: str | None = None,
		search: str = "",
		current_page: int = 1,
		page_size: int = DEFAULT_PAGE_SIZE,
	) -> tuple[list[dict], dict]:
		"""One page of rows, the same shape for both ``data_type`` values, and the page's numbers.

		``geo``: ``field_name`` is a level (``region``). ``attribute``: it is an attribute id
		(``DOCUMENT_PURPOSE``). ``parent`` keeps only the children of that value.

		The page numbers are ``current_page``, ``page_size``, ``total_items`` and ``total_pages``.
		When Master Data does not report the last two, ``total_items`` is the number of rows
		returned and ``total_pages`` is ``current_page``.
		"""
		if data_type == "geo":
			rows, pagination = self._geo_rows(field_name, parent, search, current_page, page_size)
		elif data_type == "attribute":
			rows, pagination = self._attribute_rows(field_name, search, current_page, page_size)
		else:
			raise MasterDataError(f"data_type must be one of {', '.join(DATA_TYPES)}", kind="invalid")
		if parent:
			# Master Data may ignore the parent filter; the answer must be the parent's children either way.
			rows = [r for r in rows if r["parent"] == parent]
		return rows, {
			"current_page": current_page,
			"page_size": page_size,
			"total_items": _first_set(pagination.get("number_of_items"), len(rows)),
			"total_pages": _first_set(pagination.get("number_of_pages"), current_page),
		}

	def _geo_rows(
		self, level: str, parent: str | None, search: str, current_page: int, page_size: int
	) -> tuple[list[dict], dict]:
		if level not in LEVELS:
			raise MasterDataError(f"field_name must be one of {', '.join(LEVELS)} for geo", kind="invalid")
		by_mnemonic = {str(row.get("level_mnemonic")).lower(): row for row in self.get_geo_levels()}
		if level not in by_mnemonic:
			raise MasterDataError(f"Master Data has no {level} level")
		rows, pagination = self.get_geo_level_values(
			by_mnemonic[level]["level_id"], parent, search, current_page, page_size
		)
		return [
			{
				"id": r.get("level_value_id"),
				"code": r.get("level_value_id"),
				"name": r.get("level_value_mnemonic"),
				"field_name": level,
				"parent": r.get("parent_level_value_id"),
				"sort_order": 0,
			}
			for r in rows
		], pagination

	def _attribute_rows(
		self, attribute_id: str, search: str, current_page: int, page_size: int
	) -> tuple[list[dict], dict]:
		rows, pagination = self.get_attribute_values(attribute_id, search, current_page, page_size)
		rows.sort(key=lambda r: r.get("sort_order") or 0)
		return [
			{
				"id": r.get("value_id"),
				"code": r.get("value_code"),
				"name": r.get("value_display"),
				"field_name": r.get("attribute_id") or attribute_id,
				"parent": r.get("parent_value_id"),
				"sort_order": r.get("sort_order") or 0,
			}
			for r in rows
		], pagination

	def _page(
		self,
		path: str,
		request_payload: dict,
		*,
		action: str,
		search: str = "",
		current_page: int = 1,
		page_size: int = DEFAULT_PAGE_SIZE,
	) -> tuple[list[dict], dict]:
		"""POST one call for one page; returns ``(rows, pagination_response)``."""
		url = self.base_url + path
		# Double submit: a server-to-server call has no browser cookie, so the CSRF value
		# goes in the header and, with the same value, in the cookie.
		headers = {
			"Authorization": f"Bearer {self.token}",
			"X-CSRF-Token": self.csrf_token,
			"Content-Type": "application/json",
		}
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
					"current_page": current_page,
					"page_size": page_size,
					"filter_by": {},
					"search_text": search or "",
				},
			},
		}
		payload, pagination = self._send(url, headers, envelope, action)
		if isinstance(payload, dict):
			payload = payload.get("attribute_values")
		rows = [r for r in payload if isinstance(r, dict)] if isinstance(payload, list) else []
		return rows, pagination

	def _send(self, url: str, headers: dict, envelope: dict, action: str) -> tuple[object, dict]:
		"""One POST; returns ``(response_payload, pagination_response)``."""
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
		body = data.get("response_body") or {}
		pagination = body.get("pagination_response")
		return body.get("response_payload"), pagination if isinstance(pagination, dict) else {}
