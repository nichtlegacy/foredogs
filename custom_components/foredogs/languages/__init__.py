"""Dashboard languages.

To add one: copy `en.py`, translate the tables, override whichever formatting
methods differ, and add the module to `_MODULES` below. `tests/test_languages.py`
fails if a module in this folder is not registered, so the two cannot drift.
"""

from __future__ import annotations

import logging
from importlib import import_module

from .base import Language

_LOGGER = logging.getLogger(__name__)

#: Module name -> the code it registers under. Both are stated so a typo shows
#: up as a failing test rather than as a language that silently never loads.
_MODULES = {
    "de": "de",
    "en": "en",
}

DEFAULT_LANGUAGE = "de"

_CACHE: dict[str, Language] = {}


def _load(module_name: str) -> Language:
    module = import_module(f"{__name__}.{module_name}")
    language = getattr(module, "LANGUAGE", None)
    if not isinstance(language, Language):
        raise TypeError(f"{module_name} does not export a Language as LANGUAGE")
    return language


def available_languages() -> list[str]:
    """Every registered code, sorted. Used by the tests and by error messages."""
    return sorted(_MODULES.values())


def get_language(code: str | None) -> Language:
    """Resolve a code to a language.

    An unknown code falls back to the default and logs it rather than raising:
    a typo in an automation should cost you a German panel, not a blank one.
    """
    wanted = (code or DEFAULT_LANGUAGE).strip().lower()

    # "en-GB" and "de_DE" both resolve to their base language. Home Assistant
    # hands out locale strings in both shapes.
    if wanted not in _MODULES:
        wanted = wanted.replace("_", "-").split("-", 1)[0]

    if wanted not in _MODULES:
        _LOGGER.warning(
            "Unknown dashboard language %r; falling back to %r. Available: %s",
            code,
            DEFAULT_LANGUAGE,
            ", ".join(available_languages()),
        )
        wanted = DEFAULT_LANGUAGE

    if wanted not in _CACHE:
        _CACHE[wanted] = _load(_MODULES[wanted])
    return _CACHE[wanted]


__all__ = ["DEFAULT_LANGUAGE", "Language", "available_languages", "get_language"]
