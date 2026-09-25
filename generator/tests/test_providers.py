"""Provider selection, configuration fallback, and image response parsing."""

from __future__ import annotations

import base64
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from foredogs_generator.config import load_config
from foredogs_generator.providers import (
    CodexProvider,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderSettings,
    get_provider,
)
from foredogs_generator.providers.openai_compat import OpenAICompatibleProvider as OpenAI

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)
PNG_B64 = base64.b64encode(PNG_BYTES).decode()


class Message:
    """Minimal stand-in for an SDK message object."""

    def __init__(self, content, **extra):
        self.content = content
        for key, value in extra.items():
            setattr(self, key, value)


class ProviderSelectionTests(unittest.TestCase):
    def test_default_is_codex(self):
        self.assertIsInstance(get_provider(ProviderSettings()), CodexProvider)

    def test_aliases_all_resolve_to_the_openai_adapter(self):
        for kind in ("openai", "openai-compatible", "gemini", "litellm", "ollama", "OpenAI"):
            with self.subTest(kind=kind):
                provider = get_provider(ProviderSettings(kind=kind))
                self.assertIsInstance(provider, OpenAICompatibleProvider)

    def test_unknown_kind_names_the_known_ones(self):
        with self.assertRaises(ProviderError) as caught:
            get_provider(ProviderSettings(kind="banana"))
        self.assertIn("codex", str(caught.exception))

    def test_image_model_falls_back_to_the_text_model(self):
        settings = ProviderSettings(text_model="one")
        self.assertEqual(settings.resolved_image_model(), "one")
        settings = ProviderSettings(text_model="one", image_model="two")
        self.assertEqual(settings.resolved_image_model(), "two")


class ConfigFallbackTests(unittest.TestCase):
    """A config.json written before providers existed must keep working."""

    def _write_config(self, root: Path, payload: dict) -> Path:
        (root / "config").mkdir(parents=True, exist_ok=True)
        (root / "config" / "dogs.json").write_text(
            json.dumps({"dogs": [{"name": "Rex", "description": "d", "image_paths": []}]})
        )
        config_path = root / "config.json"
        base = {
            "location": "Testville, Testland",
            "paths": {"dogs_file": "./config/dogs.json"},
        }
        base.update(payload)
        config_path.write_text(json.dumps(base))
        return config_path

    def test_legacy_codex_keys_still_select_codex(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self._write_config(
                root,
                {
                    "generator": {
                        "codex_model": "gpt-5.6-luna",
                        "codex_reasoning_effort": "max",
                        "generation_timeout_seconds": 900,
                    }
                },
            )
            config = load_config(config_path)

        self.assertEqual(config.provider.kind, "codex")
        self.assertEqual(config.provider.text_model, "gpt-5.6-luna")
        self.assertEqual(config.provider.reasoning_effort, "max")
        self.assertEqual(config.provider.timeout_seconds, 900)
        self.assertIsInstance(get_provider(config.provider), CodexProvider)

    def test_provider_block_wins_and_inherits_the_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = self._write_config(
                root,
                {
                    "generator": {"generation_timeout_seconds": 600},
                    "provider": {
                        "kind": "openai",
                        "text_model": "gemini-2.5-flash",
                        "image_model": "gemini-3-pro-image",
                        "base_url": "https://example.invalid/v1",
                        "api_key_env": "MY_KEY",
                    },
                },
            )
            config = load_config(config_path)

        self.assertEqual(config.provider.kind, "openai")
        self.assertEqual(config.provider.image_model, "gemini-3-pro-image")
        self.assertEqual(config.provider.api_key_env, "MY_KEY")
        # Not restated in the provider block, so it comes from the generator block.
        self.assertEqual(config.provider.timeout_seconds, 600)


class ImageExtractionTests(unittest.TestCase):
    """Endpoints disagree about where the picture goes in a chat response."""

    def setUp(self):
        self.provider = OpenAI(ProviderSettings(kind="openai"))

    def test_markdown_data_url(self):
        message = Message(f"Here you go:\n\n![image](data:image/png;base64,{PNG_B64})")
        self.assertEqual(self.provider._extract_image(message), PNG_BYTES)

    def test_bare_data_url(self):
        message = Message(f"data:image/png;base64,{PNG_B64}")
        self.assertEqual(self.provider._extract_image(message), PNG_BYTES)

    def test_raw_base64_without_padding(self):
        message = Message(PNG_B64.rstrip("="))
        self.assertEqual(self.provider._extract_image(message), PNG_BYTES)

    def test_structured_images_attribute(self):
        message = Message(
            "",
            images=[{"image_url": {"url": f"data:image/png;base64,{PNG_B64}"}}],
        )
        self.assertEqual(self.provider._extract_image(message), PNG_BYTES)

    def test_content_list_with_image_part(self):
        message = Message(
            [
                {"type": "text", "text": "here"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_B64}"}},
            ]
        )
        self.assertEqual(self.provider._extract_image(message), PNG_BYTES)

    def test_empty_content_is_an_error_not_a_crash(self):
        with self.assertRaises(ProviderError):
            self.provider._extract_image(Message(None))

    def test_prose_without_an_image_reports_what_came_back(self):
        message = Message("I can't create that image.")
        with self.assertRaises(ProviderError) as caught:
            self.provider._extract_image(message)
        self.assertIn("Could not find an image", str(caught.exception))


@unittest.skipUnless(
    importlib.util.find_spec("openai"),
    "the openai package is an optional dependency and is not installed",
)
class MissingCredentialTests(unittest.TestCase):
    def test_absent_api_key_explains_where_to_put_it(self):
        provider = OpenAI(ProviderSettings(kind="openai", api_key_env="FOREDOGS_TEST_KEY_ABSENT"))
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("FOREDOGS_TEST_KEY_ABSENT", None)
            with self.assertRaises(ProviderError) as caught:
                provider._get_client()
        self.assertIn("FOREDOGS_TEST_KEY_ABSENT", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
