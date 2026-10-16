"""Public messaging router entry point.

The canonical WhatsApp/internal conversation service remains independent from
search/discovery state. Product visibility is applied by the durable
classification layer in routes_classified.
"""

from .routes_classified import install_messaging

__all__ = ["install_messaging"]
