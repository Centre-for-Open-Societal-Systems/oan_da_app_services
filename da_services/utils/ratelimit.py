"""Per-user, per-endpoint request limit for the v1 API.

Frappe's own ``rate_limit`` keys on the client IP and on ``form_dict.cmd``; our
endpoints are Werkzeug routes (no ``cmd``) and DAs sit behind shared mobile NAT, so
that would throttle a whole Woreda as one client and lump every endpoint into one
bucket. This one counts per signed-in user and per function, in Redis, and is a no-op
outside an HTTP request (tests, bench execute).
"""

from __future__ import annotations

from functools import wraps

import frappe
from frappe import _


def rate_limited(limit: int, seconds: int):
	"""Allow ``limit`` calls per ``seconds`` for the current user on the wrapped function.

	Raises ``frappe.RateLimitExceededError`` (Frappe answers 429) when exceeded. Put it
	outside ``handle_api_errors`` so the 429 reaches the client as such.
	"""

	def decorator(fn):
		bucket = f"rl:da:{fn.__module__}.{fn.__name__}"

		@wraps(fn)
		def wrapper(*args, **kwargs):
			if getattr(frappe.local, "request", None) is None:
				return fn(*args, **kwargs)
			who = frappe.session.user if frappe.session and frappe.session.user != "Guest" else None
			who = who or getattr(frappe.local, "request_ip", None) or "anonymous"
			key = frappe.cache.make_key(f"{bucket}:{who}:{seconds}")
			count = frappe.cache.incrby(key, 1)
			if count == 1:
				frappe.cache.expire(key, seconds)
			if count > limit:
				frappe.throw(
					_("Too many requests; try again in a moment."),
					frappe.RateLimitExceededError,
				)
			return fn(*args, **kwargs)

		return wrapper

	return decorator
