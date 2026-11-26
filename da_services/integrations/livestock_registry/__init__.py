"""Livestock Registry client.

Boundary to the Livestock staff API. Staff host and register id come from
DA Services Settings.
"""

from da_services.integrations.staff_registry import (
	load_registry_module_config,
	master_data_url_from_settings,
)

from .client import (
	LivestockRegistryClient,
	LivestockRegistryClientError,
	LivestockRegistryError,
	LivestockRegistryServerError,
)
from .http import HTTPLivestockRegistryClient

__all__ = [
	"LivestockRegistryClient",
	"LivestockRegistryClientError",
	"LivestockRegistryError",
	"LivestockRegistryServerError",
	"get_livestock_registry_client",
]


def get_livestock_registry_client() -> LivestockRegistryClient:
	"""Return the Livestock HTTP client (Settings domain + register id)."""
	staff_url, register_id = load_registry_module_config(
		domain_field="livestock_registry_domain",
		register_field="livestock_registry_form_register_id",
		domain_label="Livestock Registry Domain",
		register_label="Livestock Registry Form Register ID",
	)
	return HTTPLivestockRegistryClient(
		staff_url=staff_url,
		register_id=register_id,
		master_data_url=master_data_url_from_settings(),
	)
