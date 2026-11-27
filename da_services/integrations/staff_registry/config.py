"""Desk Settings → staff URL + register id for registry modules."""

from __future__ import annotations

import frappe

from da_services.services.utils import SETTINGS_DOCTYPE, base_url_from_domain, required_setting_value


def load_registry_module_config(
	*,
	domain_field: str,
	register_field: str,
	domain_label: str,
	register_label: str,
) -> tuple[str, str]:
	"""Return ``(staff_base_url, register_id)`` from DA Services Settings.

	Fails closed via ``StaffAPIError`` when either value is blank.
	"""
	settings = (
		frappe.db.get_value(
			SETTINGS_DOCTYPE,
			SETTINGS_DOCTYPE,
			[domain_field, register_field],
			as_dict=True,
		)
		or frappe._dict()
	)
	return (
		base_url_from_domain(settings.get(domain_field), domain_label),
		required_setting_value(settings.get(register_field), register_label),
	)


def master_data_url_from_settings() -> str | None:
	"""Optional Master Data Domain from DA Services Settings."""
	domain = (frappe.db.get_single_value(SETTINGS_DOCTYPE, "master_data_domain") or "").strip()
	if not domain:
		return None
	return base_url_from_domain(domain, "Master Data Domain")
