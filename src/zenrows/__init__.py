"""Zenrows Python SDK.

Two clients live here:

  - `ZenRowsClient` — the original synchronous scraping API client.
    Backward-compatible with pre-1.4 usage; freshened-up internals.

  - `ZenRowsBatchClient` — the new async-job / batch API client.
    Built on a generated OpenAPI core with a hand-written ergonomic
    facade on top.
"""

from zenrows.__version__ import __version__
from zenrows.batch import ZenRowsBatchClient
from zenrows.client import ZenRowsClient

__all__ = ["ZenRowsBatchClient", "ZenRowsClient", "__version__"]
