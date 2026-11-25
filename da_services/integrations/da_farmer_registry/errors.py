"""Errors raised by the DA Farmer Registry client."""


class FarmerRegistryError(Exception):
	"""``kind`` is one of config, unauthorized, unavailable, rejected."""

	def __init__(self, message: str, *, kind: str = "rejected"):
		super().__init__(message)
		self.kind = kind
