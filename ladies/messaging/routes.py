"""Compatibility import for the canonical messaging router.

The previous router coupled conversation access to current Ladies listing
identity. The active implementation lives in routes_v2 and treats persisted
messaging resources as authoritative.
"""

from .routes_v2 import install_messaging

__all__ = ["install_messaging"]
