"""Provider interface: how the generator reaches a model.

The generator itself has no opinion about which model produces the activity
text or the picture. It needs two things — a line of text, and an image file
built from reference photos — and everything else is a provider concern.

Two providers ship:

  codex   drives the `codex` CLI as a subprocess. No API key and no endpoint
          live in this project; whatever the CLI is logged into answers.
  openai  talks to any OpenAI-compatible endpoint directly: a local proxy,
          LiteLLM, OpenRouter, Ollama, or OpenAI and Google themselves.

The image call is the part that constrains the choice. Reference photos are not
optional here — they are what keeps the dog recognisable — so a provider has to
accept image input alongside the prompt. That rules out plain
`/images/generations`, which takes a prompt and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


class ProviderError(RuntimeError):
    """A provider failed to produce what was asked of it.

    Transport hangs and refusals both land here, because the caller treats them
    the same way: retry the identical prompt a few times, then give up and
    leave yesterday's picture on the display.
    """


@dataclass(slots=True)
class ProviderSettings:
    """Everything a provider needs, resolved from configuration."""

    # "codex" or "openai".
    kind: str = "codex"

    # Model for the activity description.
    text_model: str = "gpt-5.6-luna"
    # Model for the picture. None means "same as text_model", which is right
    # for codex, where one agent orchestrates its own image tool.
    image_model: str | None = None

    # Codex only: passed as a config override, since `codex exec` has no flag
    # for it. Ignored by the openai provider.
    reasoning_effort: str | None = "max"

    timeout_seconds: int = 1800

    # openai only.
    base_url: str | None = None
    # Name of the environment variable holding the key — never the key itself.
    api_key_env: str = "FOREDOGS_API_KEY"
    # Passed as extra_body {"size": ...}. Endpoints that do not know the field
    # ignore it; those that do use it to pick the generation resolution.
    image_size: str | None = "1280x720"
    # Reference photos are downscaled before being encoded, because a handful
    # of 1 MB JPEGs turn into several megabytes of base64 in the request body.
    reference_image_max_px: int = 1024

    def resolved_image_model(self) -> str:
        return self.image_model or self.text_model


@runtime_checkable
class Provider(Protocol):
    """What the generator needs from a model backend."""

    def generate_text(self, prompt: str) -> str:
        """Return a single line of text. Raises ProviderError on failure."""

    def generate_image(
        self,
        prompt: str,
        reference_images: list[Path],
        output_path: Path,
    ) -> Path:
        """Write one image to output_path and return it.

        The reference images carry the dogs' identity. A provider that cannot
        accept image input cannot implement this.
        """

    @property
    def name(self) -> str:
        """Short label for logs and the status file."""
