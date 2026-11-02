"""Shared OpenG2P staff protocol for Farmer, Crop Sown, and Livestock.

Authentication, CSRF, token caching, the request envelope, and HTTP error
classification live here. Each registry keeps its own URLs, mnemonics, and
field mapping.
"""

from .auth import StaffAuthError, auth_and_csrf_headers
from .transport import http_failure, staff_envelope

__all__ = [
	"StaffAuthError",
	"auth_and_csrf_headers",
	"http_failure",
	"staff_envelope",
]
