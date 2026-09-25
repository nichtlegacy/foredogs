"""Batch weather/style pre-generation CLI."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import load_config
from .generator import prepare_generation, render_generation_plan, run_generation
from .main import configure_logging
from .weather import fetch_daily_forecast

logger = logging.getLogger("foredogs_generator.batch")

SCENARIO_PRESETS = [
    {"condition": "sunny", "weather_summary_de": "Sonnig und warm", "temperature": 29, "templow": 18, "precipitation": 0.0, "humidity": 42, "wind_speed": 10.0},
    {"condition": "partlycloudy", "weather_summary_de": "Heiter bis wolkig", "temperature": 25, "templow": 16, "precipitation": 0.0, "humidity": 48, "wind_speed": 9.0},
    {"condition": "cloudy", "weather_summary_de": "Stark bewölkt", "temperature": 22, "templow": 15, "precipitation": 0.0, "humidity": 56, "wind_speed": 11.0},
    {"condition": "rainy", "weather_summary_de": "Regnerisch und kühl", "temperature": 18, "templow": 12, "precipitation": 3.2, "humidity": 78, "wind_speed": 16.0},
    {"condition": "pouring", "weather_summary_de": "Starker Dauerregen", "temperature": 16, "templow": 11, "precipitation": 9.5, "humidity": 89, "wind_speed": 24.0},
    {"condition": "lightning-rainy", "weather_summary_de": "Gewitter mit Regen", "temperature": 21, "templow": 15, "precipitation": 7.0, "humidity": 82, "wind_speed": 21.0},
    {"condition": "windy", "weather_summary_de": "Windig und frisch", "temperature": 19, "templow": 12, "precipitation": 0.0, "humidity": 55, "wind_speed": 28.0},
    {"condition": "windy-variant", "weather_summary_de": "Böig und wechselhaft", "temperature": 17, "templow": 10, "precipitation": 1.0, "humidity": 60, "wind_speed": 32.0},
    {"condition": "fog", "weather_summary_de": "Neblig am Morgen", "temperature": 11, "templow": 7, "precipitation": 0.0, "humidity": 96, "wind_speed": 5.0},
    {"condition": "snowy", "weather_summary_de": "Schneefall und kalt", "temperature": -1, "templow": -5, "precipitation": 2.5, "humidity": 91, "wind_speed": 13.0},
    {"condition": "snowy-rainy", "weather_summary_de": "Schneeregen und nasskalt", "temperature": 2, "templow": -1, "precipitation": 4.1, "humidity": 93, "wind_speed": 18.0},
    {"condition": "hail", "weather_summary_de": "Hagelschauer", "temperature": 8, "templow": 4, "precipitation": 5.0, "humidity": 85, "wind_speed": 22.0},
    {"condition": "lightning", "weather_summary_de": "Gewitterstimmung", "temperature": 20, "templow": 14, "precipitation": 2.2, "humidity": 73, "wind_speed": 20.0},
    {"condition": "clear-night", "weather_summary_de": "Klare Nacht", "temperature": 9, "templow": 5, "precipitation": 0.0, "humidity": 65, "wind_speed": 6.0},
    {"condition": "exceptional", "weather_summary_de": "Dramatische Wetterlage", "temperature": 14, "templow": 8, "precipitation": 1.5, "humidity": 68, "wind_speed": 19.0},
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pre-generate multiple foredogs images for weather/style testing")
    parser.add_argument("--config", default="config.json", help="Path to config JSON relative to project root or absolute.")
    parser.add_argument(
        "--forecast-file",
        help="Optional base forecast JSON used as template for generated scenarios.",
    )
    parser.add_argument("--count", type=int, default=12, help="How many images to generate. Recommended: 10-20.")
    parser.add_argument("--parallel", type=int, default=1, help="How many images to render in parallel. Uses serial prompt planning and parallel image generation.")
    parser.add_argument("--dry-run", action="store_true", help="Skip image generation and only build prompts/status files.")
    parser.add_argument("--json", action="store_true", help="Print final batch manifest as JSON.")
    return parser.parse_args()


def _slugify(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value.lower()).strip("_")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def build_scenarios(base_forecast: dict, count: int) -> list[dict]:
    """Create deterministic weather scenarios for style testing."""
    if count < 1:
        raise ValueError("count must be at least 1")
    if count > 20:
        raise ValueError("count must be at most 20")

    base_date_raw = base_forecast.get("datetime", datetime.now(timezone.utc).isoformat())
    try:
        base_date = datetime.fromisoformat(base_date_raw.replace("Z", "+00:00"))
    except ValueError:
        base_date = datetime.now(timezone.utc)

    scenarios: list[dict] = []
    for index in range(count):
        preset = SCENARIO_PRESETS[index % len(SCENARIO_PRESETS)]
        scenario = dict(base_forecast)
        scenario.update(preset)
        scenario["datetime"] = (base_date + timedelta(days=index)).isoformat()
        scenario["wind_bearing"] = float((base_forecast.get("wind_bearing", 0.0) + (index * 23)) % 360)
        scenario["uv_index"] = max(0.0, float(base_forecast.get("uv_index", 3.0)) - (index % 4))
        scenarios.append(scenario)
    return scenarios


def _build_batch_dirs(project_dir: Path) -> tuple[Path, Path]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    batch_root = project_dir / "output" / "batches" / timestamp
    batch_state_dir = project_dir / "state" / "batch" / timestamp
    batch_root.mkdir(parents=True, exist_ok=True)
    batch_state_dir.mkdir(parents=True, exist_ok=True)
    return batch_root, batch_state_dir


def _build_item_payload(index: int, scenario: dict, result, item_output_dir: Path) -> dict:
    return {
        "index": index,
        "scenario": scenario,
        "style": result.style_entry,
        "activity": result.activity,
        "output_dir": str(item_output_dir),
        "original_path": str(result.original_path),
        "optimized_path": str(result.optimized_path),
        "status_path": str(result.status_path),
        "activity_prompt_path": str(result.activity_prompt_path) if result.activity_prompt_path else None,
        "image_prompt_path": str(result.image_prompt_path) if result.image_prompt_path else None,
    }


def _write_partial_manifest(manifest_path: Path, count: int, dry_run: bool, batch_items: list[dict]) -> None:
    ordered_items = sorted(batch_items, key=lambda item: item["index"])
    manifest_path.write_text(json.dumps({"count": count, "dry_run": dry_run, "items": ordered_items}, ensure_ascii=False, indent=2) + "\n")


def run_batch(
    *,
    config_path: Path,
    forecast_path: Path | None,
    count: int,
    parallel: int,
    dry_run: bool,
    project_dir: Path | None = None,
) -> dict:
    if project_dir is None:
        project_dir = Path(__file__).resolve().parents[1]
    configure_logging(project_dir)

    config = load_config(config_path)
    if forecast_path is not None:
        base_forecast = _load_json(forecast_path)
    else:
        base_forecast = fetch_daily_forecast(config.location, config.weather)
    scenarios = build_scenarios(base_forecast, count)
    batch_root, batch_state_dir = _build_batch_dirs(project_dir)
    manifest_path = batch_root / "batch_manifest.json"

    batch_items: list[dict] = []
    logger.info("Starting batch generation count=%s dry_run=%s parallel=%s output=%s", count, dry_run, parallel, batch_root)

    prepared_items: list[tuple[int, dict, Path, object]] = []

    for index, scenario in enumerate(scenarios, start=1):
        scenario_name = f"{index:02d}_{_slugify(scenario['condition'])}"
        item_output_dir = batch_root / scenario_name
        item_output_dir.mkdir(parents=True, exist_ok=True)

        item_config = replace(
            config,
            output_dir=str(item_output_dir),
            state_dir=str(batch_state_dir),
        )
        if dry_run and parallel == 1:
            result = run_generation(
                item_config,
                project_dir,
                dry_run=True,
                forecast_override=scenario,
            )
            item_payload = _build_item_payload(index, scenario, result, item_output_dir)
            batch_items.append(item_payload)
            _write_partial_manifest(manifest_path, count, dry_run, batch_items)
            print(
                f"[{index}/{count}] done condition={scenario['condition']} "
                f"style={result.style_entry['style']} output={item_output_dir}"
            )
            logger.info(
                "Batch item %s/%s complete condition=%s style=%s output=%s",
                index,
                count,
                scenario["condition"],
                result.style_entry["style"],
                item_output_dir,
            )
            continue

        plan = prepare_generation(
            item_config,
            project_dir,
            forecast_override=scenario,
        )
        prepared_items.append((index, scenario, item_output_dir, plan))

    if dry_run:
        for index, scenario, item_output_dir, plan in prepared_items:
            result = render_generation_plan(plan, dry_run=True)
            item_payload = _build_item_payload(index, scenario, result, item_output_dir)
            batch_items.append(item_payload)
            _write_partial_manifest(manifest_path, count, dry_run, batch_items)
            print(
                f"[{index}/{count}] done condition={scenario['condition']} "
                f"style={result.style_entry['style']} output={item_output_dir}"
            )
            logger.info(
                "Batch item %s/%s complete condition=%s style=%s output=%s",
                index,
                count,
                scenario["condition"],
                result.style_entry["style"],
                item_output_dir,
            )
    elif parallel <= 1:
        for index, scenario, item_output_dir, plan in prepared_items:
            result = render_generation_plan(plan, dry_run=False)
            item_payload = _build_item_payload(index, scenario, result, item_output_dir)
            batch_items.append(item_payload)
            _write_partial_manifest(manifest_path, count, dry_run, batch_items)
            print(
                f"[{index}/{count}] done condition={scenario['condition']} "
                f"style={result.style_entry['style']} output={item_output_dir}"
            )
            logger.info(
                "Batch item %s/%s complete condition=%s style=%s output=%s",
                index,
                count,
                scenario["condition"],
                result.style_entry["style"],
                item_output_dir,
            )
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
            future_map = {
                executor.submit(render_generation_plan, plan, False): (index, scenario, item_output_dir)
                for index, scenario, item_output_dir, plan in prepared_items
            }
            for future in concurrent.futures.as_completed(future_map):
                index, scenario, item_output_dir = future_map[future]
                result = future.result()
                item_payload = _build_item_payload(index, scenario, result, item_output_dir)
                batch_items.append(item_payload)
                _write_partial_manifest(manifest_path, count, dry_run, batch_items)
                print(
                    f"[{index}/{count}] done condition={scenario['condition']} "
                    f"style={result.style_entry['style']} output={item_output_dir}"
                )
                logger.info(
                    "Batch item %s/%s complete condition=%s style=%s output=%s",
                    index,
                    count,
                    scenario["condition"],
                    result.style_entry["style"],
                    item_output_dir,
                )

    summary = {
        "count": count,
        "dry_run": dry_run,
        "batch_root": str(batch_root),
        "batch_state_dir": str(batch_state_dir),
        "manifest_path": str(manifest_path),
        "items": sorted(batch_items, key=lambda item: item["index"]),
        "config_snapshot": asdict(config),
    }
    manifest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


def main() -> int:
    args = parse_args()
    project_dir = Path(__file__).resolve().parents[1]

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (project_dir / config_path).resolve()

    forecast_path = None
    if args.forecast_file:
        forecast_path = Path(args.forecast_file)
        if not forecast_path.is_absolute():
            forecast_path = (project_dir / forecast_path).resolve()

    summary = run_batch(
        config_path=config_path,
        forecast_path=forecast_path,
        count=args.count,
        parallel=args.parallel,
        dry_run=args.dry_run,
        project_dir=project_dir,
    )

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"batch_root={summary['batch_root']}")
        print(f"manifest={summary['manifest_path']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
