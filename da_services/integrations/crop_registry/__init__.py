"""Crop Sown Registry read client.

Boundary to the Crop Sown Staff HTTP API. Staff host and register id come from
DA Services Settings.
"""

from .client import (
	CropRegistryClient,
	CropRegistryClientError,
	CropRegistryError,
	CropRegistryServerError,
	_master_data_url,
	_staff_url_and_register_id,
)
from .http import HTTPCropRegistryClient

__all__ = [
	"CropRegistryClient",
	"CropRegistryClientError",
	"CropRegistryError",
	"CropRegistryServerError",
	"get_crop_registry_client",
]


def get_crop_registry_client() -> CropRegistryClient:
	"""Return the Crop Sown HTTP client (Settings domain + register id)."""
	staff_url, register_id = _staff_url_and_register_id()
	return HTTPCropRegistryClient(
		staff_url=staff_url,
		register_id=register_id,
		master_data_url=_master_data_url(),
	)
