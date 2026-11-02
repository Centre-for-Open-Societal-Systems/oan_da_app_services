"""Crop Sown Registry read client.

Boundary to the Crop Sown Staff API. The mock is the default. The HTTP client
follows the verified login, discovery, and intake-submission read sequence.
"""

from .client import CropRegistryClient, get_crop_registry_client

__all__ = ["CropRegistryClient", "get_crop_registry_client"]
