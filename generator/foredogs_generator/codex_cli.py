"""Codex CLI bridge."""

from __future__ import annotations

import logging
import subprocess
import tempfile
import textwrap
from pathlib import Path

from .providers.base import ProviderError

logger = logging.getLogger("foredogs_generator")


class CodexCliError(ProviderError):
    """Raised when Codex CLI fails.

    A ProviderError, so a caller that handles any backend failure does not have
    to know which backend it got.
    """


def _run_codex(
    *,
    prompt: str,
    model: str,
    workdir: Path,
    output_last_message_path: Path,
    timeout_seconds: int,
    image_paths: list[Path] | None = None,
    reasoning_effort: str | None = None,
) -> str:
    use_stdin_prompt = bool(image_paths)
    command = [
        "codex",
        "-a",
        "never",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--color",
        "never",
        "-C",
        str(workdir),
        "-m",
        model,
        "-o",
        str(output_last_message_path),
    ]

    # Passed through as a config override rather than a flag: `codex exec` has
    # no --reasoning-effort of its own, but -c sets the same key the TUI uses.
    if reasoning_effort:
        command.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])

    for image_path in image_paths or []:
        command.extend(["-i", str(image_path)])

    if use_stdin_prompt:
        command.append("-")
    else:
        command.append(prompt)
    logger.info(
        "Running Codex CLI model=%s effort=%s in %s",
        model,
        reasoning_effort or "default",
        workdir,
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        input=prompt if use_stdin_prompt else None,
        timeout=timeout_seconds,
    )

    if result.returncode != 0:
        raise CodexCliError(
            textwrap.dedent(
                f"""
                Codex CLI failed with exit code {result.returncode}
                STDOUT:
                {result.stdout}
                STDERR:
                {result.stderr}
                """
            ).strip()
        )

    if not output_last_message_path.exists():
        raise CodexCliError("Codex CLI finished without writing last-message output.")

    return output_last_message_path.read_text().strip()


def generate_activity_text(
    prompt: str,
    model: str,
    timeout_seconds: int,
    reasoning_effort: str | None = None,
) -> str:
    """Generate activity text through Codex."""
    with tempfile.TemporaryDirectory(prefix="foredogs-codex-text-") as temp_dir:
        workdir = Path(temp_dir)
        response_path = workdir / "activity.txt"
        return _run_codex(
            prompt=prompt,
            model=model,
            workdir=workdir,
            output_last_message_path=response_path,
            timeout_seconds=timeout_seconds,
            reasoning_effort=reasoning_effort,
        )


def generate_image_file(
    *,
    prompt: str,
    model: str,
    timeout_seconds: int,
    image_paths: list[Path],
    output_path: Path,
    reasoning_effort: str | None = None,
) -> Path:
    """Generate image via Codex built-in image tool and copy to output path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="foredogs-codex-image-") as temp_dir:
        workdir = Path(temp_dir)
        response_path = workdir / "image_path.txt"
        full_prompt = textwrap.dedent(
            f"""
            Use the built-in image generation tool to create exactly one image.
            Use all attached reference images to preserve the dog's identity.
            After generation, copy the selected generated image into this absolute path:
            {output_path}

            Final response rules:
            - Print only the absolute path to the copied file.
            - Do not print commentary.

            Image request:
            {prompt}
            """
        ).strip()
        response = _run_codex(
            prompt=full_prompt,
            model=model,
            workdir=workdir,
            output_last_message_path=response_path,
            timeout_seconds=timeout_seconds,
            image_paths=image_paths,
            reasoning_effort=reasoning_effort,
        )

    resolved_path = Path(response).expanduser()
    if resolved_path != output_path:
        raise CodexCliError(f"Codex returned unexpected output path: {resolved_path}")
    if not output_path.exists():
        raise CodexCliError(f"Codex reported success but file does not exist: {output_path}")
    return output_path
