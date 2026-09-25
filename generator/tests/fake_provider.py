"""A provider stand-in for the tests.

The tests exercise prompt assembly, history handling and status writing. None
of that should need a model, a network or the codex CLI, so `get_provider` is
patched to hand back one of these instead.
"""

from __future__ import annotations

from pathlib import Path


class FakeProvider:
    """Returns canned text and writes a one-pixel PNG."""

    # A valid 1x1 PNG, so Pillow can open whatever the generator post-processes.
    PNG_BYTES = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
        "00000049454e44ae426082"
    )

    def __init__(self, texts: list[str] | str) -> None:
        self._texts = [texts] if isinstance(texts, str) else list(texts)
        self.text_calls: list[str] = []
        self.image_calls: list[str] = []

    @property
    def name(self) -> str:
        return "fake"

    def generate_text(self, prompt: str) -> str:
        self.text_calls.append(prompt)
        if not self._texts:
            raise AssertionError("FakeProvider ran out of canned responses")
        # The last response repeats, so a test does not have to count calls.
        return self._texts.pop(0) if len(self._texts) > 1 else self._texts[0]

    def generate_image(
        self,
        prompt: str,
        reference_images: list[Path],
        output_path: Path,
    ) -> Path:
        self.image_calls.append(prompt)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(self.PNG_BYTES)
        return output_path
