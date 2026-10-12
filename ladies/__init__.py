"""Ladies Explorer application package."""
from __future__ import annotations

import logging
import sys
import types

LOGGER = logging.getLogger("ladies_explorer.package")

# Temporary import compatibility for callers/tests from pre-neutral filenames.
# No versioned source file exists; importing the legacy module name delegates
# lazily to the current neutral app module.
_legacy_web = types.ModuleType(__name__ + ".web_v92")


def _legacy_create_app(*args, **kwargs):
    from .app import create_app
    return create_app(*args, **kwargs)


_legacy_web.create_app = _legacy_create_app
sys.modules.setdefault(__name__ + ".web_v92", _legacy_web)
