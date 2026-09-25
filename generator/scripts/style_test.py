"""Generate one image per named art style against a single shared forecast.

Built for judging new catalogue entries against each other. The batch runner
cannot do this: it shifts the date by one day per item and cycles weather
presets, so every image would differ in more than the style. Here the forecast
is fetched once and reused, which leaves the style as the only variable.

A style is forced by handing the generator a pool containing just that entry,
so no generator code needs a test-only branch.

Prompt planning runs serially and image rendering runs in parallel. That split
is deliberate: all items share one history file, so serial planning both avoids
a write race and lets each activity see the previous ones and pick something
different. Rendering is where the wall-clock actually sits.

State goes to a throwaway directory inside the run folder, so a test run never
touches the live style rotation.

    python3 scripts/style_test.py --styles-file styles.txt --parallel 3
    python3 scripts/style_test.py --styles "Bauhaus geometric poster style" --dry-run
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from foredogs_generator.batch import _slugify  # noqa: E402
from foredogs_generator.config import load_config  # noqa: E402
from foredogs_generator.generator import prepare_generation, render_generation_plan  # noqa: E402
from foredogs_generator.main import configure_logging  # noqa: E402
from foredogs_generator.weather import fetch_daily_forecast  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate one image per art style against one shared forecast")
    parser.add_argument("--config", default="config.json", help="Config JSON, relative to the project root or absolute.")
    parser.add_argument("--styles", help="Comma-separated style names, exactly as they appear in the catalogue.")
    parser.add_argument("--styles-file", help="File with one style name per line. Blank lines and # comments ignored.")
    parser.add_argument("--label", help="Suffix for the run folder name, e.g. 'new-batch'.")
    parser.add_argument("--parallel", type=int, default=3, help="Concurrent image renders. Higher risks provider rate limits.")
    parser.add_argument("--forecast-file", help="Use this forecast JSON instead of fetching today's.")
    parser.add_argument(
        "--timeout",
        type=int,
        help="Per-call Codex timeout in seconds, overriding the config. The daily run allows 1800, "
             "which is far too patient for a test: a hung text call costs half an hour before it "
             "raises. A few hundred seconds fails fast and still clears a healthy call by a wide margin.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build prompts only, generate no images.")
    return parser.parse_args()


def read_style_names(args: argparse.Namespace) -> list[str]:
    names: list[str] = []
    if args.styles:
        names += [part.strip() for part in args.styles.split(",")]
    if args.styles_file:
        for line in Path(args.styles_file).read_text().splitlines():
            text = line.strip()
            if text and not text.startswith("#"):
                names.append(text)
    if not names:
        raise SystemExit("Give at least one style via --styles or --styles-file.")
    return names


def resolve_styles(names: list[str], pool: list[dict]) -> list[dict]:
    """Match requested names against the catalogue, failing loudly on typos.

    Exact match first, then a unique case-insensitive prefix, so long entries
    can be requested by their leading words.
    """
    by_name = {entry["style"]: entry for entry in pool}
    resolved: list[dict] = []

    for name in names:
        if name in by_name:
            resolved.append(by_name[name])
            continue

        matches = [entry for entry in pool if entry["style"].lower().startswith(name.lower())]
        if len(matches) == 1:
            resolved.append(matches[0])
            continue
        if not matches:
            raise SystemExit(f"No style matches {name!r}.")
        raise SystemExit(f"{name!r} is ambiguous: " + ", ".join(m["style"] for m in matches))

    return resolved


def main() -> int:
    args = parse_args()
    configure_logging(PROJECT_DIR)

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_DIR / config_path
    config = load_config(config_path)
    if args.timeout:
        config = replace(config, generation_timeout_seconds=args.timeout)

    styles = resolve_styles(read_style_names(args), config.art_style_entries)

    if args.forecast_file:
        forecast = json.loads(Path(args.forecast_file).read_text())
    else:
        forecast = fetch_daily_forecast(config.location, config.weather)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_name = f"{stamp}-{_slugify(args.label)}" if args.label else stamp
    run_dir = config.resolve_path(PROJECT_DIR, config.output_dir) / "style_tests" / run_name
    state_dir = run_dir / "_state"
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"{len(styles)} styles | {forecast.get('datetime')} {forecast.get('condition')} "
          f"{forecast.get('temperature')} deg | parallel={args.parallel}")
    print(f"output: {run_dir}\n")

    # Serial planning: shared history, and each activity should differ from the last.
    prepared = []
    for index, style in enumerate(styles, start=1):
        item_dir = run_dir / f"{index:02d}_{_slugify(style['style'])[:48]}"
        item_dir.mkdir(parents=True, exist_ok=True)
        item_config = replace(
            config,
            output_dir=str(item_dir),
            state_dir=str(state_dir),
            art_style_entries=[style],
        )
        plan = prepare_generation(item_config, PROJECT_DIR, forecast_override=forecast)
        prepared.append((index, style, item_dir, plan))
        print(f"[plan {index}/{len(styles)}] {style['style']}\n         {plan.activity[:150]}")

    items: list[dict] = []
    manifest_path = run_dir / "manifest.json"

    def finish(index: int, style: dict, item_dir: Path, result) -> None:
        copied = None
        if not args.dry_run and Path(result.original_path).exists():
            # A flat copy next to the manifest, so all results sit in one folder.
            copied = run_dir / f"{index:02d}_{_slugify(style['style'])[:48]}.png"
            shutil.copy2(result.original_path, copied)
        items.append({
            "index": index,
            "style": style["style"],
            "outfit": style.get("outfit"),
            "props": style.get("props"),
            "universe": style.get("universe"),
            "activity": result.activity,
            "item_dir": str(item_dir),
            "image": str(copied) if copied else None,
        })
        items.sort(key=lambda entry: entry["index"])
        manifest_path.write_text(json.dumps(
            {"forecast": forecast, "dry_run": args.dry_run, "items": items},
            ensure_ascii=False, indent=2) + "\n")
        print(f"[done {index}/{len(styles)}] {style['style']}")

    print()
    if args.dry_run or args.parallel <= 1:
        for index, style, item_dir, plan in prepared:
            finish(index, style, item_dir, render_generation_plan(plan, dry_run=args.dry_run))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures = {
                executor.submit(render_generation_plan, plan, False): (index, style, item_dir)
                for index, style, item_dir, plan in prepared
            }
            for future in concurrent.futures.as_completed(futures):
                index, style, item_dir = futures[future]
                try:
                    finish(index, style, item_dir, future.result())
                except Exception as error:  # one failing style must not sink the run
                    print(f"[FAIL {index}/{len(styles)}] {style['style']}: {error}")
                    items.append({"index": index, "style": style["style"], "error": str(error)})

    print(f"\nmanifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
