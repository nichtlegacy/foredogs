"""Composite dashboard renderer for the reTerminal E1002 kitchen display.

Renders an 800x480 PNG that combines the daily Foredogs image with live Home
Assistant data. This module owns the shared vocabulary — palette, fonts, weather
icons, dithering, the header and the battery gauge — plus page 1. Pages 2 and 3
live in `dashboard_pages.py` and are dispatched from `render_dashboard`.

Page 1 (home), layout C:

    +--------------------------------------------+
    |  20:47  Fr 26.07        18 deg Teils wolkig|  header, 64px
    +------------------------------+-------------+
    |                              |  DRINNEN    |
    |                              |   24.8 deg  |
    |      Foredogs image          |   60%       |
    |      590 x 330               +-------------+
    |                              | RESTABFALL  |
    |                              |  Mi 29.07   |
    |                              +-------------+
    |                              | BIO         |
    +------------------------------+-------------+
    | MORGEN 21 Regen | DI 27 Sonne | MI 31 Sonne|  forecast band
    +--------------------------------------------+

Page 2 is the weather detail (24h curve plus a 7-day band), page 3 a full-bleed
photo. The device cycles them with its three buttons and every second wake; see
`esp/e1002-kitchen.yaml`.

Why a composite instead of ESPHome lambdas:
  - the image and the data update on completely different schedules. The
    Gemini image costs money and is generated once a day; weather and waste
    dates need to be current every 30 minutes. Compositing here means the
    cheap part can re-run often while the expensive part stays cached.
  - ESPHome cannot read HA forecast *arrays* at all, only scalar states and
    attributes, so the 3-day band would otherwise need ~12 template sensors.
  - Pillow layout can be iterated without reflashing the device.

The renderer never calls Gemini. It reads whatever image the generator last
produced and draws around it.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from .languages import DEFAULT_LANGUAGE, Language, get_language

try:
    import numpy as np

    _HAVE_NUMPY = True
except ImportError:  # pragma: no cover - depends on the host environment
    # Atkinson dithering needs numpy for a per-pixel error-diffusion loop.
    # Rather than pin numpy in manifest.json — which risks fighting the version
    # Home Assistant already ships — fall back to Pillow's Floyd-Steinberg.
    # Slightly more colour fringing, still perfectly usable.
    _HAVE_NUMPY = False

logger = logging.getLogger("foredogs")

# --- canvas geometry ---------------------------------------------------------
# The panel is 800x480 and cannot be scaled, so every coordinate is absolute.
WIDTH = 800
HEIGHT = 480

HEADER_H = 64
FOOTER_H = 86  # four forecast days need a little more room than three
# Pages 2 and 3 carry only the battery gauge and a couple of status pairs, so
# they give the saved 42px back to their content.
SLIM_FOOTER_H = 44
# Wide enough for "GELBER SACK" and "in 4 Tagen" on the same row.
SIDEBAR_W = 210

IMAGE_X = 0
IMAGE_Y = HEADER_H
IMAGE_W = WIDTH - SIDEBAR_W
IMAGE_H = HEIGHT - HEADER_H - FOOTER_H

SIDEBAR_X = IMAGE_W
SIDEBAR_Y = HEADER_H
SIDEBAR_H = IMAGE_H

FOOTER_Y = HEIGHT - FOOTER_H

PAD = 12
GAP = 6  # gutter between stacked sidebar blocks
# Two text rows plus padding. Three bins have to fit the column, so this is the
# floor below which a block stops being legible rather than a target height.
MIN_WASTE_BLOCK_H = 58

# --- Spectra 6 palette -------------------------------------------------------
# These are the *device* colours. Anything drawn must use one of them exactly,
# otherwise quantization will shift it somewhere unintended.
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (200, 0, 0)
GREEN = (0, 150, 0)
BLUE = (0, 0, 200)
YELLOW = (255, 230, 0)

SPECTRA6 = [BLACK, WHITE, RED, GREEN, BLUE, YELLOW]

FORECAST_DAYS = 4
# Page 2 has the full width for its band, so it can carry a proper week.
FORECAST_DAYS_LONG = 7
# Hours plotted in the page 2 curve. 24 keeps one column per hour readable at
# ~31 px, which is enough for a temperature label every third hour.
HOURLY_POINTS = 24

# Bin colours. Spectra 6 has no brown, so bio gets green and paper gets blue.
BIN_COLORS = {
    "rest": BLACK,
    "bio": GREEN,
    "papier": BLUE,
    "gelb": YELLOW,
}

# Yellow is far too light to carry white text, so that one bin flips to black
# lettering. Everything else reads fine reversed out in white.
BIN_TEXT_COLORS = {
    "gelb": BLACK,
}


@dataclass
class WasteEntry:
    """One upcoming waste collection."""

    label: str
    kind: str  # key into BIN_COLORS
    due: date

    @property
    def days_until(self) -> int:
        return (self.due - date.today()).days

    @property
    def urgent(self) -> bool:
        """True when the bin must go out today or tomorrow."""
        return self.days_until <= 1

    def when_text(self, language: Language) -> str:
        return language.waste_when(self.days_until)

    def date_text(self, language: Language) -> str:
        # Full weekday name: the sidebar has the width for it, and "Donnerstag"
        # is read without the beat of decoding that "Do" costs.
        return language.waste_date(self.due)


@dataclass
class ForecastDay:
    """One day in the outlook band."""

    #: Pre-formatted column heading. Only used when `when` is unset — prefer
    #: setting the date and letting the language decide, so a collector cannot
    #: bake one language's weekday into data that is rendered in another.
    label: str
    condition: str
    temp_high: float | None
    temp_low: float | None
    precipitation: float | None = None
    #: The day itself. When set, the heading is derived from it at draw time.
    when: date | None = None
    #: Days from today, so "Tomorrow" can replace the weekday name.
    offset: int | None = None

    def heading(self, language: Language) -> str:
        """The column heading, in the active language."""
        if self.when is None:
            return self.label
        return language.forecast_label(
            self.offset if self.offset is not None else 2,
            self.when,
        )


@dataclass
class HourPoint:
    """One hour in the page 2 curve."""

    when: datetime
    temp: float | None
    precipitation: float | None = None
    condition: str = ""


@dataclass
class PhotoInfo:
    """A photo for page 3, plus whatever caption metadata came with it."""

    path: Path
    taken: datetime | None = None
    caption: str = ""
    # Where the photo came from, split so the page can render it in the active
    # language: kind is "immich" or "folder", name is the album or directory.
    # "Immich" is a product name and is not translated; "Ordner"/"Folder" is.
    source_kind: str = ""
    source_name: str = ""
    # Stable identity of the asset, so the fingerprint changes when the photo
    # does. The path alone is not enough: the cache reuses one filename.
    asset_id: str = ""


@dataclass
class DashboardData:
    """Everything the renderer needs. Assembled by the caller from HA states."""

    now: datetime
    outside_temp: float | None = None
    outside_condition: str = ""
    outside_humidity: float | None = None
    inside_temp: float | None = None
    inside_humidity: float | None = None
    wind_speed: float | None = None
    uv_index: float | None = None
    precipitation: float | None = None
    precipitation_probability: float | None = None
    feels_like: float | None = None
    sun_rise: str = ""  # "05:42", already local
    sun_set: str = ""
    # Pre-formatted by the caller from the hourly forecast, e.g. "22:00" or
    # "Mo 14:00". Empty when nothing is expected within the lookahead window.
    # Today's own high and low, from the daily forecast rather than the current
    # reading: on a panel drawn once in the morning, "what will today be" is the
    # useful number and "what is it right now" is not.
    temp_high: float | None = None
    temp_low: float | None = None
    # Rain summed over the day, plus the window it falls in ("14-17 Uhr").
    # Unlike a countdown this is still correct in the evening.
    rain_total: float | None = None
    rain_window: str = ""
    # "15h 42m", derived from sunrise/sunset so the panel does not have to.
    daylight_hours: str = ""
    # Page indicator, for when the device gains multiple screens: left button
    # back, centre deep sleep, right button forward.
    page: int = 1
    page_count: int = 1
    battery: float | None = None
    battery_voltage: float | None = None
    # Days until empty at the current discharge rate, or None when the history
    # cannot support an estimate. Drawn under the gauge.
    battery_days_left: float | None = None
    waste: list[WasteEntry] = field(default_factory=list)
    forecast: list[ForecastDay] = field(default_factory=list)
    image_path: Path | None = None
    # --- page 2 -----------------------------------------------------------
    # The hourly curve, starting at the current hour. Empty on installs whose
    # weather integration publishes no hourly forecast, in which case page 2
    # falls back to the day band alone.
    hourly: list[HourPoint] = field(default_factory=list)
    # Up to FORECAST_DAYS_LONG days. Page 1 slices the first FORECAST_DAYS off
    # the same list, so only one forecast fetch is needed for both pages.
    # --- page 3 -----------------------------------------------------------
    photo: PhotoInfo | None = None
    # Set when the photo source was configured but unreachable, so the page can
    # say *why* it is empty instead of just showing a placeholder box.
    photo_error: str = ""
    # --- presentation -----------------------------------------------------
    # Which language the drawn labels use. Defaults to German, the language the
    # panel was built in, so an install that never passes one is unaffected.
    #
    # It lives on the data rather than in a module global because a single Home
    # Assistant can render several panels, and because a global would make the
    # renderer's output depend on call order.
    language: Language = field(default_factory=lambda: get_language(DEFAULT_LANGUAGE))


# --- fonts -------------------------------------------------------------------
# The Home Assistant OS container ships no fonts at all — no DejaVu, nothing in
# /usr/share/fonts — so a TTF has to be bundled or Pillow silently falls back to
# a tiny unscalable bitmap font. Inter lives in foredogs_data/fonts/; the system
# paths below are only there so the local preview tool works on a dev machine.
# custom_components/foredogs/ -> custom_components/ -> repository root.
_REPO_FONTS = Path(__file__).resolve().parents[2] / "foredogs_data" / "fonts"

_FONT_CANDIDATES_BOLD = [
    "/config/foredogs_data/fonts/Inter-Bold.ttf",
    "/config/foredogs_data/fonts/Inter-SemiBold.ttf",
    # Same fonts inside the repo, so the local preview matches the panel.
    str(_REPO_FONTS / "Inter-Bold.ttf"),
    str(_REPO_FONTS / "Inter-SemiBold.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

_FONT_CANDIDATES_REGULAR = [
    "/config/foredogs_data/fonts/Inter-Medium.ttf",
    "/config/foredogs_data/fonts/Inter-Regular.ttf",
    str(_REPO_FONTS / "Inter-Medium.ttf"),
    str(_REPO_FONTS / "Inter-Regular.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Find a usable TrueType font at the requested size and weight."""
    candidates = _FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES_REGULAR
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue

    # Everything on the panel is sized for a real font; the bitmap fallback
    # would be unreadable, so make the cause obvious in the log.
    logger.error(
        "No TrueType font found — install one in /config/foredogs_data/fonts/. "
        "Falling back to Pillow's bitmap font; text will look wrong."
    )
    return ImageFont.load_default(size)


