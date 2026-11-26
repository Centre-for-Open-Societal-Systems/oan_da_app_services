"""Shared utils for Crop / Livestock staff registry GET paths."""

from __future__ import annotations

import frappe
from frappe import _


def page_count(pagination: dict) -> int:
	try:
		count = int(pagination.get("number_of_pages") or 1)
	except TypeError, ValueError:
		return 1
	return count if count > 0 else 1


def row_matches_farmer(row: dict, farmer_id: str) -> bool:
	"""True when a search row names this farmer (submission or register hit)."""
	wanted = str(farmer_id)
	for key in ("record_name", "functional_record_id", "link_internal_record_id"):
		if str(row.get(key) or "") == wanted:
			return True
	for field in row.get("display_fields") or []:
		if not isinstance(field, dict):
			continue
		if field.get("field_name") == "farmer_id" and str(field.get("value") or "") == wanted:
			return True
		if str(field.get("value") or "") == wanted:
			return True
	return False


def filter_search_hits(rows: list[dict], farmer_ids: str | set[str]) -> list[dict]:
	"""Keep rows that match any farmer id; dedupe by submission or register id."""
	wanted = {str(farmer_ids)} if isinstance(farmer_ids, str) else {str(item) for item in farmer_ids}
	found: list[dict] = []
	seen: set[str] = set()
	for row in rows:
		if not any(row_matches_farmer(row, candidate) for candidate in wanted):
			continue
		key = str(row.get("submission_id") or row.get("internal_record_id") or "")
		if key and key in seen:
			continue
		if key:
			seen.add(key)
		found.append(dict(row))
	return found


def register_tab_payload(
	*,
	internal_record_id: str,
	register_id: str,
	tab_id: str,
	records: list,
) -> dict:
	"""Envelope returned by Crop / Livestock register-tab GETs."""
	return {
		"internal_record_id": internal_record_id,
		"register_id": register_id,
		"tab_id": tab_id,
		"tabs": [{"tab_id": tab_id, "records": records}],
	}


def search_by_source(transport, *, source: str, search_text: str, label: str, client_error) -> list[dict]:
	"""One staff search for ``submission`` or ``register``."""
	if source == "submission":
		return transport.search_submissions(search_text=search_text, action=f"search {label} submissions")
	if source == "register":
		return transport.search_register(search_text=search_text, action=f"search {label} register")
	raise client_error(f"unsupported {label} list source: {source}")


def read_register_tab(
	transport,
	*,
	internal_record_id: str,
	tab_id: str,
	register_id: str,
	action: str,
	client_error,
) -> dict:
	"""One ``get_tab_records`` round trip and the shared register-tab envelope."""
	record_id = (internal_record_id or "").strip()
	wanted_tab = (tab_id or "").strip()
	if not record_id:
		raise client_error("internal_record_id is required")
	if not wanted_tab:
		raise client_error("tab_id is required")
	records = transport.get_tab_records(
		subject_record_id=record_id,
		tab_id=wanted_tab,
		action=action,
	)
	if not isinstance(records, list):
		raise client_error("register tab records were not a list")
	return register_tab_payload(
		internal_record_id=record_id,
		register_id=register_id,
		tab_id=wanted_tab,
		records=records,
	)


def parse_registry_get_query(form_dict) -> tuple[str, str, str, str]:
	"""FE query for Crop / Livestock GET: submission, register tab, or list.

	Returns ``(submission_id, internal_record_id, tab_id, source)``.
	Enforces mutual exclusivity of ids, ``tab_id`` with register detail, and
	required ``source`` on list. Raises ``frappe.ValidationError`` once here.
	"""
	submission_id = (form_dict.get("submission_id") or "").strip()
	internal_record_id = (form_dict.get("internal_record_id") or "").strip()
	if submission_id and internal_record_id:
		frappe.throw(
			_("Pass submission_id or internal_record_id, not both."),
			frappe.ValidationError,
		)
	tab_id = (form_dict.get("tab_id") or "").strip()
	source = (form_dict.get("source") or "").strip().lower()
	if internal_record_id and not tab_id:
		frappe.throw(_("tab_id is required with internal_record_id."), frappe.ValidationError)
	if not submission_id and not internal_record_id and not source:
		frappe.throw(_("source is required."), frappe.ValidationError)
	return submission_id, internal_record_id, tab_id, source
