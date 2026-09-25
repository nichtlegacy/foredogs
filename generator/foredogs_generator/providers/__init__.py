"""Model backends.

`get_provider()` is the only entry point the rest of the generator uses.
"""

from __future__ import annotations

from .base import Provider, ProviderError, ProviderSettings
from .codex import CodexProvider
from .openai_compat import OpenAICompatibleProvider

# Aliases, so a configuration that says "openai-compatible" or "gemini" does
# what it obviously means rather than failing on a spelling.
_KINDS: dict[str, str] = {
    "codex": "codex",
    "codex-cli": "codex",
    "openai": "openai",
    "openai-compatible": "openai",
    "openai_compatible": "openai",
    "gemini": "openai",
    "google": "openai",
    "litellm": "openai",
    "openrouter": "openai",
    "ollama": "openai",
}


def get_provider(settings: ProviderSettings) -> Provider:
    """Build the provider named by the settings."""
    kind = _KINDS.get(settings.kind.strip().lower())
    if kind is None:
        known = ", ".join(sorted(set(_KINDS)))
        raise ProviderError(f"Unknown provider {settings.kind!r}. Known values: {known}")

    if kind == "codex":
        return CodexProvider(settings)
    return OpenAICompatibleProvider(settings)


__all__ = [
    "CodexProvider",
    "OpenAICompatibleProvider",
    "Provider",
    "ProviderError",
    "ProviderSettings",
    "get_provider",
]
