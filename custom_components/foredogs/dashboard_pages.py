r"""Secondary dashboard pages for the reTerminal E1002.

Page 1 (the daily image plus waste and climate) stays in `dashboard_render.py`,
which also owns the palette, fonts, icons and dithering these pages import.

Page 2 — weather detail:

    +--------------------------------------------+
    |  20:47  Freitag        18° Teils wolkig    |  shared header, 64px
    +--------------------------------------------+
    |  24 STUNDEN                        max 24° |
    |      ___                                   |  temperature curve with a
    |  ___/   \___                       min 13° |  precipitation bar under it
    |  ||    ||||                                |
    |  21  00  03  06  09  12  15  18            |
    +--------------------------------------------+
    | SA | 26° ---- 13° | Sonnig            0.4  |  7 day rows
    | SO | 24° --- 12°  | Regen             3.2  |
    ...
    +--------------------------------------------+
    | SONNENAUF 05:42 | UNTER 21:38 |  2/3  [==] |  slim footer
    +--------------------------------------------+

Page 3 — full-bleed photo from Immich (or a local folder), with a black caption
bar so the date and album are readable against any image.

Both pages reuse the slim footer and battery gauge in the same corner as page 1:
the panel is read from across the room, and a gauge that moves between pages
reads as two different devices.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PIL import Image

from .dashboard_render import (
    BLACK,
    BLUE,
    FORECAST_DAYS_LONG,
    HEADER_H,
    HEIGHT,
    HOURLY_POINTS,
    PAD,
    SLIM_FOOTER_H,
    Language,
    WHITE,
    WIDTH,
    YELLOW,
    DashboardData,
    condition_label,
    condition_short,
    degrees,
    dither_region,
    draw_header,
    draw_slim_footer,
    draw_text,
    draw_weather_icon,
    fit_crop,
    fonts,
    temperature_color,
    text_width,
)

logger = logging.getLogger("foredogs")

# --- page 2 geometry ---------------------------------------------------------
CHART_Y = HEADER_H
CHART_H = 168
# The band gets whatever is left between the chart and the slim footer, divided
# into FORECAST_DAYS_LONG rows.
BAND_Y = CHART_Y + CHART_H
BAND_H = HEIGHT - SLIM_FOOTER_H - BAND_Y

# Left gutter of the chart, reserved for the min/max scale labels.
CHART_LEFT = PAD + 4
CHART_RIGHT = WIDTH - PAD - 52
# Precipitation bars hang from the bottom of the plot area rather than sharing
# the temperature scale — two quantities on one axis would need two scales and a
# 6-colour panel has no room for that kind of legend.
RAIN_H = 30
# Headroom kept clear at the top of the curve, so the callout above the warmest
# hour has somewhere to go. Without it the peak sits exactly on plot_top and its
# label lands in the "24 STUNDEN" caption band above the chart — which is what
# happens on any day whose warmest hour is also its first.
CURVE_LABEL_H = 18
# Bars are clipped here. Anything over 4 mm/h is "heavy" and the exact figure
# stops mattering for the decision the chart supports (coat or no coat).
RAIN_FULL_SCALE_MM = 4.0


def render_weather_page(canvas: Image.Image, draw, data: DashboardData) -> None:
    """Page 2: 24-hour curve on top, 7-day band below."""
    draw.rectangle([0, HEADER_H, WIDTH, HEIGHT], fill=WHITE)

    _draw_hourly_chart(draw, data)
    _draw_week_band(draw, data)

    draw_header(draw, data)

    items: list[tuple[str, str]] = []
    if data.sun_rise:
        items.append((data.language.label("SUNRISE"), data.sun_rise))
    if data.sun_set:
        items.append((data.language.label("SUNSET"), data.sun_set))
    if data.inside_temp is not None:
        items.append((data.language.label("INSIDE"), f"{data.inside_temp:.1f}°"))
    if data.wind_speed is not None:
        items.append((data.language.label("WIND"), f"{data.wind_speed:.0f} km/h"))
    draw_slim_footer(draw, data, items)


def _draw_hourly_chart(draw, data: DashboardData) -> None:
    """Temperature line with precipitation bars beneath it.

    Drawn as a filled area rather than a 1px line: on a dithered 6-colour panel
    a thin diagonal breaks into disconnected dots, whereas a filled shape keeps
    its silhouette.
    """
    hours = [h for h in data.hourly[:HOURLY_POINTS] if h.temp is not None]

    draw_text(draw, (CHART_LEFT, CHART_Y + 6), data.language.label("HOURS_24"), fonts().xxs, BLACK)

    if len(hours) < 2:
        # Installs whose weather integration publishes no hourly forecast still
        # get a usable page — say so instead of drawing an empty frame.
        draw_text(
            draw,
            (WIDTH // 2, CHART_Y + CHART_H // 2),
            data.language.label("NO_HOURLY"),
            fonts().s,
            BLACK,
            anchor="mm",
        )
        draw.line([PAD, BAND_Y, WIDTH - PAD, BAND_Y], fill=BLACK, width=2)
        return

    temps = [h.temp for h in hours if h.temp is not None]
    lo, hi = min(temps), max(temps)
    # A flat day would otherwise divide by zero and, worse, exaggerate a 0.3°
    # wobble into a mountain range. Force at least a 4° window.
    if hi - lo < 4:
        mid = (hi + lo) / 2
        lo, hi = mid - 2, mid + 2

    plot_top = CHART_Y + 26
    plot_bottom = CHART_Y + CHART_H - 22
    # The rain band sits below the temperature baseline with a gap, so a heavy
    # hour does not read as part of the curve's own fill.
    curve_bottom = plot_bottom - RAIN_H - 4

    # Scale labels on the right, so the curve keeps the full left width.
    draw_text(
        draw,
        (WIDTH - PAD, plot_top - 2),
        f"max {degrees(hi)}°",
        fonts().xxs,
        temperature_color(hi),
        anchor="ra",
    )
    draw_text(
        draw,
        (WIDTH - PAD, curve_bottom - 12),
        f"min {degrees(lo)}°",
        fonts().xxs,
        temperature_color(lo),
        anchor="ra",
    )

    span = CHART_RIGHT - CHART_LEFT
    step = span / (len(hours) - 1)

    def y_for(temp: float) -> int:
        # Headroom at both ends: the warmest hour maps to
        # plot_top + CURVE_LABEL_H and the coldest to
        # curve_bottom - CURVE_LABEL_H, so every point has room for its label
        # on whichever side is clear — above it in white, or below it in the
        # fill. Without the bottom band a dip sits exactly on the baseline and
        # has nowhere to put its number except into the rising curve beside it.
        top = plot_top + CURVE_LABEL_H
        bottom = curve_bottom - CURVE_LABEL_H
        return int(bottom - (temp - lo) / (hi - lo) * (bottom - top))

    points = [
        (int(CHART_LEFT + i * step), y_for(h.temp))  # type: ignore[arg-type]
        for i, h in enumerate(hours)
    ]

    # Fill under the curve in yellow — the one palette colour light enough to
    # sit behind black labels without hiding them.
    draw.polygon(
        [(points[0][0], curve_bottom)] + points + [(points[-1][0], curve_bottom)],
        fill=YELLOW,
    )
    # Then the curve itself, thick enough to survive quantization.
    draw.line(points, fill=BLACK, width=3, joint="curve")
    draw.line([CHART_LEFT, curve_bottom, CHART_RIGHT, curve_bottom], fill=BLACK, width=1)

    # --- precipitation bars ---
    bar_w = max(3, int(step) - 3)
    for (x, _), hour in zip(points, hours, strict=False):
        mm = hour.precipitation or 0.0
        if mm <= 0.05:
            continue
        height = max(2, int(min(mm / RAIN_FULL_SCALE_MM, 1.0) * RAIN_H))
        draw.rectangle(
            [x - bar_w // 2, plot_bottom - height, x - bar_w // 2 + bar_w, plot_bottom],
            fill=BLUE,
        )

    # --- hour axis ---
    # Every third hour, so the labels never touch at 24 points across 700px.
    for i, hour in enumerate(hours):
        if i % 3:
            continue
        x = int(CHART_LEFT + i * step)
        draw.line([x, plot_bottom, x, plot_bottom + 3], fill=BLACK, width=1)
        draw_text(
            draw,
            (x, plot_bottom + 6),
            hour.when.strftime("%H"),
            fonts().xxs,
            BLACK,
            anchor="ma",
        )

    # Temperature callouts at both ends plus the extremes, rather than a label
    # per hour: four numbers are read, twenty-four are skimmed past.
    warmest = temps.index(max(temps))
    coldest = temps.index(min(temps))
    marked = {0, len(hours) - 1, warmest, coldest}

    # Which side of its point each number sits on.
    #
    # The two extremes are decided by geometry rather than measured: nothing can
    # be above the warmest hour and nothing below the coldest, and the scale
    # reserves CURVE_LABEL_H at both ends so the room is always there. The two
    # endpoints are wherever the curve leaves them, so those are measured.
    sides = {warmest: "above", coldest: "below"}

    for i in sorted(marked):
        x, y = points[i]
        anchor = "la" if i == 0 else ("ra" if i == len(hours) - 1 else "ma")
        side = sides.get(i) or _clear_side(points, x, y, anchor)
        label_y = y + 6 if side == "below" else y - 18
        draw_text(
            draw,
            (x, label_y),
            f"{degrees(hours[i].temp)}°",  # type: ignore[union-attr]
            fonts().xs,
            BLACK,
            anchor=anchor,
        )

    draw.line([PAD, BAND_Y, WIDTH - PAD, BAND_Y], fill=BLACK, width=2)


def _clear_side(points, x: int, y: int, anchor: str) -> str:
    """"above" or "below", for a point whose surroundings decide it.

    Used for the two endpoints, where the curve can be doing anything. Above is
    preferred, because black on white reads best; but if the curve climbs into
    the space the label would occupy, the label goes below it instead, onto the
    yellow fill — the one readable combination the six-colour palette offers,
    and the reason the fill is yellow.
    """
    # The label's own footprint, which depends on the end it is anchored by.
    half = 26
    if anchor == "la":
        left, right = x, x + 2 * half
    elif anchor == "ra":
        left, right = x - 2 * half, x
    else:
        left, right = x - half, x + half

    nearby = [py for px, py in points if left <= px <= right]
    # Smaller y is higher on the panel, so the smallest is the curve's high
    # point within the width the label will cover.
    highest = min(nearby) if nearby else y

    # More than a few pixels of climb and the label would sit in the curve
    # rather than above it. Six absorbs the jitter of an almost flat hour.
    return "below" if (y - highest) > 6 else "above"


def _draw_week_band(draw, data: DashboardData) -> None:
    """One row per day: weekday, low–high range bar, condition, precipitation.

    A horizontal range bar rather than two bare numbers: it makes "Tuesday is
    the warm one" answerable without reading any digits, which is what a glance
    at a kitchen display actually asks.
    """
    days = data.forecast[:FORECAST_DAYS_LONG]
    if not days:
        return

    row_h = BAND_H // len(days)

    highs = [d.temp_high for d in days if d.temp_high is not None]
    lows = [d.temp_low for d in days if d.temp_low is not None]
    scale_hi = max(highs) if highs else 20.0
    scale_lo = min(lows) if lows else 10.0
    if scale_hi - scale_lo < 6:
        mid = (scale_hi + scale_lo) / 2
        scale_lo, scale_hi = mid - 3, mid + 3

    # Column geometry, left to right: label, low, bar, high, icon, condition, mm.
    #
    # The label column is measured rather than fixed. It used to be a constant
    # sized for "MORGEN", which clipped English to "TOMORR" — every language
    # has a different longest word here, and so does every week, since the
    # labels are weekday names the rest of the time.
    #
    # The clamp keeps a pathological label from eating the range bar: past that
    # width it is truncated, which is what the loop below already does.
    label_x = PAD + 2
    widest = max(
        (text_width(draw, d.heading(data.language).upper(), fonts().xs) for d in days),
        default=0,
    )
    low_x = label_x + min(max(widest + 8, 60), 120)
    bar_x0 = low_x + 36
    bar_x1 = WIDTH - PAD - 250
    high_x = bar_x1 + 8
    icon_x = bar_x1 + 60
    cond_x = bar_x1 + 82
    mm_x = WIDTH - PAD

    for i, day in enumerate(days):
        y = BAND_Y + i * row_h
        mid_y = y + row_h // 2

        if i:
            draw.line([PAD, y, WIDTH - PAD, y], fill=BLACK, width=1)

        # Today's own row is dropped by the collector, so row 0 is tomorrow.
        # Truncated to the column rather than trusted to fit: the label comes
        # from the caller and a longer one would overrun the temperature beside
        # it instead of being clipped.
        label = day.heading(data.language).upper()
        while label and text_width(draw, label, fonts().xs) > low_x - label_x - 6:
            label = label[:-1]
        draw_text(draw, (label_x, mid_y), label, fonts().xs, BLACK, anchor="lm")

        if day.temp_low is not None and day.temp_high is not None:
            x_lo = int(
                bar_x0 + (day.temp_low - scale_lo) / (scale_hi - scale_lo) * (bar_x1 - bar_x0)
            )
            x_hi = int(
                bar_x0 + (day.temp_high - scale_lo) / (scale_hi - scale_lo) * (bar_x1 - bar_x0)
            )
            x_hi = max(x_hi, x_lo + 4)

            # Track behind the bar, so a short range still reads as a position
            # on a scale rather than a stray dash.
            draw.line([bar_x0, mid_y, bar_x1, mid_y], fill=BLACK, width=1)
            draw.rectangle(
                [x_lo, mid_y - 5, x_hi, mid_y + 5],
                fill=temperature_color(day.temp_high),
                outline=BLACK,
            )

            draw_text(
                draw,
                (low_x, mid_y),
                f"{degrees(day.temp_low)}°",
                fonts().xs,
                BLUE if day.temp_low <= 5 else BLACK,
                anchor="lm",
            )
            draw_text(draw, (high_x, mid_y), f"{degrees(day.temp_high)}°", fonts().m, BLACK, anchor="lm")

        draw_weather_icon(draw, day.condition, icon_x, mid_y, min(28, row_h - 6))
        draw_text(
            draw,
            (cond_x, mid_y),
            condition_short(data.language, day.condition, mm_x - cond_x - 54, draw),
            fonts().xs,
            BLACK,
            anchor="lm",
        )

        if day.precipitation is not None and day.precipitation > 0.1:
            draw_text(
                draw,
                (mm_x, mid_y),
                f"{day.precipitation:.1f} mm",
                fonts().xs,
                BLUE,
                anchor="rm",
            )


# --- page 3 -----------------------------------------------------------------
# The caption bar overlays the photo instead of shrinking it: an 800x480 panel
# has no pixels to spare, and dark type on an unknown photo is unreadable while
# white-on-black always works.
CAPTION_H = 34


def render_photo_page(
    canvas: Image.Image,
    draw,
    data: DashboardData,
    dither_photo: bool = True,
) -> None:
    """Page 3: one photo, full bleed, with a caption bar and the slim footer."""
    photo_bottom = HEIGHT - SLIM_FOOTER_H

    drawn = False
    if data.photo is not None and Path(data.photo.path).exists():
        try:
            art = Image.open(data.photo.path).convert("RGB")
            canvas.paste(fit_crop(art, WIDTH, photo_bottom), (0, 0))
            drawn = True
        except OSError:
            logger.warning("Could not open photo at %s", data.photo.path)

    if not drawn:
        draw.rectangle([0, 0, WIDTH - 1, photo_bottom - 1], fill=WHITE, outline=BLACK, width=2)
        # photo_error is a label key from photo_source; an unrecognised value
        # is printed as-is so a future error string still says something.
        message = (
            data.language.label(data.photo_error)
            if data.photo_error
            else data.language.label("PHOTO_NONE")
        )
        draw_text(
            draw,
            (WIDTH // 2, photo_bottom // 2),
            message,
            fonts().m,
            BLACK,
            anchor="mm",
        )
    elif dither_photo:
        dither_region(canvas, (0, 0, WIDTH, photo_bottom))

    if drawn and data.photo is not None:
        _draw_caption(draw, data.photo, photo_bottom, data.language)

    items: list[tuple[str, str]] = []
    if data.outside_temp is not None:
        items.append((
            data.language.label("OUTSIDE"),
            f"{degrees(data.outside_temp)}°  {condition_label(data.language, data.outside_condition)}",
        ))
    if data.inside_temp is not None:
        items.append((data.language.label("INSIDE"), f"{data.inside_temp:.1f}°"))
    if data.waste:
        entry = data.waste[0]
        items.append((entry.label.upper(), entry.when_text(data.language)))
    draw_slim_footer(draw, data, items)


def _draw_caption(draw, photo, photo_bottom: int, language: Language) -> None:
    """Date and source in a black bar along the bottom edge of the photo."""
    top = photo_bottom - CAPTION_H
    draw.rectangle([0, top, WIDTH, photo_bottom], fill=BLACK)

    left = _date_caption(photo.taken, language)
    if photo.caption:
        left = f"{left}  ·  {photo.caption}" if left else photo.caption
    if left:
        draw_text(draw, (PAD, top + CAPTION_H // 2), left, fonts().s, WHITE, anchor="lm")

    source = _source_caption(photo, language)
    if source:
        # Truncated rather than allowed to collide with the date on the left.
        budget = WIDTH - PAD - text_width(draw, left, fonts().s) - PAD - 24
        while source and text_width(draw, source, fonts().xs) > budget:
            source = source[:-1]
        if source:
            draw_text(
                draw,
                (WIDTH - PAD, top + CAPTION_H // 2),
                source,
                fonts().xs,
                WHITE,
                anchor="rm",
            )


def _source_caption(photo, language: Language) -> str:
    """"Immich · Holidays" or "Ordner · archive", in the active language."""
    if photo.source_kind == "immich":
        # A product name, not a word.
        prefix = "Immich"
    elif photo.source_kind == "folder":
        prefix = language.label("PHOTO_SOURCE_FOLDER")
    else:
        return ""
    return f"{prefix} · {photo.source_name}" if photo.source_name else prefix


def _date_caption(taken: datetime | None, language: Language) -> str:
    """"Sonntag, 14. Juli 2024 · vor 2 Jahren" — the years-ago part is the point.

    A bare date says little; "vor 2 Jahren" is what makes an old photo land.
    """
    if taken is None:
        return ""

    text = language.header_date(taken)

    today = datetime.now(tz=taken.tzinfo)
    years = today.year - taken.year - ((today.month, today.day) < (taken.month, taken.day))
    if years >= 1:
        return f"{text}  ·  {language.years_ago(years)}"
    return text
