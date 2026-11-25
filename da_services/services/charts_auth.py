"""Keycloak client-credentials auth for central dashboard chart feeders.

Verifies Bearer JWT against the staff realm: trusted issuer, audience (charts
client), and role ``charts:read``. Does not use portal user JWT, ``require_roles``,
or Frappe User provisioning — the BFF is a service account, not a DA session.

Config lives on **DA Services Settings** (Charts Auth section). Local / isolated
tests: leave Charts Auth Enabled unchecked (Confluence empty clientId). When
enabled, missing or invalid tokens fail closed (401/403).
"""

from __future__ import annotations

from typing import Any

import frappe
import jwt
from frappe import _
from jwt import PyJWKClient

from da_services.services import constants as C

SETTINGS = "DA Services Settings"
_JWKS_CLIENTS: dict[str, PyJWKClient] = {}


def _settings():
	return frappe.get_cached_doc(SETTINGS)


def charts_auth_enabled() -> bool:
	"""Whether chart feeders must verify a staff-realm Bearer token."""
	return bool(_settings().charts_auth_enabled)


def _charts_config() -> dict[str, str]:
	settings = _settings()
	issuer = str(settings.charts_auth_issuer or "").rstrip("/")
	audience = str(settings.charts_auth_audience or "").strip()
	return {
		"issuer": issuer,
		"audience": audience,
		"role": C.CHARTS_READ_ROLE,
		"jwks_url": f"{issuer}/protocol/openid-connect/certs" if issuer else "",
	}


def _bearer_token() -> str | None:
	request = getattr(frappe.local, "request", None)
	if not request:
		return None
	auth = request.headers.get("Authorization") or ""
	if not auth.lower().startswith("bearer "):
		return None
	token = auth[7:].strip()
	return token or None


def _audience_matches(claims: dict[str, Any], audience: str) -> bool:
	aud = claims.get("aud")
	if isinstance(aud, str) and aud == audience:
		return True
	if isinstance(aud, (list, tuple)) and audience in aud:
		return True
	# Keycloak client-credentials tokens often put the client id in azp with aud=account.
	return claims.get("azp") == audience


def _has_charts_role(claims: dict[str, Any], client_id: str, role: str) -> bool:
	roles = ((claims.get("resource_access") or {}).get(client_id) or {}).get("roles") or []
	return isinstance(roles, list) and role in roles


def _jwks_client(url: str) -> PyJWKClient:
	client = _JWKS_CLIENTS.get(url)
	if client is None:
		client = PyJWKClient(url, cache_keys=True)
		_JWKS_CLIENTS[url] = client
	return client


def verify_charts_token(token: str) -> dict[str, Any]:
	"""Decode and validate a charts Bearer JWT. Raises AuthenticationError / PermissionError."""
	cfg = _charts_config()
	if not cfg["issuer"] or not cfg["audience"] or not cfg["jwks_url"]:
		err = frappe.ValidationError(_("Charts auth is misconfigured (issuer/audience)."))
		err.http_status_code = 500
		raise err

	try:
		signing_key = _jwks_client(cfg["jwks_url"]).get_signing_key_from_jwt(token)
		claims = jwt.decode(
			token,
			signing_key.key,
			algorithms=["RS256"],
			issuer=cfg["issuer"],
			options={"require": ["exp", "iss"], "verify_aud": False},
		)
	except jwt.PyJWTError as exc:
		frappe.throw(_("Invalid or expired charts token: {0}").format(exc), frappe.AuthenticationError)

	if not _audience_matches(claims, cfg["audience"]):
		frappe.throw(_("Charts token audience is not accepted."), frappe.AuthenticationError)

	if not _has_charts_role(claims, cfg["audience"], cfg["role"]):
		frappe.throw(
			_("Charts token is missing role {0}.").format(cfg["role"]),
			frappe.PermissionError,
		)

	return claims


def require_charts_read() -> dict[str, Any] | None:
	"""Enforce charts:read when auth is enabled. Returns claims, or None when auth is off."""
	if not charts_auth_enabled():
		return None
	token = _bearer_token()
	if not token:
		frappe.throw(_("Missing bearer token."), frappe.AuthenticationError)
	return verify_charts_token(token)
