"""Livestock Registry client.

Boundary to the Livestock staff API. The mock is the default. The HTTP client
reads the live intake form and saves a farmer's livestock through it.
"""

from .auth import livestock_auth_headers
from .client import LivestockRegistryClient, get_livestock_registry_client

__all__ = ["LivestockRegistryClient", "get_livestock_registry_client", "livestock_auth_headers"]
