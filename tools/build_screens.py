#!/usr/bin/env python3
"""Render the landing page's panel frames with the real dashboard renderer.

The simulated reTerminal on the site shows exactly what the panel would show:
every frame is drawn by `custom_components/foredogs/dashboard_render.py`, the
same code Home Assistant runs, fed with a made-up morning for each season. If
the renderer changes, rerun this and the site follows; it cannot drift into
showing something the code no longer does.

    python3 tools/build_screens.py            # writes site/screens/ and site/pictures/
    python3 tools/build_screens.py --check    # fail if a committed frame is stale

Besides the sixteen panel frames (four seasons, two languages, two pages) it
writes, per season, the raw picture as WebP and the picture area cut out of the
page 1 frame. The page's before/after slider puts those two on top of each
other, so the dithered half is the panel's own pixels, not a re-creation.

The pictures are the four README gallery frames. The weather in each scene
matches what the model was given when it drew that picture, so the header, the
forecast and the picture never disagree.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
GALLERY = ROOT / ".github" / "images"
SITE = ROOT / "site"
OUT = SITE / "screens"

# preview_dashboard.py already knows how to import the renderer outside Home
# Assistant. Load it as a module rather than duplicating that shim.
_spec = importlib.util.spec_from_file_location("_preview", ROOT / "tools" / "preview_dashboard.py")
_preview = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_preview)

_render = _preview._render
DashboardData = _preview.DashboardData
ForecastDay = _preview.ForecastDay
HourPoint = _preview.HourPoint
WasteEntry = _preview.WasteEntry
get_language = _preview.get_language

# The six inks of a Spectra 6 panel, in the renderer's own order and values.
# Frames are stored as a six-entry palette PNG, which is both smaller and what
# the page's "real panel" mode needs to swap colours.
PALETTE = list(_render.SPECTRA6)


@dataclass
class Scene:
    key: str
    day: date
    image: str
    style: str
    condition: str
    outside: float
    high: float
    low: float
    sunrise: str
    sunset: str
    humidity: float
    wind: float
    inside: float
    battery: float
    days_left: float
    # 24 hourly temperatures from 06:00, and {hour offset: mm} of rain.
    curve: list[float]
    rain: dict[int, float]
    hour_condition: str
    # Seven following days: (condition, high, low, mm or None).
    week: list[tuple[str, float, float, float | None]]
    # Days from the scene date to each bin.
    bins: tuple[int, int, int]


# In the order the year runs, spring to winter, as the page cycles them.
SCENES = [
    Scene(
        key="spring",
        day=date(2027, 4, 21),
        image="gallery-spring.jpg",
        style="comic book, bold ink lines and flat colour",
        condition="rainy",
        outside=8.2,
        high=14.1,
        low=6.3,
        sunrise="05:52",
        sunset="20:22",
        humidity=88.0,
        wind=24.0,
        inside=21.9,
        battery=64.0,
        days_left=19.0,
        curve=[
            6.4, 6.3, 6.8, 7.6, 8.9, 10.2, 11.4, 12.6,
            13.5, 14.1, 13.8, 12.9, 11.7, 10.9, 10.1, 9.4,
            8.6, 8.0, 7.5, 7.0, 6.6, 6.3, 6.0, 5.8,
        ],
        rain={1: 0.4, 2: 1.8, 3: 3.1, 4: 2.2, 5: 0.9, 9: 0.6, 10: 1.4, 11: 0.5},
        hour_condition="rainy",
        week=[
            ("lightning-rainy", 13.2, 7.1, 6.8),
            ("partlycloudy", 15.8, 6.4, None),
            ("sunny", 18.4, 7.9, None),
            ("sunny", 19.6, 9.2, None),
            ("rainy", 14.3, 8.8, 4.2),
            ("cloudy", 13.1, 7.4, None),
            ("partlycloudy", 16.0, 6.9, None),
        ],
        bins=(3, 8, 12),
    ),
    Scene(
        key="summer",
        day=date(2027, 7, 14),
        image="gallery-summer.jpg",
        style="watercolour with soft bleeding edges",
        condition="sunny",
        outside=21.6,
        high=31.2,
        low=19.4,
        sunrise="04:52",
        sunset="21:28",
        humidity=54.0,
        wind=9.0,
        inside=24.3,
        battery=43.0,
        days_left=13.0,
        curve=[
            19.6, 19.4, 20.8, 22.9, 24.9, 26.8, 28.4, 29.7,
            30.6, 31.2, 31.0, 30.3, 29.1, 27.6, 25.9, 24.4,
            23.0, 22.1, 21.3, 20.6, 20.0, 19.5, 19.1, 18.8,
        ],
        rain={},
        hour_condition="sunny",
        week=[
            ("sunny", 32.4, 20.1, None),
            ("partlycloudy", 29.8, 19.6, None),
            ("lightning-rainy", 26.2, 18.3, 8.4),
            ("rainy", 22.9, 16.2, 3.1),
            ("partlycloudy", 24.5, 15.8, None),
            ("sunny", 27.3, 16.4, None),
            ("sunny", 28.9, 17.7, None),
        ],
        bins=(2, 9, 16),
    ),
    Scene(
        key="autumn",
        day=date(2027, 10, 20),
        image="gallery-autumn.jpg",
        style="stained glass with lead came outlines",
        condition="windy",
        outside=7.1,
        high=12.3,
        low=6.2,
        sunrise="07:41",
        sunset="18:08",
        humidity=76.0,
        wind=38.0,
        inside=20.8,
        battery=21.0,
        days_left=6.0,
        curve=[
            7.4, 7.3, 7.6, 8.3, 9.2, 10.3, 11.2, 11.9,
            12.3, 12.2, 11.7, 11.0, 10.3, 9.6, 9.0, 8.5,
            8.1, 7.7, 7.3, 7.0, 6.8, 6.5, 6.3, 6.2,
        ],
        rain={13: 0.3, 14: 0.7, 15: 0.4},
        hour_condition="windy",
        week=[
            ("cloudy", 11.4, 5.9, None),
            ("pouring", 9.8, 6.1, 14.2),
            ("rainy", 10.6, 5.2, 3.8),
            ("partlycloudy", 12.9, 4.7, None),
            ("fog", 10.2, 3.9, None),
            ("sunny", 13.4, 4.1, None),
            ("cloudy", 11.8, 5.3, None),
        ],
        bins=(0, 4, 11),
    ),
    Scene(
        key="winter",
        day=date(2028, 1, 12),
        image="gallery-winter.jpg",
        style="mid-century children's book illustration",
        condition="snowy",
        outside=-3.4,
        high=-1.2,
        low=-5.1,
        sunrise="08:12",
        sunset="16:21",
        humidity=91.0,
        wind=11.0,
        inside=21.4,
        battery=88.0,
        days_left=27.0,
        curve=[
            -4.8, -5.0, -5.1, -4.6, -3.9, -3.1, -2.4, -1.8,
            -1.4, -1.2, -1.3, -1.7, -2.3, -2.9, -3.3, -3.6,
            -3.9, -4.1, -4.4, -4.6, -4.8, -5.0, -5.2, -5.4,
        ],
        rain={2: 0.3, 3: 0.6, 4: 0.8, 5: 0.5, 6: 0.2, 14: 0.2},
        hour_condition="snowy",
        week=[
            ("snowy", -0.4, -4.2, 2.1),
            ("cloudy", 0.8, -3.0, None),
            ("sunny", 1.6, -6.3, None),
            ("partlycloudy", 2.4, -2.8, None),
            ("snowy-rainy", 3.1, -0.4, 3.6),
            ("cloudy", 2.2, -1.5, None),
            ("sunny", 0.9, -5.8, None),
        ],
        bins=(1, 6, 19),
    ),
]

# Bin names come straight from the waste integration's sensors, so the panel
# never translates them. The demo does, so each language shows plausible ones.
BIN_LABELS = {
    "de": ("Restabfall", "Papier", "Gelber Sack"),
    "en": ("Rubbish", "Paper", "Recycling"),
}
BIN_KINDS = ("rest", "papier", "gelb")


def _pin_today(day: date) -> None:
    """Make the renderer's `date.today()` return the scene date.

    Two places in the renderer ask the clock rather than the data: the bin
    countdown and the forecast heading. Without this, a frame drawn in
    September would show a January date with "in 118 days" next to it.
    """

    class _SceneDate(date):
        @classmethod
        def today(cls) -> date:  # type: ignore[override]
            return day

    _render.date = _SceneDate


def build_data(scene: Scene, lang: str, page: int) -> DashboardData:
    language = get_language(lang)
    morning = datetime.combine(scene.day, datetime.min.time()).replace(hour=5, minute=45)
    start = morning.replace(hour=6, minute=0)

    hourly = [
        HourPoint(
            when=start + timedelta(hours=offset),
            temp=temp,
            precipitation=scene.rain.get(offset),
            condition=scene.hour_condition,
        )
        for offset, temp in enumerate(scene.curve)
    ]

    forecast = [
        ForecastDay(
            label="",
            condition=condition,
            temp_high=high,
            temp_low=low,
            precipitation=mm,
            when=scene.day + timedelta(days=offset),
            offset=offset,
        )
        for offset, (condition, high, low, mm) in enumerate(scene.week, start=1)
    ]

    rise_h, rise_m = map(int, scene.sunrise.split(":"))
    set_h, set_m = map(int, scene.sunset.split(":"))
    minutes = (set_h * 60 + set_m) - (rise_h * 60 + rise_m)

    rain_hours = sorted(scene.rain)
    rain_window = ""
    if rain_hours:
        rain_window = language.rain_window(6 + rain_hours[0], 6 + rain_hours[-1] + 1)

    return DashboardData(
        now=morning,
        language=language,
        outside_temp=scene.outside,
        outside_condition=scene.condition,
        outside_humidity=scene.humidity,
        inside_temp=scene.inside,
        inside_humidity=48.0,
        wind_speed=scene.wind,
        battery=scene.battery,
        battery_days_left=scene.days_left,
        battery_voltage=3.9,
        sun_rise=scene.sunrise,
        sun_set=scene.sunset,
        temp_high=scene.high,
        temp_low=scene.low,
        rain_total=round(sum(scene.rain.values()), 1) or None,
        rain_window=rain_window,
        daylight_hours=f"{minutes // 60}h {minutes % 60:02d}m",
        waste=[
            WasteEntry(label=label, kind=kind, due=scene.day + timedelta(days=days))
            for label, kind, days in zip(BIN_LABELS[lang], BIN_KINDS, scene.bins)
        ],
        hourly=hourly,
        forecast=forecast,
        image_path=GALLERY / scene.image,
        page=page,
        page_count=2,
    )


def to_palette_png(path: Path) -> bytes:
    """Re-encode a rendered frame as a six-colour palette PNG.

    The renderer writes RGB. Every pixel is already one of the six inks, so the
    conversion is exact; anything else means the renderer changed its palette
    and the page's colour swap would silently miss it.
    """
    rgb = Image.open(path).convert("RGB")
    colours = {colour for _, colour in rgb.getcolors(maxcolors=1 << 16) or []}
    stray = colours - set(PALETTE)
    if stray:
        raise SystemExit(f"{path.name}: colours outside the panel palette: {sorted(stray)[:5]}")

    lookup = {colour: index for index, colour in enumerate(PALETTE)}
    indexed = Image.new("P", rgb.size)
    indexed.putpalette([channel for colour in PALETTE for channel in colour])
    indexed.putdata([lookup[pixel] for pixel in rgb.getdata()])

    buffer = io.BytesIO()
    indexed.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def write_pictures(scene: Scene, frame: bytes, out: Path) -> None:
    """The raw picture next to the same area as the panel paints it."""
    out.mkdir(parents=True, exist_ok=True)
    x, y, w, h = _render.IMAGE_X, _render.IMAGE_Y, _render.IMAGE_W, _render.IMAGE_H

    painted = Image.open(io.BytesIO(frame)).crop((x, y, x + w, y + h))
    painted.save(out / f"{scene.key}-panel.png", "PNG", optimize=True)

    raw = Image.open(GALLERY / scene.image).convert("RGB")
    # Twice the panel's size, cropped the way the renderer crops, so the two
    # halves of the slider line up to the pixel. The gallery reuses it: the
    # crop loses a few pixels of a 16:9 frame and saves a second download.
    _render.fit_crop(raw, w * 2, h * 2).save(out / f"{scene.key}.webp", "WEBP", quality=74, method=6)
    # The gallery card: at most ~300 CSS px wide, so 640 px covers a 2x screen
    # at a quarter of the weight. Small enough to load eagerly, which is the
    # point: lazy cards under a scroll reveal loaded late or not at all.
    _render.fit_crop(raw, 640, 358).save(out / f"{scene.key}-card.webp", "WEBP", quality=68, method=6)


def write_readme_gallery(scene: Scene) -> None:
    """The README's copy of a gallery picture: 16:9, with rounded corners.

    The four source frames are not all the same shape (the model returned one
    at 1200x720), and GitHub strips any CSS, so size and corners are baked in.
    WebP keeps the transparent corners at a fraction of a PNG's weight.
    """
    raw = Image.open(GALLERY / scene.image).convert("RGB")
    card = _render.fit_crop(raw, 1200, 675).convert("RGBA")
    # Drawn at 4x and shrunk, so the curve is antialiased.
    scale = 4
    mask = Image.new("L", (card.width * scale, card.height * scale), 0)
    from PIL import ImageDraw
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, mask.width - 1, mask.height - 1], radius=28 * scale, fill=255)
    card.putalpha(mask.resize(card.size, Image.Resampling.LANCZOS))
    card.save(GALLERY / f"gallery-{scene.key}.webp", "WEBP", quality=82, method=6)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail when a committed frame differs")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    scratch = Path("/tmp/foredogs-screens")
    scratch.mkdir(exist_ok=True)

    manifest = []
    stale = []
    for scene in SCENES:
        _pin_today(scene.day)
        for lang in ("en", "de"):
            for page in (1, 2):
                name = f"{scene.key}-{lang}-p{page}.png"
                raw = scratch / name
                _render.render_dashboard(build_data(scene, lang, page), raw, dither_photo=True)
                encoded = to_palette_png(raw)
                target = args.out / name
                if args.check:
                    if not target.exists() or target.read_bytes() != encoded:
                        stale.append(name)
                else:
                    target.write_bytes(encoded)
                    if lang == "en" and page == 1:
                        write_pictures(scene, encoded, SITE / "pictures")
                        write_readme_gallery(scene)
                print(f"{name}: {len(encoded) / 1024:.1f} KB")
        manifest.append(
            {
                "key": scene.key,
                "date": scene.day.isoformat(),
                "style": scene.style,
                "condition": scene.condition,
                "high": scene.high,
                "low": scene.low,
                "image": scene.image,
            }
        )

    manifest_text = json.dumps(manifest, indent=2) + "\n"
    manifest_path = args.out / "scenes.json"
    if args.check:
        if not manifest_path.exists() or manifest_path.read_text() != manifest_text:
            stale.append("scenes.json")
        if stale:
            print(f"stale: {', '.join(stale)} — run tools/build_screens.py", file=sys.stderr)
            return 1
        return 0

    manifest_path.write_text(manifest_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
