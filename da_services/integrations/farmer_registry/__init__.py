"""Farmer Registry integration client.

Boundary to the Farmer Registry (OpenG2P-based). Provides read-only farmer data
for DA profile screens (DA-40) and queues farmer updates for sync (DA-41).

Architecture:
- client.py: HTTP client interface + factory
- mock.py: In-memory mock for testing (default)
- schemas.py: Pydantic models for farmer data
- mapper.py: Transform Farmer Registry ↔ DA backend formats
"""

from .client import FarmerRegistryClient, get_farmer_registry_client

__all__ = ["FarmerRegistryClient", "get_farmer_registry_client"]
