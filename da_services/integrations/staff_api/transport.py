"""OpenG2P staff envelope and HTTP failure classification.

Farmer requests include ``instance_id`` and use an empty string when there is
no filter. Crop Sown omits ``instance_id`` and uses an empty object. Callers
pass those choices so existing payloads stay the same.
"""

from __future__ import annotations

import uuid

import requests


def staff_envelope(
	request_payload: dict,
	*,
	sender_app: str,
	timestamp: str,
	page: int = 1,
	page_size: int = 50,
	filter_by: dict | None = None,
	search_text: str = "",
	sort_by: str = "",
	include_instance_id: bool = True,
	include_sort_by: bool = True,
	empty_filter: dict | str = "",
) -> dict:
	"""Wrap a payload in the staff request envelope."""
	header = {
		"sender_app_mnemonic": sender_app,
		"sender_app_url": "",
		"request_id": str(uuid.uuid4()),
		"request_timestamp": timestamp,
	}
	if include_instance_id:
		header["instance_id"] = ""
	pagination: dict = {
		"current_page": page,
		"page_size": page_size,
	}
	if include_sort_by:
		pagination["sort_by"] = sort_by
	pagination["filter_by"] = filter_by if filter_by else empty_filter
	pagination["search_text"] = search_text
	return {
		"request_header": header,
		"request_body": {
			"request_payload": request_payload,
			"pagination_request": pagination,
		},
	}


def http_failure(response: requests.Response) -> tuple[str, int, str] | None:
	"""Return ('client'|'server', status, snippet) for an HTTP error, else None.

	4xx is a client failure. 5xx is a server failure. The snippet prefers the
	staff envelope error message and never includes a token.
	"""
	code = response.status_code
	if code < 400:
		return None
	detail = ""
	try:
		detail = response.json().get("response_header", {}).get("response_error_message") or ""
	except ValueError:
		pass
	snippet = detail or (response.text or "")[:300]
	kind = "client" if code < 500 else "server"
	return kind, code, snippet
