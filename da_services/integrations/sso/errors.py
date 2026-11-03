"""Errors raised by the SSO login."""


class SSOLoginError(Exception):
	"""``kind`` is one of invalid_key, invalid_credentials, rejected, unavailable, upstream."""

	def __init__(self, message: str, *, kind: str = "upstream"):
		super().__init__(message)
		self.kind = kind
