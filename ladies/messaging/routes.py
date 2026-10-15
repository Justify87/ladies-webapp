"""Canonical messaging router composition.

Conversation/message routes live in ``routes_v2``. WhatsApp connection/session
lifecycle is installed separately so pairing and auth persistence stay
independent from chat state.
"""

from .connection_lifecycle import install_connection_lifecycle
from .routes_v2 import install_messaging as _install_messaging


def install_messaging(app, *, data_dir, database=None):
    service = _install_messaging(app, data_dir=data_dir, database=database)
    install_connection_lifecycle(app, service)
    return service


__all__ = ["install_messaging"]
