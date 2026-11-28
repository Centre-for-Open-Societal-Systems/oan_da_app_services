"""Farmer Registry reads (register search, a farmer's record and tab records), called with
the signed-in user's bearer token and CSRF token."""

from .client import (
	DEFAULT_PAGE_SIZE,
	LAND_TAB_ID,
	MAX_PAGE_SIZE,
	FarmerRegistryClient,
	client_from_settings,
)
from .errors import FarmerRegistryError

__all__ = [
	"DEFAULT_PAGE_SIZE",
	"LAND_TAB_ID",
	"MAX_PAGE_SIZE",
	"FarmerRegistryClient",
	"FarmerRegistryError",
	"client_from_settings",
]
