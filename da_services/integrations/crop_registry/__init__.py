"""Crop Sown Registry read client.

Boundary to the Crop Sown Staff HTTP API. Staff host and register id come from
DA Services Settings.
"""

from da_services.integrations.staff_registry import (
	load_registry_module_config,
	master_data_url_from_settings,
)

from .client import (
	CropRegistryClient,
	CropRegistryClientError,
	CropRegistryError,
	CropRegistryServerError,
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
	staff_url, register_id = load_registry_module_config(
		domain_field="crop_registry_domain",
		register_field="crop_registry_form_register_id",
		domain_label="Crop Registry Domain",
		register_label="Crop Registry Form Register ID",
	)
	return HTTPCropRegistryClient(
		staff_url=staff_url,
		register_id=register_id,
		master_data_url=master_data_url_from_settings(),
	)
