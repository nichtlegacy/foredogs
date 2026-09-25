"""Import the Home Assistant component without Home Assistant installed.

`custom_components/foredogs/__init__.py` imports `homeassistant`, which is not
a dependency of this repository — it is provided by the host at runtime. The
rendering modules themselves need nothing but Pillow, so registering an empty
stand-in package pointing at the component directory lets the tests import
exactly the code that runs on the device path.

This is the same trick `tools/preview_dashboard.py` uses, for the same reason.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "foredogs"
PACKAGE = "_foredogs_under_test"

if PACKAGE not in sys.modules:
    shim = types.ModuleType(PACKAGE)
    shim.__path__ = [str(COMPONENT)]  # type: ignore[attr-defined]
    sys.modules[PACKAGE] = shim


def module(name: str):
    """Import `custom_components/foredogs/<name>.py`."""
    return importlib.import_module(f"{PACKAGE}.{name}")


languages = module("languages")
dashboard_render = module("dashboard_render")
dashboard_pages = module("dashboard_pages")
