"""Livestock bearer and CSRF headers from the shared staff IAM login.

The HTTP client forwards the caller's SSO headers instead (see http.py). This
login is for callers without a request, such as scripts.
"""

from __future__ import annotations

from .client import LivestockRegistryClientError, LivestockRegistryServerError


def livestock_auth_headers() -> tuple[dict, dict]:
	"""Return (headers, cookies) for a Livestock staff call. Nothing is sent to the Livestock host."""
	import frappe

	from da_services.integrations.staff_api.auth import auth_and_csrf_headers, keycloak_from_conf

	keycloak = keycloak_from_conf(
		frappe.conf.get("livestock_registry_keycloak"),
		frappe.conf.get("farmer_registry_keycloak"),
	)
	headers = {"Content-Type": "application/json", "Accept": "application/json"}
	api_key = frappe.conf.get("livestock_registry_api_key")
	if api_key:
		headers["Authorization"] = f"Bearer {api_key}"
	try:
		timeout = float(frappe.conf.get("livestock_registry_timeout") or 30)
	except TypeError, ValueError:
		timeout = 30.0
	if timeout <= 0:
		timeout = 30.0
	client_id = frappe.conf.get("livestock_registry_client_id") or "livestock-registry-staff-portal"
	return auth_and_csrf_headers(
		keycloak=keycloak,
		timeout=timeout,
		headers=headers,
		client_error=LivestockRegistryClientError,
		server_error=LivestockRegistryServerError,
		log_title="Livestock Registry Auth Error",
		registry_client_id=str(client_id),
	)
