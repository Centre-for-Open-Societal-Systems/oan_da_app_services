"""Master Data geography (country, region, zone, woreda, kebele), read with the caller's own token."""

from .client import LEVELS, MasterDataClient
from .errors import MasterDataError

__all__ = ["LEVELS", "MasterDataClient", "MasterDataError"]
