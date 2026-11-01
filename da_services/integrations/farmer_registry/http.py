"""HTTP client for the live Farmer Registry (OpenG2P Staff API).

Every call is a POST with the OpenG2P staff envelope and the double-submit
CSRF pair the staff middleware requires. Auth order is staff-user IAM login
(when username and password are set), then Keycloak client-credentials, then
a static API key. Reads come from ``/register-data/*``, updates become change
requests, and registration goes through the ingest pipeline.

Copy keys from ``config.example.json`` into site_config.json. The client does
not read the ``_environments`` examples in that file.

Staff API endpoints used:
    register-data/get_subject_record        -> get_farmer (identity row)
    register-data/get_tab_records           -> get_farmer (farmer/location/household tabs), get_farmer_plots (land tab)
    register-data/search_in_a_register      -> list_farmers (filter_by on whitelisted *_name fields)
    change-requests/create_change_request   -> update_farmer (one change request per section)
    input-mechanism-data/ingest-data        -> register_farmer
    ingestion-data/search_in_ingestion_data -> get_ingest_status
"""

import base64
import hashlib
import json
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import ClassVar
from urllib.parse import urljoin

import frappe
import requests
from frappe import _

from .client import (
	FarmerRegistryClient,
	FarmerRegistryClientError,
	FarmerRegistryError,
	FarmerRegistryServerError,
)
from .mapper import (
	land_record_to_plot,
	merge_tab_records,
	registry_record_to_farmer,
	search_row_to_list_item,
	updates_to_change_requests,
)
from .schemas import Farmer, FarmerListItem, Plot

# Farmer register tabs on the registry; overridable through farmer_registry_ingest.tabs
DEFAULT_TABS: dict[str, str] = {
	"farmer": "farmer_farmer_tab",
	"location": "farmer_location_tab",
	"household": "farmer_household_link_tab",
	"land": "farmer_land_tab",
	"crop": "farmer_crop_tab",
	"livestock": "farmer_livestock_tab",
}

# Registry messages that mean "no such record" rather than a failure.
# Codes such as REGISTER_DATA_NOT_FOUND use underscores, so they are normalized
# before the phrase check.
_NOT_FOUND_MARKERS = ("not found", "does not exist", "no record")


def _looks_like_missing_record(*parts: str | None) -> bool:
	text = " ".join(part.replace("_", " ").replace("-", " ") for part in parts if part).lower()
	return any(marker in text for marker in _NOT_FOUND_MARKERS)


# A new client is built per call, so tokens live on the worker, not the instance.
_STAFF_TOKEN_CACHE: dict[tuple[str, str], dict] = {}
_STAFF_TOKEN_LOCK = threading.Lock()
_CC_TOKEN_CACHE: dict[tuple[str, str], dict] = {}
_CC_TOKEN_LOCK = threading.Lock()
_TOKEN_SKEW_SECONDS = 30
_CALLBACK_COOKIE_MAX_AGE = 2100

_LOGIN_FORM_RE = re.compile(r'<form\b[^>]*\bid=["\']kc-form-login["\'][^>]*>', re.IGNORECASE)
_FORM_ACTION_RE = re.compile(r'\baction=["\']([^"\']+)["\']', re.IGNORECASE)
_MAX_AGE_RE = re.compile(r"Max-Age=(\d+)", re.IGNORECASE)


def _set_cookie_chunks(response: requests.Response) -> list[str]:
	raw_headers = getattr(getattr(response, "raw", None), "headers", None)
	if raw_headers is not None and hasattr(raw_headers, "getlist"):
		return [chunk for chunk in raw_headers.getlist("Set-Cookie") if chunk]
	single = response.headers.get("Set-Cookie")
	return [single] if single else []