class _FontSet:
    """Lazily built font set, cached across renders."""

    def __init__(self) -> None:
        # Every size is bold. On a 6-colour panel there is no antialiasing to
        # carry a regular weight — thin strokes dither into broken pixels — and
        # a mixed-weight sidebar reads as accidental rather than hierarchical.
        # Hierarchy comes from size and colour instead.
        self.xl = _load_font(64, bold=True)
        self.l = _load_font(34, bold=True)  # noqa: E741 - matches the visual scale naming
        self.m = _load_font(24, bold=True)
        self.s = _load_font(18, bold=True)
        self.xs = _load_font(14, bold=True)
        self.xxs = _load_font(11, bold=True)  # metric labels in the header


_FONTS: _FontSet | None = None


def fonts() -> _FontSet:
    global _FONTS
    if _FONTS is None:
        _FONTS = _FontSet()
    return _FONTS


# --- drawing helpers ---------------------------------------------------------
def degrees(value: float) -> str:
    """A temperature rounded to whole degrees, for drawing.

    Plain `:.0f` keeps the sign of a value that rounds to zero, so -0.4 would be
    drawn as "-0". On a winter morning that is a real reading, and "-0" is not a
    temperature anyone uses.
    """
    return str(round(value))


def draw_text(draw, xy, text, font, fill=BLACK, anchor="la") -> None:
    """Draw text without anti-aliasing artefacts bleeding into the palette."""
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def text_width(draw, text, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def text_height(draw, text, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[3] - box[1]


def condition_label(language: Language, condition: str) -> str:
    return language.condition(condition)


# --- weather icons -----------------------------------------------------------
# Drawn as vector shapes rather than loaded from an icon font. A 6-colour panel
# has no greyscale, so anti-aliased glyphs would dither into speckle; flat
# filled shapes in palette colours stay crisp at any size.
def _icon_sun(draw, cx: int, cy: int, r: int, color=YELLOW) -> None:
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color, outline=BLACK)
    # Eight rays, drawn as short spokes clear of the disc.
    for i in range(8):
        angle = math.pi * i / 4
        x1 = cx + int(math.cos(angle) * (r + 3))
        y1 = cy + int(math.sin(angle) * (r + 3))
        x2 = cx + int(math.cos(angle) * (r + 7))
        y2 = cy + int(math.sin(angle) * (r + 7))
        draw.line([x1, y1, x2, y2], fill=color, width=2)


def _icon_moon(draw, cx: int, cy: int, r: int, color=YELLOW, bg=BLACK) -> None:
    """Crescent moon: a filled disc with an offset disc punched out of it.

    Three details decide whether this reads as a moon or as a bitten circle at
    fourteen pixels across:

    * The punch is offset up as well as right. A purely horizontal offset
      leaves a symmetric lune that reads as half a disc; tilting it gives the
      pose everyone recognises as a moon.
    * The punch carries no outline. Outlining it draws a second circle, and the
      result reads as two overlapping discs rather than one crescent.
    * `bg` has to match whatever the icon sits on — BLACK in the header, WHITE
      in the sidebar. Getting it wrong does not fail, it just paints a bite in
      the wrong colour.
    """
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color, outline=BLACK)
    offset_x = max(2, round(r * 0.75))
    offset_y = -max(1, round(r * 0.35))
    draw.ellipse(
        [cx - r + offset_x, cy - r + offset_y, cx + r + offset_x, cy + r + offset_y],
        fill=bg,
    )


