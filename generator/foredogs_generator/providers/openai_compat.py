"""The OpenAI-compatible provider.

One adapter covers a lot of ground, because the chat-completions shape has
become the common denominator: OpenAI itself, Google's OpenAI-compatible
endpoint, LiteLLM, OpenRouter, Ollama, vLLM, and most self-hosted proxies all
speak it.

Images go through chat completions rather than `/images/generations`, because
the reference photos are not optional: the dogs have to stay recognisable, and
`/images/generations` accepts a prompt and nothing else. Endpoints that return
a picture from a chat call put it in the message content as a data URL, which
is what gets parsed back out here.

The key is read from an environment variable named in the configuration, never
from the configuration itself.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import re
from pathlib import Path

from .base import ProviderError, ProviderSettings

logger = logging.getLogger("foredogs_generator")

_DATA_URL_PATTERN = re.compile(r"data:image/[^;]+;base64,([A-Za-z0-9+/=]+)")


class OpenAICompatibleProvider:
    """Generate through any OpenAI-compatible chat-completions endpoint."""

    def __init__(self, settings: ProviderSettings) -> None:
        self._settings = settings
        self._client = None

    @property
    def name(self) -> str:
        return "openai"

    # --- client -------------------------------------------------------------
    def _get_client(self):
        """Build the client lazily.

        The openai package is not a hard dependency: an install that uses the
        codex provider should not have to carry it. The import error therefore
        has to explain itself, because "No module named 'openai'" out of a
        scheduled job at 04:30 explains nothing.
        """
        if self._client is not None:
            return self._client

        try:
            from openai import OpenAI
        except ImportError as error:
            raise ProviderError(
                "The 'openai' provider needs the openai package: "
                "python3 -m pip install openai"
            ) from error

        api_key = os.environ.get(self._settings.api_key_env)
        if not api_key:
            raise ProviderError(
                f"No API key in ${self._settings.api_key_env}. "
                f"Put it in a file and export it, for example: "
                f'export {self._settings.api_key_env}="$(cat ~/.config/foredogs/api_key)"'
            )

        self._client = OpenAI(base_url=self._settings.base_url, api_key=api_key)
        return self._client

    # --- text ---------------------------------------------------------------
    def generate_text(self, prompt: str) -> str:
        client = self._get_client()
        logger.info("Requesting activity from %s", self._settings.text_model)
        try:
            response = client.chat.completions.create(
                model=self._settings.text_model,
                messages=[{"role": "user", "content": prompt}],
                timeout=self._settings.timeout_seconds,
            )
        except Exception as error:
            raise ProviderError(f"Text request failed: {error}") from error

        if not response.choices:
            raise ProviderError("Text response contained no choices.")
        content = response.choices[0].message.content
        if not content:
            raise ProviderError("Text response was empty.")
        return content.strip()

    # --- image --------------------------------------------------------------
    def generate_image(
        self,
        prompt: str,
        reference_images: list[Path],
        output_path: Path,
    ) -> Path:
        client = self._get_client()
        model = self._settings.resolved_image_model()

        content: list[dict] = [{"type": "text", "text": prompt}]
        for data_url in self._encode_references(reference_images):
            content.append({"type": "image_url", "image_url": {"url": data_url}})

        extra_body = {}
        if self._settings.image_size:
            extra_body["size"] = self._settings.image_size

        logger.info(
            "Requesting image from %s with %s reference image(s)",
            model,
            len(content) - 1,
        )
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                extra_body=extra_body or None,
                timeout=self._settings.timeout_seconds,
            )
        except Exception as error:
            raise ProviderError(f"Image request failed: {error}") from error

        if not response.choices:
            raise ProviderError("Image response contained no choices.")
        message = response.choices[0].message

        image_bytes = self._extract_image(message)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)
        return output_path

    # --- helpers ------------------------------------------------------------
    def _encode_references(self, reference_images: list[Path]) -> list[str]:
        """Downscale and base64-encode the reference photos.

        Four untouched phone photos are several megabytes of base64 in the
        request body, which some gateways reject outright and all of them are
        slower for. The references only need to carry identity — breed, fur
        colour, markings, ear and snout shape — and that survives 1024px.
        """
        from PIL import Image

        encoded: list[str] = []
        max_px = self._settings.reference_image_max_px

        for path in reference_images:
            if not path.exists():
                logger.warning("Reference image missing, skipping: %s", path)
                continue

            with Image.open(path) as image:
                image = image.convert("RGB") if image.mode not in ("RGB", "L") else image
                image.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                image_format = "JPEG" if path.suffix.lower() in (".jpg", ".jpeg") else "PNG"
                image.save(buffer, format=image_format)

            mime = "image/jpeg" if image_format == "JPEG" else "image/png"
            payload = base64.b64encode(buffer.getvalue()).decode()
            encoded.append(f"data:{mime};base64,{payload}")

        if not encoded:
            raise ProviderError(f"No usable reference images among: {reference_images}")
        return encoded

    def _extract_image(self, message) -> bytes:
        """Pull image bytes out of a chat response.

        Endpoints disagree about where the picture goes, so all three shapes
        seen in practice are handled rather than assuming one.
        """
        # Some gateways return a structured multimodal message.
        for attribute in ("images", "multi_mod_content"):
            payload = getattr(message, attribute, None)
            if payload:
                extracted = self._from_structured(payload)
                if extracted:
                    return extracted

        content = getattr(message, "content", None)
        if not content:
            raise ProviderError("Image response contained no content.")

        if isinstance(content, list):
            for part in content:
                url = None
                if isinstance(part, dict):
                    url = part.get("image_url", {}).get("url") if isinstance(part.get("image_url"), dict) else None
                    url = url or part.get("url")
                if url:
                    extracted = self._from_data_url(url)
                    if extracted:
                        return extracted
            content = " ".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )

        if not isinstance(content, str):
            raise ProviderError(f"Unexpected content type in image response: {type(content)}")

        extracted = self._from_data_url(content)
        if extracted:
            return extracted

        # Last resort: the whole body is raw base64, padding possibly stripped.
        # validate=True matters here. Without it b64decode silently drops any
        # character outside the alphabet, so a refusal like "I can't create
        # that image." decodes to garbage bytes and the failure only surfaces
        # later, as an unreadable PNG.
        cleaned = re.sub(r"\s+", "", content)
        if cleaned:
            padding = (-len(cleaned)) % 4
            try:
                return base64.b64decode(cleaned + "=" * padding, validate=True)
            except Exception:  # noqa: BLE001 - fall through to the error below
                pass

        raise ProviderError(
            "Could not find an image in the response. "
            f"First 200 characters: {content[:200]!r}"
        )

    @staticmethod
    def _from_structured(payload) -> bytes | None:
        for item in payload if isinstance(payload, list) else [payload]:
            url = None
            if isinstance(item, dict):
                image_url = item.get("image_url")
                if isinstance(image_url, dict):
                    url = image_url.get("url")
                url = url or item.get("url") or item.get("b64_json")
            if url:
                extracted = OpenAICompatibleProvider._from_data_url(url)
                if extracted:
                    return extracted
        return None

    @staticmethod
    def _from_data_url(text: str) -> bytes | None:
        match = _DATA_URL_PATTERN.search(text)
        if match:
            return base64.b64decode(match.group(1))
        return None


__all__ = ["OpenAICompatibleProvider"]