def _cookie_value(response: requests.Response, name: str) -> str | None:
	"""Last Set-Cookie value for name. Also checks the parsed cookie jar."""
	pattern = re.compile(rf"^{re.escape(name)}=([^;]*)", re.IGNORECASE)
	found = []
	for chunk in _set_cookie_chunks(response):
		match = pattern.match(chunk)
		if match:
			found.append(match.group(1).strip().strip('"'))
	if found and found[-1]:
		return found[-1]
	cookie = response.cookies.get(name)
	if cookie:
		value = str(cookie).strip().strip('"')
		return value or None
	return None


def _cookie_max_age(response: requests.Response, name: str) -> int | None:
	pattern = re.compile(rf"^{re.escape(name)}=", re.IGNORECASE)
	for chunk in _set_cookie_chunks(response):
		if pattern.match(chunk):
			match = _MAX_AGE_RE.search(chunk)
			if match:
				return int(match.group(1))
	return None


def _positive_timeout(value: object, default: float = 30) -> float:
	if isinstance(value, bool) or not isinstance(value, (int, float, str)):
		return default
	try:
		timeout = float(value)
	except ValueError:
		return default
	return timeout if timeout > 0 else default


def _token_still_valid(entry: dict, *, require_csrf: bool = False) -> bool:
	if not entry.get("token") or entry.get("expires_at", 0) <= time.time() + _TOKEN_SKEW_SECONDS:
		return False
	if require_csrf and not entry.get("csrf"):
		return False
	return True


def _jwt_expires_at(token: str) -> float | None:
	"""Read exp. The Staff API checks the signature."""
	try:
		payload = token.split(".")[1]
		payload += "=" * (-len(payload) % 4)
		claims = json.loads(base64.urlsafe_b64decode(payload))
		return float(claims["exp"])
	except IndexError, KeyError, ValueError, json.JSONDecodeError:
		return None


def _keycloak_login_action(html: str, realm_url: str) -> str:
	"""Pull the credential form action out of the Keycloak login page."""
	form = _LOGIN_FORM_RE.search(html or "")
	if not form:
		raise FarmerRegistryClientError("Keycloak did not return the staff login form")
	action = _FORM_ACTION_RE.search(form.group(0))
	url = action.group(1).replace("&amp;", "&") if action else ""
	expected = realm_url.rstrip("/") + "/login-actions/"
	if not url.startswith(expected):
		raise FarmerRegistryClientError("Keycloak login form does not post to the configured staff realm")
	return url


@dataclass
class StaffResponse:
	"""Parsed OpenG2P staff envelope."""

	status: str
	error_code: str | None
	error_message: str | None
	payload: object
	pagination: dict = field(default_factory=dict)

	@property
	def ok(self) -> bool:
		return self.status == "SUCCESS"

	@property
	def not_found(self) -> bool:
		return (not self.ok) and _looks_like_missing_record(self.error_message, self.error_code)


