"""Configuration loading for the macOS foredogs worker."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .providers.base import ProviderSettings


@dataclass(slots=True)
class DogProfile:
    """One dog profile from config."""

    name: str
    description: str
    image_paths: list[str]


@dataclass(slots=True)
class WeatherConfig:
    """Weather provider settings."""

    provider: str = "open-meteo"
    geocoding_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    geocoding_language: str = "de"
    timezone: str = "auto"
    forecast_days: int = 1


@dataclass(slots=True)
class AppConfig:
    """App configuration loaded from JSON and helper files."""

    location: str
    dogs: list[DogProfile]
    art_style_entries: list[dict] = field(default_factory=list)
    image_gen_aspect_ratio: str = "16:9"
    image_gen_resolution: str = "1K"
    final_image_size: str = "800x480"
    display_profile: str | None = None
    # Whether the generated artwork carries its own weather caption box. Off by
    # default now that the e-ink dashboard draws weather itself in crisp palette
    # colours — two weather readouts on one screen is one too many, and the
    # painted one is the less legible of the two.
    include_weather_box: bool = False
    state_dir: str = "./state"
    output_dir: str = "./output"
    # Dated copies of every generated original. The working files are
    # overwritten each run, Home Assistant prunes its own copies, and Immich can
    # be down for weeks without anyone noticing — as happened. This is the one
    # copy that lives on the machine that made the picture.
    archive_dir: str = "./output/archive"
    # 0 keeps everything. At roughly 2.5 MB a day that is under a gigabyte a
    # year, which is nothing on the machine already running the model.
    archive_keep_days: int = 0
    seed_prompt_history_path: str | None = None
    seed_style_history_path: str | None = None
    codex_model: str = "gpt-5.6-luna"
    # Reasoning effort for Codex. "max" costs more time per call but noticeably
    # improves how well the scene follows the style and outfit instructions.
    codex_reasoning_effort: str = "max"
    generation_timeout_seconds: int = 1800
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    # Which backend produces the text and the picture. Built from the optional
    # `provider` block, falling back to the older codex_* keys so an existing
    # config.json keeps working untouched.
    provider: ProviderSettings = field(default_factory=ProviderSettings)

    @property
    def dog_names(self) -> list[str]:
        return [dog.name for dog in self.dogs]

    @property
    def dog_descriptions(self) -> list[str]:
        return [dog.description for dog in self.dogs]

    @property
    def input_image_paths(self) -> list[str]:
        image_paths: list[str] = []
        for dog in self.dogs:
            image_paths.extend(dog.image_paths)
        return image_paths

    def resolve_path(self, base_dir: Path, raw_path: str | None) -> Path | None:
        """Resolve optional config path."""
        if not raw_path:
            return None

        path = Path(raw_path).expanduser()
        if path.is_absolute():
            return path
        return (base_dir / path).resolve()

    def resolved_image_paths(self, base_dir: Path) -> list[Path]:
        """Resolve dog image paths."""
        return [self.resolve_path(base_dir, raw_path) for raw_path in self.input_image_paths if raw_path]


def _load_dogs(dogs_path: Path) -> list[DogProfile]:
    payload = json.loads(dogs_path.read_text())
    dogs = [DogProfile(**item) for item in payload.get("dogs", [])]
    if not dogs:
        raise ValueError(f"No dogs configured in {dogs_path}")
    return dogs


def _load_art_styles(art_styles_path: Path | None) -> list[dict]:
    if art_styles_path is None or not art_styles_path.exists():
        return []
    payload = json.loads(art_styles_path.read_text())
    return [dict(item) for item in payload.get("art_styles", [])]


def _load_provider(provider_payload: dict | None, generator: dict) -> ProviderSettings:
    """Resolve provider settings, preferring the `provider` block.

    Without that block the older `generator.codex_*` keys are used, so a
    configuration written before providers existed keeps selecting codex with
    the same model and the same reasoning effort. Nobody has to edit a working
    installation to pick up this feature.
    """
    timeout = generator.get("generation_timeout_seconds", 1800)

    if not provider_payload:
        return ProviderSettings(
            kind="codex",
            text_model=generator.get("codex_model", "gpt-5.6-luna"),
            reasoning_effort=generator.get("codex_reasoning_effort", "max"),
            timeout_seconds=timeout,
        )

    settings = ProviderSettings(
        kind=provider_payload.get("kind", "codex"),
        text_model=provider_payload.get(
            "text_model", generator.get("codex_model", "gpt-5.6-luna")
        ),
        image_model=provider_payload.get("image_model"),
        reasoning_effort=provider_payload.get(
            "reasoning_effort", generator.get("codex_reasoning_effort", "max")
        ),
        timeout_seconds=provider_payload.get("timeout_seconds", timeout),
        base_url=provider_payload.get("base_url"),
        api_key_env=provider_payload.get("api_key_env", "FOREDOGS_API_KEY"),
        image_size=provider_payload.get("image_size", "1280x720"),
        reference_image_max_px=provider_payload.get("reference_image_max_px", 1024),
    )
    return settings


def load_config(config_path: Path) -> AppConfig:
    """Load config from disk."""
    payload = json.loads(config_path.read_text())
    base_dir = config_path.parent

    paths = payload.get("paths", {})
    generator = payload.get("generator", {})
    weather_payload = payload.get("weather", {})

    dogs_path = Path(paths["dogs_file"]).expanduser()
    if not dogs_path.is_absolute():
        dogs_path = (base_dir / dogs_path).resolve()

    art_styles_path = None
    if paths.get("art_styles_file"):
        art_styles_path = Path(paths["art_styles_file"]).expanduser()
        if not art_styles_path.is_absolute():
            art_styles_path = (base_dir / art_styles_path).resolve()

    return AppConfig(
        location=payload["location"],
        dogs=_load_dogs(dogs_path),
        art_style_entries=_load_art_styles(art_styles_path),
        image_gen_aspect_ratio=generator.get("image_gen_aspect_ratio", "16:9"),
        image_gen_resolution=generator.get("image_gen_resolution", "1K"),
        final_image_size=generator.get("final_image_size", "800x480"),
        display_profile=generator.get("display_profile"),
        include_weather_box=generator.get("include_weather_box", False),
        state_dir=generator.get("state_dir", "./state"),
        output_dir=generator.get("output_dir", "./output"),
        archive_dir=generator.get("archive_dir", "./output/archive"),
        archive_keep_days=generator.get("archive_keep_days", 0),
        seed_prompt_history_path=generator.get("seed_prompt_history_path"),
        seed_style_history_path=generator.get("seed_style_history_path"),
        codex_model=generator.get("codex_model", "gpt-5.6-luna"),
        codex_reasoning_effort=generator.get("codex_reasoning_effort", "max"),
        generation_timeout_seconds=generator.get("generation_timeout_seconds", 1800),
        weather=WeatherConfig(**weather_payload),
        provider=_load_provider(payload.get("provider"), generator),
    )
