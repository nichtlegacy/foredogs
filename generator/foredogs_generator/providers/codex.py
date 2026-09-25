"""The codex CLI provider.

Drives the `codex` binary as a subprocess. Nothing about the backend is
configured here: no endpoint, no API key, no port. Whichever provider the CLI
is logged into answers, which is set in ~/.codex/config.toml and is therefore
outside this project entirely.

That is the appeal and the cost. Switching backend needs no change here, but a
fresh install needs the CLI installed and logged in before anything works.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..codex_cli import CodexCliError, generate_activity_text, generate_image_file
from .base import ProviderError, ProviderSettings


class CodexProvider:
    """Generate through the codex CLI."""

    def __init__(self, settings: ProviderSettings) -> None:
        self._settings = settings

    @property
    def name(self) -> str:
        return "codex"

    def generate_text(self, prompt: str) -> str:
        try:
            return generate_activity_text(
                prompt,
                self._settings.text_model,
                self._settings.timeout_seconds,
                reasoning_effort=self._settings.reasoning_effort,
            ).strip()
        except subprocess.TimeoutExpired as error:
            # A timeout is a transport failure like any other as far as the
            # caller is concerned, so it arrives as the same exception type.
            raise ProviderError(f"codex timed out after {self._settings.timeout_seconds}s") from error

    def generate_image(
        self,
        prompt: str,
        reference_images: list[Path],
        output_path: Path,
    ) -> Path:
        try:
            return generate_image_file(
                prompt=prompt,
                model=self._settings.resolved_image_model(),
                reasoning_effort=self._settings.reasoning_effort,
                timeout_seconds=self._settings.timeout_seconds,
                image_paths=reference_images,
                output_path=output_path,
            )
        except subprocess.TimeoutExpired as error:
            raise ProviderError(f"codex timed out after {self._settings.timeout_seconds}s") from error


__all__ = ["CodexProvider", "CodexCliError"]