class HTTPFarmerRegistryClient(FarmerRegistryClient):
	"""Live Farmer Registry client over the OpenG2P Staff API."""

	REGISTER_MNEMONIC: ClassVar[str] = "Farmer"

	def __init__(self):
		self.base_url = str(frappe.conf.get("farmer_registry_url", "") or "").rstrip("/")
		self.api_key = frappe.conf.get("farmer_registry_api_key")
		self.timeout = _positive_timeout(frappe.conf.get("farmer_registry_timeout"))

		self.headers = {"Content-Type": "application/json", "Accept": "application/json"}
		# Static API key. Staff IAM or client-credentials replace this per request.
		if self.api_key:
			self.headers["Authorization"] = f"Bearer {self.api_key}"

		# Register / intake / tab identifiers for the Farmer register.
		ingest = frappe.conf.get("farmer_registry_ingest", {}) or {}
		self.ingest_data_model = ingest.get("data_model", "FARMER_ODK_MODEL")
		self.ingest_register_id = ingest.get("register_id")
		self.ingest_intake_form_id = ingest.get("intake_form_id")
		self.register_id = ingest.get("register_id")
		self.tabs = {**DEFAULT_TABS, **(ingest.get("tabs") or {})}

		# Staff IAM login and/or Keycloak client-credentials. Tokens are cached
		# on the worker; see _STAFF_TOKEN_CACHE and _CC_TOKEN_CACHE.
		self.keycloak = frappe.conf.get("farmer_registry_keycloak", {}) or {}

		# Fail fast: a missing or partial config should surface when the client
		# is built, not on the first request from the field.
		self._validate_config()

	# ------------------------------------------------------------------ config

	def _validate_config(self) -> None:
		"""Raise FarmerRegistryError if the live client cannot possibly work."""
		problems = []
		if not self.base_url.startswith(("http://", "https://")):
			problems.append("farmer_registry_url must be an http(s) URL")

		iam_keys = ("iam_base_url", "kc_realm_url", "username", "password")
		cc_keys = ("token_url", "client_id", "client_secret")
		iam_set = [k for k in iam_keys if self.keycloak.get(k)]
		cc_set = [k for k in cc_keys if self.keycloak.get(k)]
		iam_complete = len(iam_set) == len(iam_keys)
		cc_complete = len(cc_set) == len(cc_keys)
		if cc_set and not cc_complete:
			problems.append(
				"farmer_registry_keycloak needs token_url, client_id and client_secret "
				f"(found only: {', '.join(cc_set)})"
			)
		# Realm URLs without a username are optional alongside a service account.
		# A started staff login (username or password present) must be complete.
		staff_login_started = any(self.keycloak.get(k) for k in ("username", "password"))
		if iam_set and not iam_complete and (staff_login_started or not (cc_complete or self.api_key)):
			problems.append(
				"farmer_registry_keycloak staff login needs iam_base_url, kc_realm_url, username and password "
				f"(found only: {', '.join(iam_set)})"
			)
		if not iam_complete and not cc_complete and not self.api_key:
			problems.append(
				"set farmer_registry_keycloak (staff login or service account) or farmer_registry_api_key"
			)

		if not self.register_id:
			problems.append("farmer_registry_ingest.register_id (Farmer register id) is required")
		if not self.ingest_data_model:
			problems.append("farmer_registry_ingest.data_model is required")

		if problems:
			raise FarmerRegistryError("Farmer Registry HTTP client misconfigured: " + "; ".join(problems))

	# ------------------------------------------------------------------ auth

	def _staff_session(self) -> tuple[str | None, str | None]:
		"""Return (access token, csrf) from the IAM callback, cached per worker.

		Returns (None, None) when staff login is not configured. The caller then
		uses client-credentials or ``farmer_registry_api_key`` and a generated CSRF pair.
		"""
		kc = self.keycloak or {}
		iam = str(kc.get("iam_base_url") or "").rstrip("/")
		realm = str(kc.get("kc_realm_url") or "").rstrip("/")
		username = kc.get("username")
		password = kc.get("password")
		if not (iam and realm and username and password):
			return None, None

		cache_key = (iam, str(username))

		cached = _STAFF_TOKEN_CACHE.get(cache_key) or {}
		if _token_still_valid(cached, require_csrf=True):
			return cached["token"], cached["csrf"]

		with _STAFF_TOKEN_LOCK:
			cached = _STAFF_TOKEN_CACHE.get(cache_key) or {}
			if _token_still_valid(cached, require_csrf=True):
				return cached["token"], cached["csrf"]

			token, csrf, expires_at = self._login_via_iam(
				iam,
				realm,
				str(username),
				str(password),
				str(kc.get("login_provider_id") or "1"),
			)
			_STAFF_TOKEN_CACHE[cache_key] = {"token": token, "csrf": csrf, "expires_at": expires_at}
			return token, csrf

	def _login_via_iam(
		self, iam: str, realm: str, username: str, password: str, provider_id: str
	) -> tuple[str, str, float]:
		"""Run the staff login and return (X-Access-Token, X-CSRF-Token, expires_at).

		01 POST {iam}/auth/start_authentication_transaction -> authorization URL
		02 GET  that URL -> Keycloak login form action
		03 POST the form (no redirect) -> location is the IAM callback URL
		04 GET  the callback (no redirect) -> Set-Cookie X-Access-Token and X-CSRF-Token

		The authorization code in the callback URL is not logged.
		"""
		session = requests.Session()
		timeout = self.timeout
		try:
			return self._exchange_staff_login(session, iam, realm, username, password, provider_id, timeout)
		finally:
			session.close()

	def _exchange_staff_login(
		self,
		session: requests.Session,
		iam: str,
		realm: str,
		username: str,
		password: str,
		provider_id: str,
		timeout: float,
	) -> tuple[str, str, float]:
		try:
			started = session.post(
				f"{iam}/auth/start_authentication_transaction",
				params={"id": provider_id, "redirect_uri": "/en"},
				timeout=timeout,
			)
		except requests.RequestException as e:
			frappe.log_error(title="Farmer Registry Auth Error", message=f"IAM login start failed: {e!s}")
			raise FarmerRegistryServerError("Could not reach IAM to start staff login") from e

		if started.status_code != 200:
			raise FarmerRegistryClientError(
				f"IAM did not start staff login (HTTP {started.status_code})",
				status_code=started.status_code,
			)
		try:
			body = started.json()
		except ValueError as e:
			raise FarmerRegistryServerError("IAM login response was not JSON") from e
		redirect_url = body.get("redirectUrl") if isinstance(body, dict) else ""

		if not str(redirect_url).startswith(realm + "/protocol/openid-connect/auth?"):
			raise FarmerRegistryClientError("IAM authorization URL is not on the configured staff realm")

		try:
			form_page = session.get(redirect_url + "&prompt=login", timeout=timeout)
		except requests.RequestException as e:
			frappe.log_error(title="Farmer Registry Auth Error", message=f"Keycloak login page failed: {e!s}")
			raise FarmerRegistryServerError("Could not open the Keycloak staff login page") from e

		if form_page.status_code != 200:
			raise FarmerRegistryClientError(
				f"Keycloak login page failed (HTTP {form_page.status_code})",
				status_code=form_page.status_code,
			)

		try:
			signed_in = session.post(
				_keycloak_login_action(form_page.text, realm),
				data={"username": username, "password": password, "credentialId": ""},
				timeout=timeout,
				allow_redirects=False,
			)
		except requests.RequestException as e:
			frappe.log_error(title="Farmer Registry Auth Error", message=f"Keycloak sign-in failed: {e!s}")
			raise FarmerRegistryServerError("Could not submit the staff username and password") from e

		if signed_in.status_code not in (302, 303):
			raise FarmerRegistryClientError(
				f"Keycloak did not accept the staff user (HTTP {signed_in.status_code})",
				status_code=signed_in.status_code,
			)

		callback = urljoin(signed_in.url, signed_in.headers.get("Location") or "")
		if not callback.startswith(iam + "/auth/callback?"):
			raise FarmerRegistryClientError("Keycloak did not redirect back to the IAM callback")

		try:
			exchanged = session.get(callback, timeout=timeout, allow_redirects=False)
		except requests.RequestException as e:
			frappe.log_error(title="Farmer Registry Auth Error", message=f"IAM callback failed: {e!s}")
			raise FarmerRegistryServerError("Could not complete the IAM callback") from e

		if exchanged.status_code not in (302, 303, 307):
			raise FarmerRegistryClientError(
				f"IAM callback did not complete (HTTP {exchanged.status_code})",
				status_code=exchanged.status_code,
			)

		token = _cookie_value(exchanged, "X-Access-Token")
		csrf = _cookie_value(exchanged, "X-CSRF-Token")
		if not token:
			raise FarmerRegistryServerError("IAM callback did not set an X-Access-Token cookie")
		if not csrf:
			raise FarmerRegistryServerError("IAM callback did not set an X-CSRF-Token cookie")

		expires_at = _jwt_expires_at(token)
		if expires_at is None:
			max_age = _cookie_max_age(exchanged, "X-Access-Token") or _cookie_max_age(
				exchanged, "X-CSRF-Token"
			)
			expires_at = time.time() + (max_age if max_age is not None else _CALLBACK_COOKIE_MAX_AGE)
		return token, csrf, expires_at

	def _get_access_token(self) -> str | None:
		"""Return a bearer token for the Staff API.

		Used when staff IAM login is not configured. The token is cached on the
		worker until near expiry, including across new client instances. A
		static ``farmer_registry_api_key`` stays on self.headers when this
		returns None.
		"""
		kc = self.keycloak
		token_url = kc.get("token_url")
		client_id = kc.get("client_id")
		client_secret = kc.get("client_secret")
		if not (token_url and client_id and client_secret):
			return None

		cache_key = (str(token_url), str(client_id))
		secret_mark = hashlib.sha256(str(client_secret).encode()).hexdigest()
		cached = _CC_TOKEN_CACHE.get(cache_key) or {}
		if cached.get("secret_mark") == secret_mark and _token_still_valid(cached):
			return cached["token"]

		with _CC_TOKEN_LOCK:
			cached = _CC_TOKEN_CACHE.get(cache_key) or {}
			if cached.get("secret_mark") == secret_mark and _token_still_valid(cached):
				return cached["token"]

			try:
				resp = requests.post(
					token_url,
					data={
						"grant_type": "client_credentials",
						"client_id": client_id,
						"client_secret": client_secret,
					},
					timeout=self.timeout,
				)
			except requests.RequestException as e:
				frappe.log_error(
					title="Farmer Registry Auth Error",
					message=f"Keycloak unreachable at {token_url}: {e!s}",
				)
				raise FarmerRegistryServerError("Could not reach Keycloak for a registry token") from e

			if resp.status_code >= 400:
				# Do not log the body: a token endpoint can echo the client or the token.
				frappe.log_error(
					title="Farmer Registry Auth Error",
					message=f"Keycloak token request failed: HTTP {resp.status_code} {token_url}",
				)
				exc = FarmerRegistryClientError if resp.status_code < 500 else FarmerRegistryServerError
				raise exc(
					f"Keycloak refused the token request (HTTP {resp.status_code})",
					status_code=resp.status_code,
				)

			try:
				data = resp.json()
			except ValueError as e:
				raise FarmerRegistryServerError("Keycloak token response was not JSON") from e
			token = data.get("access_token") if isinstance(data, dict) else None
			if not token:
				raise FarmerRegistryServerError("Keycloak response had no access_token")

			try:
				expires_in = int(data.get("expires_in", 300))
			except TypeError, ValueError:
				expires_in = 300
			_CC_TOKEN_CACHE[cache_key] = {
				"token": token,
				"secret_mark": secret_mark,
				"expires_at": time.time() + max(expires_in, 0),
			}
			return token

	def _auth_and_csrf_headers(self) -> tuple[dict, dict]:
		"""Headers + cookies for a state-changing (POST) staff call.

		Staff-user IAM login supplies the callback token and CSRF cookie.
		Otherwise a client-credentials token is used with a generated CSRF pair.
		A static API key already on self.headers is the last fallback.
		"""
		headers = dict(self.headers)
		token, csrf = self._staff_session()
		if not token:
			token = self._get_access_token()
			csrf = None
		if token:
			headers["Authorization"] = f"Bearer {token}"

		if not csrf:
			csrf = secrets.token_urlsafe(24)
		headers["X-CSRF-Token"] = csrf
		cookies = {"X-CSRF-Token": csrf}
		return headers, cookies

	# ------------------------------------------------------------------ transport

	@staticmethod
	def _raise_for_status(response: requests.Response, action: str) -> None:
		"""Map HTTP errors to retryable vs non-retryable registry exceptions.

		4xx: the request is wrong (auth, roles, validation) — retrying the same
		call cannot succeed. 5xx: the registry is unhealthy — safe to retry.
		"""
		code = response.status_code
		if code < 400:
			return

		detail = ""
		try:
			detail = response.json().get("response_header", {}).get("response_error_message") or ""
		except ValueError:
			pass
		snippet = detail or (response.text or "")[:300]

		if code < 500:
			frappe.log_error(
				title="Farmer Registry rejected request",
				message=f"{action}: HTTP {code} {response.url}\n{snippet}",
			)
			raise FarmerRegistryClientError(
				f"Farmer Registry rejected {action} (HTTP {code}): {snippet}",
				status_code=code,
				body=snippet,
			)

		frappe.log_error(
			title="Farmer Registry server error",
			message=f"{action}: HTTP {code} {response.url}\n{snippet}",
		)
		raise FarmerRegistryServerError(
			f"Farmer Registry failed during {action} (HTTP {code})", status_code=code
		)

	@staticmethod
	def _staff_envelope(
		request_payload: dict,
		*,
		page: int = 1,
		page_size: int = 50,
		filter_by: dict | None = None,
		search_text: str = "",
		sort_by: str = "",
	) -> dict:
		"""Wrap a payload in the OpenG2P staff request envelope."""
		return {
			"request_header": {
				"sender_app_mnemonic": "DA_SERVICES",
				"sender_app_url": "",
				"request_id": str(uuid.uuid4()),
				"request_timestamp": frappe.utils.now(),
				"instance_id": "",
			},
			"request_body": {
				"request_payload": request_payload,
				"pagination_request": {
					"current_page": page,
					"page_size": page_size,
					"sort_by": sort_by,
					"filter_by": filter_by or "",
					"search_text": search_text,
				},
			},
		}

	def _staff_post(self, path: str, request_payload: dict, *, action: str, **pagination) -> StaffResponse:
		"""POST an enveloped request to the Staff API and parse the envelope back.

		Raises FarmerRegistryServerError on network failure, and the 4xx/5xx
		mapping from _raise_for_status. A 200 with response_status ERROR is
		returned as a StaffResponse so callers can treat "not found" specially.
		"""
		url = f"{self.base_url}{path}"
		headers, cookies = self._auth_and_csrf_headers()
		try:
			response = requests.post(
				url,
				headers=headers,
				cookies=cookies,
				json=self._staff_envelope(request_payload, **pagination),
				timeout=self.timeout,
			)
		except requests.RequestException as e:
			frappe.log_error(
				title="Farmer Registry HTTP Error", message=f"Could not reach {url} ({action}): {e!s}"
			)
			raise FarmerRegistryServerError(f"Farmer Registry unreachable: {e!s}") from e

		self._raise_for_status(response, action)
		try:
			data = response.json()
		except ValueError as e:
			raise FarmerRegistryServerError(f"Farmer Registry returned non-JSON for {action}") from e

		header = data.get("response_header") or {}
		body = data.get("response_body") or {}
		return StaffResponse(
			status=header.get("response_status") or "ERROR",
			error_code=header.get("response_error_code"),
			error_message=header.get("response_error_message"),
			payload=body.get("response_payload"),
			pagination=body.get("pagination_response") or {},
		)

	@staticmethod
	def _reject(res: StaffResponse, action: str) -> None:
		"""Turn a response_status=ERROR envelope into a client error."""
		raise FarmerRegistryClientError(
			f"Farmer Registry rejected {action}: {res.error_message or res.error_code or 'unknown error'}",
			status_code=200,
			body=res.error_code,
		)

	# ------------------------------------------------------------------ helpers

	@staticmethod
	def _area_name(code_or_name: str | None) -> str | None:
		"""Registry filters use administrative *names*; translate a DA code when we know it."""
		if not code_or_name:
			return code_or_name
		try:
			name = frappe.db.get_value("DA Administrative Area", {"code": code_or_name}, "name_en")
		except Exception:
			# A lookup failure must not turn a farmer search into a 500. The
			# registry can still filter on the code the caller sent.
			frappe.logger("da_services").warning(
				"Could not resolve administrative area %s to a name", code_or_name, exc_info=True
			)
			name = None
		return name or code_or_name

	def _tab_records(self, farmer_id: str, tab_key: str, *, action: str) -> list:
		"""Rows of one register tab for a farmer (list of RegisterTabRecordData dicts)."""
		tab_id = self.tabs.get(tab_key)
		if not tab_id:
			return []
		res = self._staff_post(
			"/register-data/get_tab_records",
			{"subject_register_id": self.register_id, "subject_record_id": farmer_id, "tab_id": tab_id},
			action=action,
		)
		if not res.ok:
			if res.not_found:
				return []
			self._reject(res, action)
		return list(res.payload or [])

	# ------------------------------------------------------------------ reads

	def get_farmer(self, farmer_id: str) -> Farmer | None:
		"""Farmer identity row plus the farmer / location / household tabs.

		get_subject_record returns the register row (flattened fields);
		birth, socio-economic, phone, location and household data live in tab
		sections, so those tabs are merged in (best effort: a tab failure is
		logged and skipped, the identity row still comes back).
		"""
		res = self._staff_post(
			"/register-data/get_subject_record",
			{"subject_register_id": self.register_id, "subject_record_id": farmer_id},
			action="get farmer",
		)
		if not res.ok:
			if res.not_found:
				return None
			self._reject(res, "get farmer")

		record = dict(res.payload or {})
		incomplete: list[str] = []
		for key in ("farmer", "location", "household"):
			try:
				merge_tab_records(record, self._tab_records(farmer_id, key, action=f"get farmer {key} tab"))
			except FarmerRegistryError as e:
				incomplete.append(key)
				frappe.log_error(
					title="Farmer Registry tab fetch failed",
					message=f"{farmer_id} {key} tab: {e!s}",
				)
		farmer = registry_record_to_farmer(record, farmer_id)
		# Say which sections are missing instead of returning a record that looks
		# like "no phone / no household" when the registry simply failed mid-read.
		farmer.incomplete_sections = incomplete
		return farmer

	def list_farmers(
		self,
		*,
		woreda: str | None = None,
		kebele: str | None = None,
		state: str | None = None,
		limit: int = 50,
		offset: int = 0,
	) -> tuple[list[FarmerListItem], int]:
		"""Search the Farmer register.

		The registry filters on whitelisted *_name fields, so DA codes are
		translated to names through DA Administrative Area. kebele_name is not
		yet whitelisted on the registry side, so Kebele is also applied here on
		the returned rows.
		"""
		limit = max(1, int(limit or 50))
		offset = max(0, int(offset or 0))

		filter_by: dict = {}
		woreda_name = self._area_name(woreda)
		kebele_name = self._area_name(kebele)
		if woreda_name:
			filter_by["woreda_name"] = woreda_name
		if kebele_name:
			filter_by["kebele_name"] = kebele_name
		if state:
			filter_by["state"] = state

		res = self._staff_post(
			"/register-data/search_in_a_register",
			{"register_id": self.register_id},
			action="list farmers",
			page=offset // limit + 1,
			page_size=limit,
			filter_by=filter_by or None,
		)
		if not res.ok:
			self._reject(res, "list farmers")

		rows = list(res.payload or [])
		items = [search_row_to_list_item(row) for row in rows]
		if kebele_name:
			narrowed = [i for i in items if not i.kebele or i.kebele == kebele_name]
			# Only trust the client-side narrowing when the rows carry a kebele.
			if any(i.kebele for i in items):
				items = narrowed

		total = res.pagination.get("number_of_items")
		return items, int(total) if total is not None else len(items)

	def get_farmer_plots(self, farmer_id: str) -> list[Plot]:
		"""Land Details rows for a farmer (Lands tab)."""
		plots: list[Plot] = []
		for section in self._tab_records(farmer_id, "land", action="get farmer plots"):
			for rec in section.get("records") or []:
				plots.append(land_record_to_plot(rec))
		return plots

	# ------------------------------------------------------------------ writes

	def update_farmer(self, farmer_id: str, updates: dict, expected_version: int | None = None) -> dict:
		"""Edit a farmer through registry change requests (DA-41).

		The registry has no direct PATCH: each editable section gets a change
		request that a Woreda officer approves. Fields the registry cannot take
		through this route are rejected up front (non-retryable) so the queue
		records a clear reason instead of silently dropping them.
		"""
		groups, unsupported = updates_to_change_requests(updates)
		if unsupported:
			raise FarmerRegistryClientError(
				"Fields not editable through the Farmer Registry change request: " + ", ".join(unsupported),
				status_code=400,
			)
		if not groups:
			raise FarmerRegistryClientError("No registry-backed fields to update", status_code=400)

		change_request_ids: list[str] = []
		for group in groups:
			res = self._staff_post(
				"/change-requests/create_change_request",
				{
					"register_id": self.register_id,
					"register_mnemonic": self.REGISTER_MNEMONIC,
					"tab_id": self.tabs.get(group["tab"]),
					"section_id": group["section_id"],
					"section_register_id": self.register_id,
					"internal_record_id": farmer_id,
					"change_payload": group["payload"],
				},
				action="update farmer",
			)
			if not res.ok:
				self._reject(res, "update farmer")
			payload = res.payload if isinstance(res.payload, dict) else {}
			cr_id = payload.get("change_request_id")
			if cr_id:
				change_request_ids.append(str(cr_id))

		return {
			"success": True,
			"conflict": False,
			"farmer_id": farmer_id,
			"version": ",".join(change_request_ids) or None,
			"change_request_ids": change_request_ids,
			"message": _("Change request created; pending approval in the Farmer Registry"),
		}

	def register_farmer(self, payload: dict) -> dict:
		"""Register a new farmer via the Staff API ingest-data endpoint.

		POST /input-mechanism-data/ingest-data?data_model&register_id&intake_form_id
		Body is the ingest envelope (header + message.payload) built by
		mapper.da_form_to_ingest_payload(). Async: returns a correlation_id.
		"""
		if not isinstance(payload, dict) or not payload.get("message"):
			raise FarmerRegistryError("Invalid ingest payload: missing 'message'")

		url = f"{self.base_url}/input-mechanism-data/ingest-data"
		params = {"data_model": self.ingest_data_model}
		if self.ingest_register_id:
			params["register_id"] = self.ingest_register_id
		if self.ingest_intake_form_id:
			params["intake_form_id"] = self.ingest_intake_form_id

		headers, cookies = self._auth_and_csrf_headers()

		try:
			response = requests.post(
				url,
				headers=headers,
				cookies=cookies,
				params=params,
				json=payload,
				timeout=self.timeout,
			)
		except requests.RequestException as e:
			# Network-level failure (timeout, DNS, connection): transient, retryable.
			frappe.log_error(
				title="Farmer Registry HTTP Error",
				message=f"Could not reach {url} to register farmer: {e!s}",
			)
			raise FarmerRegistryServerError(f"Farmer Registry unreachable: {e!s}") from e

		self._raise_for_status(response, "register farmer")
		data = response.json()

		header = data.get("response_header", {}) or {}
		body = data.get("response_body", {}) or {}
		status = header.get("response_status")
		correlation_id = (body.get("response_payload") or {}).get("correlation_id")
		return {
			"success": status == "SUCCESS",
			"correlation_id": correlation_id,
			"status": status,
			"error_code": header.get("response_error_code"),
			"error_message": header.get("response_error_message"),
		}

	def get_ingest_status(self, correlation_id: str) -> list[dict]:
		"""Search ingestion rows for a correlation_id."""
		res = self._staff_post(
			"/ingestion-data/search_in_ingestion_data",
			{},
			action="get ingest status",
			page_size=50,
			search_text=correlation_id,
		)
		if not res.ok:
			if res.not_found:
				return []
			self._reject(res, "get ingest status")
		payload = res.payload
		if isinstance(payload, list):
			return payload
		return [payload] if payload else []