def _icon_cloud(draw, cx: int, cy: int, r: int, color=None, outline=BLACK) -> None:
    """Three overlapping circles plus a base, i.e. the usual cloud silhouette.

    Fills grey-blue rather than white by default: a white cloud on the white
    footer is invisible except for its outline, and the outline alone is lost
    once the panel dithers.
    """
    if color is None:
        color = BLUE

    draw.ellipse([cx - r, cy - r // 2, cx, cy + r // 2], fill=color, outline=outline)
    draw.ellipse(
        [cx - r // 2, cy - r, cx + r // 2, cy + r // 3],
        fill=color,
        outline=outline,
    )
    draw.ellipse([cx, cy - r // 3, cx + r, cy + r // 2], fill=color, outline=outline)
    draw.rectangle([cx - r, cy, cx + r, cy + r // 2], fill=color, outline=None)
    draw.line([cx - r, cy + r // 2, cx + r, cy + r // 2], fill=outline, width=1)







def draw_weather_icon(
    draw,
    condition: str,
    cx: int,
    cy: int,
    size: int,
    on_dark: bool = False,
) -> None:
    """Render the icon for an HA weather condition, centred on (cx, cy).

    `on_dark` flips the cloud and detail colours for the black header bar,
    where the footer's blue-on-white scheme would disappear.
    """
    r = max(6, size // 2)
    cloud = WHITE if on_dark else BLUE
    outline = WHITE if on_dark else BLACK
    detail = WHITE if on_dark else BLACK

    if condition == "clear-night":
        # The crescent is punched in the background colour, so it follows
        # on_dark. On the white forecast band a black punch drew a bite out of
        # the moon instead of a crescent.
        _icon_moon(draw, cx, cy, r - 2, bg=BLACK if on_dark else WHITE)
    elif condition == "sunny":
        _icon_sun(draw, cx, cy, r - 2)
    elif condition == "partlycloudy":
        # Sun peeking out behind the cloud.
        _icon_sun(draw, cx + r // 2, cy - r // 2, r // 2)
        _icon_cloud(draw, cx - 2, cy + 2, r - 2, color=cloud, outline=outline)
    elif condition == "cloudy":
        _icon_cloud(draw, cx, cy, r, color=cloud, outline=outline)
    elif condition in ("rainy", "snowy-rainy", "pouring"):
        heavy = condition == "pouring"
        _icon_cloud(draw, cx, cy - (r - 2) // 3, r - 2, color=cloud, outline=outline)
        drops = 4 if heavy else 3
        for i in range(drops):
            x = cx - (r - 2) + int(2 * (r - 2) * (i + 0.5) / drops)
            y = cy + (r - 2) // 2
            draw.line([x, y, x - 2, y + (8 if heavy else 6)], fill=detail, width=2)
    elif condition in ("snowy", "hail"):
        _icon_cloud(draw, cx, cy - (r - 2) // 3, r - 2, color=cloud, outline=outline)
        for i in range(3):
            x = cx - (r - 2) // 2 + i * ((r - 2) // 2)
            y = cy + (r - 2) // 2 + 4
            draw.line([x - 3, y, x + 3, y], fill=detail, width=1)
            draw.line([x, y - 3, x, y + 3], fill=detail, width=1)
    elif condition in ("lightning", "lightning-rainy"):
        _icon_cloud(draw, cx, cy - (r - 2) // 3, r - 2, color=cloud, outline=outline)
        b = r - 2
        draw.polygon(
            [
                (cx + 1, cy + b // 2),
                (cx - 5, cy + b // 2 + 9),
                (cx - 1, cy + b // 2 + 9),
                (cx - 5, cy + b // 2 + 18),
                (cx + 7, cy + b // 2 + 6),
                (cx + 2, cy + b // 2 + 6),
            ],
            fill=YELLOW,
            outline=outline,
        )
    elif condition in ("fog", "hazy"):
        for i in range(4):
            y = cy - r + i * (r // 2)
            inset = (i % 2) * 4
            draw.line([cx - r + inset, y, cx + r - inset, y], fill=cloud, width=3)
    elif condition in ("windy", "windy-variant", "hurricane", "tornado"):
        for i, length in enumerate((r, r + 4, r - 2)):
            y = cy - r // 2 + i * (r // 2)
            draw.line([cx - r, y, cx - r + length, y], fill=cloud, width=3)
            draw.arc(
                [cx - r + length - 5, y - 6, cx - r + length + 6, y + 6],
                start=270,
                end=110,
                fill=cloud,
                width=3,
            )
    else:
        _icon_cloud(draw, cx, cy, r, color=cloud, outline=outline)


def temperature_color(temp: float | None) -> tuple[int, int, int]:
    """Map a temperature onto the palette.

    Only three buckets: the panel has six colours total and a finer gradient
    would not survive quantization or read at a glance.
    """
    if temp is None:
        return BLACK
    if temp >= 28:
        return RED
    if temp <= 5:
        return BLUE
    return BLACK


def condition_short(language: Language, condition: str, max_width: int, draw) -> str:
    """Fit a condition name into a forecast column.

    Four columns leave roughly 150px each, which "Gewitterregen" overruns. Try
    the full label, then a known abbreviation, then truncate.
    """
    label = condition_label(language, condition)
    if text_width(draw, label, fonts().xs) <= max_width:
        return label

    short = language.condition_short(condition)
    if short and text_width(draw, short, fonts().xs) <= max_width:
        return short

    text = short or label
    while text and text_width(draw, text + ".", fonts().xs) > max_width:
        text = text[:-1]
    return (text + ".") if text else ""


# --- sections ----------------------------------------------------------------
def draw_header(draw, data: DashboardData) -> None:
    """Date on the left, today's weather summary on the right.

    Built for a panel redrawn once a day, right after the morning image is
    generated. That rules out anything time-of-day dependent: a clock would be
    wrong within the hour, and "next rain in 1h" is nonsense by lunchtime. What
    stays true all day is the date and the day's forecast — high and low, how
    much rain and when, wind.

    Black rather than blue: blue is one of only six colours and is worth saving
    for data (the paper bin, cold temperatures). A black bar also gives white
    text far more contrast on a reflective panel.
    """
    draw.rectangle([0, 0, WIDTH, HEADER_H], fill=BLACK)

    # --- left: the date on one line ---
    date_text = data.language.header_date(data.now)
    draw_text(draw, (PAD + 2, HEADER_H // 2 + 1), date_text, fonts().m, WHITE, anchor="lm")
    left_end = PAD + 2 + text_width(draw, date_text, fonts().m)

    # --- right: the day's high and low, with the condition icon beside them ---
    right_edge = WIDTH - PAD - 2
    if data.temp_high is not None:
        high = f"{degrees(data.temp_high)}\u00b0"
        # Yellow for hot, blue for freezing, white otherwise — three states that
        # read instantly without a legend.
        if data.temp_high >= 25:
            temp_color = YELLOW
        elif data.temp_high <= 0:
            temp_color = BLUE
        else:
            temp_color = WHITE
        draw_text(draw, (right_edge, HEADER_H // 2 + 1), high, fonts().l, temp_color, anchor="rm")
        right_edge -= text_width(draw, high, fonts().l) + 10

        if data.temp_low is not None:
            # No slash: at this size the separator reads as part of the number.
            # The size difference already says which is the high.
            low = f"{degrees(data.temp_low)}\u00b0"
            draw_text(draw, (right_edge, HEADER_H // 2 + 7), low, fonts().s, WHITE, anchor="rm")
            right_edge -= text_width(draw, low, fonts().s) + 16

    if data.outside_condition:
        label = condition_label(data.language, data.outside_condition)
        draw_text(draw, (right_edge, HEADER_H // 2 + 1), label, fonts().s, WHITE, anchor="rm")
        right_edge -= text_width(draw, label, fonts().s) + 14

        draw_weather_icon(
            draw, data.outside_condition, right_edge - 18, HEADER_H // 2, 34, on_dark=True
        )
        right_edge -= 44

    # --- middle: the day's other numbers ---
    # Label above value, so each is self-explanatory. Ordered most- to
    # least-useful; the tail is dropped when the gap runs out.
    metrics: list[tuple[str, str]] = []

    # Rain as a total plus the window it falls in. "4.2 mm  14-17 Uhr" answers
    # "coat, and when" in one line, which a bare percentage cannot — and unlike
    # a countdown it is still correct in the evening.
    if data.rain_total is not None and data.rain_total > 0.1:
        value = f"{data.rain_total:.1f} mm"
        if data.rain_window:
            value = f"{value}  {data.rain_window}"
        metrics.append((data.language.label("RAIN"), value))
    elif data.precipitation_probability is not None and data.precipitation_probability > 0:
        metrics.append((data.language.label("RAIN"), f"{data.precipitation_probability:.0f}%"))

    if data.wind_speed is not None:
        metrics.append((data.language.label("WIND"), f"{data.wind_speed:.0f} km/h"))
    if data.outside_humidity is not None:
        metrics.append((data.language.label("HUMIDITY"), f"{data.outside_humidity:.0f}%"))
    if data.uv_index is not None and data.uv_index >= 3:
        # Below 3 the UV index is not actionable.
        metrics.append((data.language.label("UV"), f"{data.uv_index:.0f}"))

    available = right_edge - left_end - 40
    if metrics and available > 90:
        # Columns sized to their own content: "LUFTFEUCHTIGKEIT" is far wider
        # than "UV 4", and a fixed width would either clip it or waste the gap.
        gutter = 26
        widths = [
            max(text_width(draw, label, fonts().xxs), text_width(draw, value, fonts().s))
            for label, value in metrics
        ]

        count = 0
        used = 0
        for w in widths:
            need = w if count == 0 else used + gutter + w
            if need > available:
                break
            used = need
            count += 1

        if count:
            mx = left_end + 24 + (available - used) // 2
            for i in range(count):
                label, value = metrics[i]
                draw_text(draw, (mx, 13), label, fonts().xxs, WHITE)
                draw_text(draw, (mx, 30), value, fonts().s, WHITE)
                mx += widths[i] + gutter


def _draw_image(canvas: Image.Image, draw, data: DashboardData) -> None:
    """Place the daily image, cropped to the available area."""
    if data.image_path and Path(data.image_path).exists():
        try:
            art = Image.open(data.image_path).convert("RGB")
            art = fit_crop(art, IMAGE_W, IMAGE_H)
            canvas.paste(art, (IMAGE_X, IMAGE_Y))
            return
        except OSError:
            logger.warning("Could not open dashboard image at %s", data.image_path)

    # Placeholder so a missing image is obvious rather than looking like a
    # rendering bug.
    draw.rectangle(
        [IMAGE_X, IMAGE_Y, IMAGE_X + IMAGE_W - 1, IMAGE_Y + IMAGE_H - 1],
        fill=WHITE,
        outline=BLACK,
        width=2,
    )
    draw_text(
        draw,
        (IMAGE_X + IMAGE_W // 2, IMAGE_Y + IMAGE_H // 2),
        "kein Bild",
        fonts().m,
        BLACK,
        anchor="mm",
    )


def _draw_sidebar(draw, data: DashboardData) -> None:
    """Indoor climate on top, then one block per upcoming waste collection.

    Block heights are computed so the stack fills the sidebar exactly — a
    partially filled column with a white gap at the bottom looks like a bug.
    """
    x0 = SIDEBAR_X
    y = SIDEBAR_Y
    bottom = SIDEBAR_Y + SIDEBAR_H

    # --- daylight ---
    # Replaced the indoor temperature here. On a panel drawn once at 05:30, an
    # indoor reading is a snapshot of the coldest moment of the day and wrong by
    # breakfast. Sunrise and sunset shift daily, are exactly what you want to
    # know in the morning, and are still correct at midnight.
    daylight_h = 90
    draw.rectangle([x0, y, WIDTH - 1, y + daylight_h], fill=WHITE, outline=BLACK, width=2)
    draw_text(draw, (x0 + PAD, y + 7), data.language.label("DAYLIGHT"), fonts().xs, BLACK)

    if data.sun_rise and data.sun_set:
        # A sun and a crescent instead of up and down arrows. Both say the same
        # thing, but the icons say it without being read — which is what this
        # panel is for, being understood from across the room in a glance.
        #
        # The sun's rays reach r + 7, so the disc has to sit far enough in that
        # they do not clip the sidebar edge.
        # Both icons share a radius, so neither looks like the other's
        # afterthought. The sun's rays reach r + 7, which sets the left inset.
        icon_r = 9
        icon_cx = x0 + PAD + icon_r + 7
        text_x = icon_cx + icon_r + 12

        # Centre each icon on the ink of its time, not on the nominal line box.
        # text_height() drops the top bearing, so adding half of it to the
        # ascender line puts the icon a couple of pixels above the digits.
        def _ink_centre_y(text: str, top: int) -> int:
            box = draw.textbbox((text_x, top), text, font=fonts().m, anchor="la")
            return (box[1] + box[3]) // 2

        rise_cy = _ink_centre_y(data.sun_rise, y + 26)
        set_cy = _ink_centre_y(data.sun_set, y + 54)

        _icon_sun(draw, icon_cx, rise_cy, icon_r)
        # White punch: the sidebar is white, unlike the header this icon was
        # originally drawn for.
        _icon_moon(draw, icon_cx, set_cy, icon_r, bg=WHITE)

        draw_text(draw, (text_x, y + 26), data.sun_rise, fonts().m, RED)
        draw_text(draw, (text_x, y + 54), data.sun_set, fonts().m, BLUE)

        if data.daylight_hours:
            draw_text(
                draw,
                (WIDTH - PAD, y + 7),
                data.daylight_hours,
                fonts().xs,
                BLACK,
                anchor="ra",
            )
    else:
        draw_text(draw, (x0 + PAD, y + 32), "--:--", fonts().m, BLACK)

    y += daylight_h + GAP

    # --- waste blocks ---
    # All configured bins should be visible; with three of them the column has
    # to divide evenly rather than fit "as many as possible" at a fixed minimum
    # height, which previously dropped the third bin off the bottom.
    available = bottom - y
    if available <= 0 or not data.waste:
        return

    count = len(data.waste)
    # Only start dropping bins if even a squeezed block would be unreadable.
    while count > 1 and (available - GAP * (count - 1)) // count < MIN_WASTE_BLOCK_H:
        count -= 1

    if count < len(data.waste):
        logger.info(
            "Sidebar fits %d of %d waste blocks; showing the soonest",
            count,
            len(data.waste),
        )

    block_h = (available - GAP * (count - 1)) // count

    for i, entry in enumerate(data.waste[:count]):
        # The last block absorbs any rounding remainder.
        height = block_h if i < count - 1 else bottom - y
        _draw_waste_block(draw, x0, y, height, entry, data.language)
        y += height + GAP


def _draw_waste_block(
    draw, x0: int, y: int, height: int, entry: WasteEntry, language: Language
) -> None:
    """One bin. Urgent pickups invert to red so they read from across the room."""
    bg = RED if entry.urgent else BIN_COLORS.get(entry.kind, BLACK)
    # Yellow cannot carry white text; that bin gets black lettering instead.
    fg = WHITE if entry.urgent else BIN_TEXT_COLORS.get(entry.kind, WHITE)
    draw.rectangle([x0, y, WIDTH - 1, y + height], fill=bg)

    if entry.urgent:
        # Double border makes "tomorrow" impossible to skim past.
        draw.rectangle([x0, y, WIDTH - 1, y + height], outline=BLACK, width=3)

    text_x = x0 + PAD

    # Two rows only. Three stacked lines do not fit the block height without
    # colliding, so the countdown shares the label's row, right-aligned:
    #
    #   RESTABFALL        in 3 Tagen
    #   Mi 29.07.
    #
    # For urgent pickups the countdown swaps to the big row instead, because
    # "MORGEN" matters more than the exact date at that point.
    right_text = (
        entry.date_text(language) if entry.urgent else entry.when_text(language)
    )
    big = entry.when_text(language) if entry.urgent else entry.date_text(language)

    # The label is truncated rather than allowed to run into the right-hand
    # countdown — "GELBER SACKin 4 Tagen" is worse than "GELBER S…".
    right_w = text_width(draw, right_text, fonts().xxs)
    label_budget = (WIDTH - PAD - right_w - 8) - text_x
    label = entry.label.upper()
    while label and text_width(draw, label, fonts().xxs) > label_budget:
        label = label[:-1]

    draw_text(draw, (text_x, y + 8), label, fonts().xxs, fg)
    draw_text(draw, (WIDTH - PAD, y + 8), right_text, fonts().xxs, fg, anchor="ra")

    # "Donnerstag 30.07." is the widest case and only just fits. Step down a
    # font size before letting it touch the edge.
    big_font = fonts().m
    if text_width(draw, big, big_font) > WIDTH - PAD - text_x:
        big_font = fonts().s

    draw_text(draw, (text_x, y + height // 2 + 5), big, big_font, fg, anchor="lm")


def _draw_footer(draw, data: DashboardData) -> None:
    """4-day outlook across the full width, battery gauge in the corner."""
    draw.rectangle([0, FOOTER_Y, WIDTH, HEIGHT], fill=WHITE)
    draw.line([PAD, FOOTER_Y, WIDTH - PAD, FOOTER_Y], fill=BLACK, width=2)

    # Reserve the right-hand strip for the battery, then split what's left.
    # The battery occupies the bottom-right corner, so the band stops short of
    # it. Sharing the last column meant the icon and the gauge overlapped.
    battery_zone = 118
    days = data.forecast[:FORECAST_DAYS]

    if days:
        usable = WIDTH - 2 * PAD - battery_zone
        col_w = usable // len(days)

        for i, day in enumerate(days):
            x = PAD + i * col_w
            draw_text(
                draw,
                (x, FOOTER_Y + 7),
                day.heading(data.language).upper(),
                fonts().xs,
                BLACK,
            )

            # Icon sits to the right of the day label, leaving the left edge
            # free for the numbers that carry the most meaning. Kept clear of
            # the column divider at x + col_w - 8.
            draw_weather_icon(draw, day.condition, x + col_w - 40, FOOTER_Y + 34, 32)

            # High is the number that matters; low is secondary, so it is set
            # smaller and beside rather than competing at the same size.
            if day.temp_high is not None:
                high = f"{degrees(day.temp_high)}°"
                draw_text(
                    draw,
                    (x, FOOTER_Y + 24),
                    high,
                    fonts().m,
                    temperature_color(day.temp_high),
                )
                high_w = text_width(draw, high, fonts().m)
            else:
                draw_text(draw, (x, FOOTER_Y + 24), "--", fonts().m, BLACK)
                high_w = text_width(draw, "--", fonts().m)

            if day.temp_low is not None:
                draw_text(
                    draw,
                    (x + high_w + 5, FOOTER_Y + 32),
                    f"{degrees(day.temp_low)}°",
                    fonts().xs,
                    BLACK,
                )

            # Four columns are narrower than three, and the icon eats part of
            # the width, so long condition names get shortened.
            draw_text(
                draw,
                (x, FOOTER_Y + 56),
                condition_short(data.language, day.condition, col_w - 42, draw),
                fonts().xs,
                BLACK,
            )

            if day.precipitation is not None and day.precipitation > 0.1:
                draw_text(
                    draw,
                    (x, FOOTER_Y + 71),
                    f"{day.precipitation:.1f} mm",
                    fonts().xs,
                    BLUE,
                )

            if i > 0:
                draw.line([x - 8, FOOTER_Y + 10, x - 8, HEIGHT - 10], fill=BLACK, width=1)

    # Divider before the battery, matching the ones between forecast columns,
    # so the gauge reads as its own cell rather than as part of the last day.
    if days:
        divider_x = WIDTH - PAD - battery_zone + 4
        draw.line([divider_x, FOOTER_Y + 10, divider_x, HEIGHT - 10], fill=BLACK, width=1)

    # The cell the divider carved out, in the coordinates the gauge is centred
    # against. The divider is the left edge; the page padding is the right one.
    cell_left = (WIDTH - PAD - battery_zone + 4) if days else WIDTH - PAD - battery_zone
    cell_right = WIDTH - PAD
    # Vertically, the cell is what the dividers span, not the whole footer.
    cell_top = FOOTER_Y + 10
    cell_bottom = HEIGHT - 10

    # Page indicator above the gauge, sharing the same cell — same helper and
    # same x as the slim footer on pages 2 and 3, so it does not appear to move
    # when the page changes. It draws nothing on a single-page setup.
    marker_visible = data.page_count > 1
    marker_top = FOOTER_Y + 14
    if marker_visible:
        marker_left = WIDTH - PAD - battery_zone + 10
        draw_page_marker(draw, data, marker_left, marker_top, width=battery_zone - 14)

    # Centre the block on the cell, using what it actually draws. With a page
    # marker above it the gauge keeps to the lower half so the two do not
    # collide; without one it takes the whole cell, which is the single-page
    # case this display runs in.
    x0, y0, x1, y1 = battery_block_extent(draw, data)
    # The divider is the cell's left edge, so the interior starts one past it.
    cell_centre_x = (cell_left + 1 + cell_right) / 2
    cell_centre_y = (cell_top + cell_bottom) / 2

    battery_x = round(cell_centre_x - (x0 + x1) / 2)
    if marker_visible:
        # Centre the block in what is left of the cell below the marker rather
        # than pinning it to a fixed offset from the bottom edge. The fixed
        # offset predated the runtime estimate and clipped it off the panel.
        sub_top = marker_top + page_marker_height(draw, data) + 4
        battery_y = round((sub_top + cell_bottom) / 2 - (y0 + y1) / 2)
    else:
        battery_y = round(cell_centre_y - (y0 + y1) / 2)
    draw_battery(draw, battery_x, battery_y, data)


def draw_battery(draw, x: int, y: int, data: DashboardData) -> None:
    """Battery gauge: percentage above, segmented bar below.

    Drawn even when the level is unknown — a visibly empty gauge marked "?" is
    honest, whereas omitting it entirely looks like a layout bug. Voltage is
    deliberately not shown: it means nothing to anyone walking past.
    """
    w, h = BATTERY_W, BATTERY_H
    term_w = BATTERY_TERM_W

    if data.battery is None:
        draw.rectangle([x, y, x + w, y + h], fill=WHITE, outline=BLACK, width=2)
        draw.rectangle([x + w + 2, y + 7, x + w + 2 + term_w, y + h - 7], fill=BLACK)
        draw_text(
            draw,
            (x + w + BATTERY_TEXT_GAP, y + h // 2),
            "—%",
            fonts().s,
            BLACK,
            anchor="lm",
        )
        return

    level = max(0.0, min(100.0, data.battery))
    color = GREEN if level > 40 else (YELLOW if level > 15 else RED)

    # Classic battery outline with a proportional fill — instantly recognisable
    # at a glance, unlike a segmented bar that has to be counted.
    draw.rectangle([x, y, x + w, y + h], fill=WHITE, outline=BLACK, width=2)
    draw.rectangle([x + w + 2, y + 7, x + w + 2 + term_w, y + h - 7], fill=BLACK)

    inner_w = w - 6
    fill_w = int(inner_w * level / 100.0)
    if fill_w > 0:
        draw.rectangle([x + 3, y + 3, x + 3 + fill_w, y + h - 3], fill=color)

    draw_text(
        draw,
        (x + w + BATTERY_TEXT_GAP, y + h // 2 + 1),
        f"{level:.0f}%",
        fonts().s,
        BLACK,
        anchor="lm",
    )

    _draw_battery_estimate(draw, x, y, data)


def _draw_battery_estimate(draw, x: int, y: int, data: DashboardData) -> None:
    """Runtime estimate under the gauge, centred on the gauge group.

    Small and unobtrusive on purpose: the percentage is the fact, this is the
    inference. It is absent whenever the history cannot support it, which is
    the normal state for the first few days after a charge or a firmware change.
    """
    label = battery_estimate_label(data)
    if label is None:
        return

    group_w = battery_group_width(draw, data)
    draw_text(
        draw,
        (x + group_w // 2, y + BATTERY_H + BATTERY_ESTIMATE_GAP),
        label,
        fonts().xs,
        BLACK,
        anchor="ma",
    )


# --- image utilities ---------------------------------------------------------
def fit_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Scale to cover the target box, then centre-crop the overflow."""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = max(1, int(src_w * scale)), max(1, int(src_h * scale))
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def atkinson_dither(img: Image.Image, palette: list[tuple[int, int, int]]) -> Image.Image:
    """Atkinson dithering: spread only 6/8 of the error to 6 neighbours.

    Floyd-Steinberg pushes 100% of the error onward, which on a 6-colour panel
    smears flat UI fills and produces colour fringing around text. Atkinson's
    75% keeps edges crisp at the cost of slightly reduced tonal range — the
    right trade for a layout that is mostly flat blocks and type.

    Without numpy this degrades to Pillow's Floyd-Steinberg rather than failing.
    """
    if not _HAVE_NUMPY:
        palette_img = Image.new("P", (1, 1))
        flat: list[int] = []
        for color in palette:
            flat.extend(color)
        flat.extend([0] * (256 * 3 - len(flat)))
        palette_img.putpalette(flat)
        return (
            img.convert("RGB")
            .quantize(palette=palette_img, dither=Image.Dither.FLOYDSTEINBERG)
            .convert("RGB")
        )

    pixels = np.array(img.convert("RGB"), dtype=np.float64)
    h, w, _ = pixels.shape
    pal = np.array(palette, dtype=np.float64)

    for y in range(h):
        for x in range(w):
            old = pixels[y, x].copy()
            nearest = pal[np.argmin(np.sum((pal - old) ** 2, axis=1))]
            pixels[y, x] = nearest
            err = (old - nearest) / 8.0

            if x + 1 < w:
                pixels[y, x + 1] += err
            if x + 2 < w:
                pixels[y, x + 2] += err
            if y + 1 < h:
                if x - 1 >= 0:
                    pixels[y + 1, x - 1] += err
                pixels[y + 1, x] += err
                if x + 1 < w:
                    pixels[y + 1, x + 1] += err
            if y + 2 < h:
                pixels[y + 2, x] += err

    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))


def quantize_flat(img: Image.Image) -> Image.Image:
    """Nearest-colour map, no diffusion. Used for the UI chrome regions."""
    palette_img = Image.new("P", (1, 1))
    flat: list[int] = []
    for color in SPECTRA6:
        flat.extend(color)
    flat.extend([0] * (256 * 3 - len(flat)))
    palette_img.putpalette(flat)
    return img.convert("RGB").quantize(palette=palette_img, dither=Image.Dither.NONE)


# --- shared chrome for the secondary pages ------------------------------------
def dither_region(canvas: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Atkinson-dither one rectangle of the canvas in place.

    Photographs need error diffusion to survive 6 colours; flat blocks and text
    look better with a hard nearest-colour map. Every page therefore dithers
    only its photo area and leaves the chrome to `quantize_flat`.
    """
    x0, y0, x1, y1 = box
    region = canvas.crop((x0, y0, x1, y1))
    # Boost before dithering — 6 colours lose a lot of apparent contrast.
    region = ImageEnhance.Contrast(region).enhance(1.2)
    region = ImageEnhance.Color(region).enhance(1.3)
    canvas.paste(atkinson_dither(region, SPECTRA6), (x0, y0))


def page_marker_height(draw, data: DashboardData) -> int:
    """Ink height of the marker, or 0 when it is not drawn.

    Callers need this to keep the battery gauge clear of it. Measuring beats
    assuming: the estimate line under the gauge is drawn from the same footer
    budget, and a hard-coded offset is what pushed "~23 Tage" off the bottom
    edge of page 1.
    """
    if data.page_count <= 1:
        return 0
    return text_height(draw, data.language.page_marker(data.page, data.page_count), fonts().s)


def draw_page_marker(
    draw, data: DashboardData, x: int, y: int, width: int = 96, anchor: str = "ma"
) -> None:
    """The page indicator, in the same spot on every page, so the eye learns
    one place.

    Drawn only when there is more than one page. "Seite 1/1" answers a question
    nobody asked and costs the battery gauge half its cell; a single-page setup
    should look like one, not like a rotation that is missing its other pages.

    Centred in the cell it shares with the battery gauge and set at the same size
    as the other footer labels — at 11px it was too small to be read from across
    the kitchen, which is the only place it matters.
    """
    if data.page_count <= 1:
        return
    label = data.language.page_marker(data.page, data.page_count)
    draw_text(draw, (x + width // 2, y), label, fonts().s, BLACK, anchor=anchor)


# Geometry of the battery graphic, shared by the gauge and by the code that
# centres it. Kept here rather than as literals in two places, because the two
# drifting apart is exactly what puts a gauge slightly off-centre.
BATTERY_W = 46
BATTERY_H = 22
BATTERY_TERM_W = 4  # the little nub on the positive end
BATTERY_TEXT_GAP = 14
# Gap between the gauge and the runtime estimate under it.
BATTERY_ESTIMATE_GAP = 4
BATTERY_ESTIMATE_H = 12


def battery_estimate_label(data: DashboardData) -> str | None:
    """"~23 Tage", "~23 days", or None when there is nothing trustworthy to say."""
    days = data.battery_days_left
    if days is None:
        return None
    return data.language.battery_estimate(days)


def battery_block_extent(draw, data: DashboardData) -> tuple[int, int, int, int]:
    """Ink extent of the whole battery block, relative to the gauge's origin.

    Measured rather than assumed. The estimate line turned out to be wider than
    the gauge it sits under, and a text's ink box is taller than its nominal
    size, so centring against nominal constants left the block visibly off in
    both axes.
    """
    label_pct = (
        "—%" if data.battery is None else f"{max(0.0, min(100.0, data.battery)):.0f}%"
    )
    pct_box = draw.textbbox(
        (BATTERY_W + BATTERY_TEXT_GAP, BATTERY_H // 2 + 1),
        label_pct,
        font=fonts().s,
        anchor="lm",
    )

    # The gauge rectangle spans 0..BATTERY_W inclusive; the nub reaches further
    # right but still falls short of where the percentage starts.
    x0, y0 = 0, 0
    x1 = max(BATTERY_W + 2 + BATTERY_TERM_W, pct_box[2] - 1)
    y1 = max(BATTERY_H, pct_box[3] - 1)

    estimate = battery_estimate_label(data)
    if estimate is not None:
        group_w = battery_group_width(draw, data)
        est_box = draw.textbbox(
            (group_w // 2, BATTERY_H + BATTERY_ESTIMATE_GAP),
            estimate,
            font=fonts().xs,
            anchor="ma",
        )
        x0 = min(x0, est_box[0])
        x1 = max(x1, est_box[2] - 1)
        y1 = max(y1, est_box[3] - 1)

    return x0, y0, x1, y1


def battery_group_width(draw, data: DashboardData) -> int:
    """Width of the gauge plus its percentage label, as actually drawn.

    The terminal nub ends at ``x + BATTERY_W + 2 + BATTERY_TERM_W``, which is
    still left of where the label starts, so it never widens the group. Counting
    it as well pushed the whole thing three pixels off centre.
    """
    label = "—%" if data.battery is None else f"{max(0.0, min(100.0, data.battery)):.0f}%"
    return BATTERY_W + BATTERY_TEXT_GAP + text_width(draw, label, fonts().s)


def draw_slim_footer(draw, data: DashboardData, items: list[tuple[str, str]]) -> int:
    """A 44px status strip: label/value pairs left, page marker and battery right.

    Pages 2 and 3 do not need page 1's 86px forecast band, but they do need the
    same battery gauge and page indicator in the same corner — the device is
    read from across the room and a gauge that moves between pages reads as two
    different devices.

    Returns the y coordinate the strip starts at, so callers can size their own
    content against it.
    """
    top = HEIGHT - SLIM_FOOTER_H
    draw.rectangle([0, top, WIDTH, HEIGHT], fill=WHITE)
    draw.line([PAD, top, WIDTH - PAD, top], fill=BLACK, width=2)

    # The gauge always sits in the same corner; the marker goes beside it rather
    # than above it. Stacking them is what page 1's taller footer does, and the
    # 44px strip has no room for both — the marker landed on top of the gauge.
    x0, y0, x1, y1 = battery_block_extent(draw, data)
    battery_left = WIDTH - PAD - x1
    draw_battery(
        draw,
        battery_left,
        top + round(SLIM_FOOTER_H / 2 - (y0 + y1) / 2),
        data,
    )

    marker_zone = 0
    if data.page_count > 1:
        marker_width = 110
        marker_zone = marker_width + 12
        draw_page_marker(
            draw,
            data,
            battery_left - marker_zone,
            top + SLIM_FOOTER_H // 2,
            width=marker_width,
            anchor="mm",
        )

    # The battery cell is fixed width; the pairs get whatever is left and are
    # dropped from the tail when they no longer fit.
    budget = battery_left - marker_zone - PAD - PAD
    x = PAD
    for label, value in items:
        width = max(
            text_width(draw, label, fonts().xxs),
            text_width(draw, value, fonts().s),
        )
        if x + width > budget:
            break
        draw_text(draw, (x, top + 5), label, fonts().xxs, BLACK)
        draw_text(draw, (x, top + 20), value, fonts().s, BLACK)
        x += width + 24

    return top


# --- entry point -------------------------------------------------------------
def render_dashboard(
    data: DashboardData,
    output_path: Path,
    dither_photo: bool = True,
) -> Path:
    """Render the requested page and write it as a PNG.

    Args:
        data: assembled dashboard state, including `page`
        output_path: PNG destination
        dither_photo: apply Atkinson dithering to photo regions. Disable for
            fast local layout iteration; it is the slow part of a render.

    Returns:
        The path written.
    """
    # Imported here rather than at module scope: the page modules import this
    # one for the palette and helpers, so a top-level import would be circular.
    # The absolute fallback is for tools/preview_dashboard.py, which imports
    # these files as plain modules rather than as part of the HA integration.
    try:
        from .dashboard_pages import render_photo_page, render_weather_page
    except ImportError:  # pragma: no cover - local preview only
        from dashboard_pages import render_photo_page, render_weather_page  # type: ignore[no-redef]

    canvas = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(canvas)

    if data.page == 2:
        render_weather_page(canvas, draw, data)
    elif data.page == 3:
        render_photo_page(canvas, draw, data, dither_photo=dither_photo)
    else:
        render_home_page(canvas, draw, data, dither_photo=dither_photo)

    final = quantize_flat(canvas).convert("RGB")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Save to a temp file and rename: the device may fetch this at any moment,
    # and a partially written PNG decodes as a failure on the panel.
    tmp_path = output_path.with_suffix(".tmp.png")
    final.save(tmp_path, optimize=True)
    tmp_path.replace(output_path)
    logger.info("Dashboard page %d rendered to %s", data.page, output_path)
    return output_path


def render_home_page(
    canvas: Image.Image,
    draw,
    data: DashboardData,
    dither_photo: bool = True,
) -> None:
    """Page 1: daily image, indoor climate, waste, 4-day band."""
    _draw_image(canvas, draw, data)

    if dither_photo:
        dither_region(canvas, (IMAGE_X, IMAGE_Y, IMAGE_X + IMAGE_W, IMAGE_Y + IMAGE_H))

    # Chrome is drawn after the photo so nothing bleeds into the text.
    draw_header(draw, data)
    _draw_sidebar(draw, data)
    _draw_footer(draw, data)


def fingerprint(data: DashboardData) -> str:
    """Stable string describing the *displayed* state.

    The device compares this to decide whether a 20-second panel refresh is
    worth the battery. Deliberately excludes minutes, battery percentage and
    anything else that changes continuously — otherwise every wakeup would
    look like a change and the whole point is lost.

    The page number is part of it: a page change is exactly the case where the
    panel *must* repaint even though the underlying data is unchanged.
    """
    parts = [
        f"p{data.page}",
        # The language is part of the displayed state: switching it changes
        # every label on the panel while leaving the underlying data alone, so
        # without this the device would keep the old rendering until the next
        # weather change.
        data.language.code,
        data.now.strftime("%Y-%m-%d-%H"),
        f"{data.outside_temp:.0f}" if data.outside_temp is not None else "-",
        data.outside_condition,
        f"{data.inside_temp:.0f}" if data.inside_temp is not None else "-",
    ]
    for entry in data.waste[:3]:
        parts.append(f"{entry.kind}:{entry.due.isoformat()}")
    for day in data.forecast[:FORECAST_DAYS]:
        high = f"{day.temp_high:.0f}" if day.temp_high is not None else "-"
        parts.append(f"{day.condition}:{high}")

    # Page-specific tails. Without these, page 3 would show the same fingerprint
    # for every photo in the album and the panel would never repaint a new one.
    if data.page == 2:
        for hour in data.hourly[:HOURLY_POINTS]:
            temp = f"{hour.temp:.0f}" if hour.temp is not None else "-"
            parts.append(f"{hour.when.strftime('%H')}:{temp}")
    elif data.page == 3:
        if data.photo is not None:
            parts.append(f"photo:{data.photo.asset_id or data.photo.path.name}")
        else:
            parts.append(f"photo:none:{data.photo_error}")

    return "|".join(parts)


def parse_waste_sensor(state: str, attributes: dict, label: str, kind: str) -> WasteEntry | None:
    """Build a WasteEntry from a waste_collection_schedule sensor.

    Those sensors carry a human string as their state ("in 3 Tagen") and the
    real dates as attribute *keys* ("2026-07-29": "Restabfallbehaelter"), so
    the earliest future date key is what we actually want.
    """
    today = date.today()
    best: date | None = None

    for key in attributes:
        try:
            parsed = datetime.strptime(str(key), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if parsed >= today and (best is None or parsed < best):
            best = parsed

    if best is None:
        # Fall back to parsing the state text ("in 3 Tagen" / "Morgen").
        text = (state or "").strip().lower()
        if text in ("heute", "today"):
            best = today
        elif text in ("morgen", "tomorrow"):
            best = today + timedelta(days=1)
        else:
            digits = "".join(c for c in text if c.isdigit())
            if digits:
                best = today + timedelta(days=int(digits))

    if best is None:
        return None

    return WasteEntry(label=label, kind=kind, due=best)
