"""CLI entry point for the macOS worker."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .config import load_config
from .generator import run_generation


def configure_logging(project_dir: Path) -> None:
    """Configure file and stdout logging."""
    log_dir = project_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "foredogs-macos.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(),
        ],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone macOS foredogs worker")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to config JSON relative to project root or absolute.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print final result as JSON.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run weather/style/prompt pipeline but skip image generation.",
    )
    parser.add_argument(
        "--forecast-file",
        help="Optional JSON file with a pre-fetched forecast object. Useful for replaying HA weather input locally.",
    )
    parser.add_argument(
        "--list-celebrations",
        action="store_true",
        help="Print the resolved celebration calendar for a year and exit. "
             "Use this to check a birthday or rule lands on the date you expect.",
    )
    parser.add_argument(
        "--year",
        type=int,
        help="Year for --list-celebrations (default: current year).",
    )
    return parser.parse_args()


def list_celebrations(project_dir: Path, year: int | None) -> int:
    """Walk a whole year and print every day that resolves to a celebration.

    Brute force over 365 days rather than inverting each rule: it is instant at
    this scale, and it exercises exactly the code path the generator uses, so a
    date shown here is a date that will really fire.
    """
    from datetime import date, timedelta

    from .celebrations import active_celebrations, load_events

    year = year or date.today().year
    events = load_events(project_dir / "config")

    day = date(year, 1, 1)
    end = date(year + 1, 1, 1)
    found = 0
    while day < end:
        active = active_celebrations(events, day)
        # Multi-day events would otherwise print on every one of their days.
        headline = [c for c in active if c.day_index == 0]
        if headline:
            found += 1
            names = ", ".join(
                f"{c.name} [{c.kind}, prio {c.priority}]" for c in headline
            )
            print(f"{day:%d.%m.%Y} {day:%a}  {names}")
        day += timedelta(days=1)

    print(f"\n{found} day(s) with a celebration in {year}, from {len(events)} configured events.")
    return 0


def main() -> int:
    args = parse_args()
    project_dir = Path(__file__).resolve().parents[1]

    if args.list_celebrations:
        # Before logging is configured: this is a read-only inspection command,
        # and it should not append to the pipeline's log file.
        return list_celebrations(project_dir, args.year)

    configure_logging(project_dir)

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (project_dir / config_path).resolve()

    config = load_config(config_path)
    forecast_override = None
    if args.forecast_file:
        forecast_path = Path(args.forecast_file)
        if not forecast_path.is_absolute():
            forecast_path = (project_dir / forecast_path).resolve()
        forecast_override = json.loads(forecast_path.read_text())

    result = run_generation(
        config,
        project_dir,
        dry_run=args.dry_run,
        forecast_override=forecast_override,
    )

    payload = {
        "original_path": str(result.original_path),
        "optimized_path": str(result.optimized_path),
        "status_path": str(result.status_path),
        "forecast": result.forecast,
        "style": result.style_entry,
        "activity": result.activity,
        "activity_prompt_path": str(result.activity_prompt_path) if result.activity_prompt_path else None,
        "image_prompt_path": str(result.image_prompt_path) if result.image_prompt_path else None,
        "dry_run": args.dry_run,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"original={result.original_path}")
        print(f"optimized={result.optimized_path}")
        print(f"status={result.status_path}")
        print(f"activity={result.activity}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
