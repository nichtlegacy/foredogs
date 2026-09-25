# The dashboard

What the panel actually shows, where every number on it comes from, and how to
point it at your own sensors.

The renderer lives in `custom_components/foredogs/dashboard_render.py`; the data
collection that feeds it is `dashboard_service.py`. Nothing here runs on the
display — the device downloads a finished 800x480 PNG.

## Configuring the render call

`foredogs.render_dashboard` takes its entity wiring per call rather than from a
configuration file, so one Home Assistant can drive several panels off different
sensors. Every field:

| Field | Required | Default | What it does |
|---|---|---|---|
| `weather_entity` | yes | — | Current conditions and the daily forecast. Must be a `weather.` entity that supports the `get_forecasts` action. |
| `inside_temp_entity` | no | — | Indoor temperature. The display's own SHT40 is the obvious choice. Omitted, the cell is left empty. |
| `inside_humidity_entity` | no | — | Indoor humidity. |
| `battery_entity` | no | — | Battery percentage of the panel. Also the source for the runtime estimate under the icon. |
| `waste_sensors` | no | `[]` | Bins exposed as sensors. Each entry: `entity_id`, `label`, and `kind` (`rest`, `bio`, `papier`, `gelb`) which picks the block colour. |
| `waste_calendars` | no | `[]` | Bins that only exist as calendar events. Each entry: `entity_id`, `label`, `kind`, and `match`, a substring filter on the event summary. |
| `image_dir` | no | — | Folder of generated pictures, relative to `www/daily_foredogs`. Preferred over `source_image`: the picker resolves today's date first and falls back through weather pools to the newest image, so a day the generator did not run still shows a picture. |
| `source_image` | no | `foredogs_original.png` | A single file to composite. Use the *original*, not the optimized copy, so the renderer dithers it itself. |
| `keep_days` | no | `0` | Delete dated images in `image_dir` older than this. `0` keeps everything. |
| `language` | no | `de` | Language of the drawn labels: `de` or `en`. See [Languages](#languages). |
| `dither_photo` | no | `true` | Atkinson dithering on the photo region. Turn it off only to compare. |
| `page` | no | `1` | 1 = home, 2 = weather detail, 3 = photo. The panel passes this when its right button turns a page. |
| `page_count` | no | `1` | How many pages exist. At `1` the page marker is not drawn at all — so this has to match reality, or the panel claims to be alone when it is not. |
| `photo` | no | `{}` | Source for page 3. See [Photo source and Immich](#photo-source-and-immich). |

A working call, which is also `examples/homeassistant/render_dashboard_script.yaml`:

```yaml
action: foredogs.render_dashboard
data:
  weather_entity: weather.forecast_home
  inside_temp_entity: sensor.e1002_playground_temperature
  inside_humidity_entity: sensor.e1002_playground_humidity
  battery_entity: sensor.e1002_playground_battery_level
  image_dir: dogs
  keep_days: 30
  waste_sensors:
    - entity_id: sensor.residual_waste
      label: Residual
      kind: rest
    - entity_id: sensor.paper_waste
      label: Paper
      kind: papier
  waste_calendars:
    - entity_id: calendar.waste_collection
      label: Bio
      kind: bio
      match: bio
```

The service returns `changed: true` when the rendered content actually differs
from the previous render. The device uses that to skip a panel refresh it does
not need — see [`fingerprint(data)`](#fingerprintdata).

### Languages

Everything the renderer writes itself — the date line, condition names, the
daylight caption, the countdowns, the page marker, the runtime estimate — is
drawn in the language the call asks for:

```yaml
action: foredogs.render_dashboard
data:
  weather_entity: weather.forecast_home
  language: en        # omit for German
```

German is the default, because that is the language the panel was built in. An
unknown code logs a warning and falls back rather than failing the render, and
`en-GB` or `de_DE` resolve to their base language.

**What is not translated:** your own waste bin labels. Those come from
`waste_sensors` and `waste_calendars` and are drawn as written — the renderer
has no business renaming `Gelber Sack`. The same applies to an Immich album
name in the photo caption.

The language is part of the [fingerprint](#fingerprintdata), so switching it
repaints the panel on the next wake instead of waiting for the weather to
change.

#### Adding one

One file, `custom_components/foredogs/languages/<code>.py`:

```python
from .base import Language


class Dutch(Language):
    code = "nl"
    name = "Dutch"

    conditions = {"rainy": "Regen", ...}
    months = ("januari", ...)          # 12, January first
    weekdays_long = ("maandag", ...)   # 7, Monday first
    weekdays_short = ("ma", ...)       # 7, two or three characters
    labels = {"DAYLIGHT": "DAGLICHT", ...}

    def waste_when(self, days_until: int) -> str:
        ...


LANGUAGE = Dutch()
```

Then add it to `_MODULES` in `languages/__init__.py`. Anything left unset falls
back to `base.Language`, so a new language is usable long before it is
complete — and `tests/test_languages.py` fails if a module in the folder was
never registered, or if a shipped language is missing a condition slug, a
label, or a table of the right length.

`base.Language` is the contract, and its docstrings say what each method is
for. The methods exist because the hard part is not the words: `24.09.` against
`24 Sep`, `14-17 Uhr` against `14:00-17:00`, and the plural of `Tag`.

### What each part of the panel needs

| Region | Entity it needs | Behaviour when missing |
|---|---|---|
| Picture | `image_dir` or `source_image` | Renders the chrome over a blank area. |
| Header date and celebration | none — clock and the generator's status file | Always drawn. |
| Today's weather | `weather_entity` | The service raises; this one is required. |
| Inside temperature/humidity | `inside_temp_entity`, `inside_humidity_entity` | Cell stays empty. |
| `TAGESLICHT` sunrise/sunset | `sun.sun` | Falls back to no times. |
| Waste blocks | `waste_sensors` and/or `waste_calendars` | No blocks drawn; the forecast band expands. |
| Battery and runtime estimate | `battery_entity` | Neither icon nor estimate is drawn. |
| Four-day forecast | `weather_entity` daily forecast | Fewer columns. |

## Geometry of the `800x480` panel

Constants in `dashboard_render.py:65-93`:

| Constant | Value | Meaning |
|---|---:|---|
| `WIDTH` | `800` | Panel width. |
| `HEIGHT` | `480` | Panel height. |
| `HEADER_H` | `64` | Black header. |
| `FOOTER_H` | `86` | Home page with four forecast days plus battery. |
| `SLIM_FOOTER_H` | `44` | Pages 2 and 3. |
| `SIDEBAR_W` | `210` | Sidebar. |
| `IMAGE_X` | `0` | Picture on the left. |
| `IMAGE_Y` | `64` | Picture below the header. |
| `IMAGE_W` | `590` | `800 - 210`. |
| `IMAGE_H` | `330` | `480 - 64 - 86`. |
| `SIDEBAR_X` | `590` | Sidebar starts right of the picture. |
| `SIDEBAR_Y` | `64` | Sidebar below the header. |
| `SIDEBAR_H` | `330` | Sidebar height. |
| `FOOTER_Y` | `394` | `480 - 86`. |
| `PAD` | `12` | Edge padding. |
| `GAP` | `6` | Gap between sidebar blocks. |
| `MIN_WASTE_BLOCK_H` | `58` | Legibility floor. |

Absolute geometry avoids layout surprises. The panel cannot scale, and a
one-pixel error stays visible on the device.

## Spectra 6 palette

Everything drawn as UI chrome uses exactly these six device colours
(`dashboard_render.py:95-105`):

| Name | RGB |
|---|---|
| `BLACK` | `(0, 0, 0)` |
| `WHITE` | `(255, 255, 255)` |
| `RED` | `(200, 0, 0)` |
| `GREEN` | `(0, 150, 0)` |
| `BLUE` | `(0, 0, 200)` |
| `YELLOW` | `(255, 230, 0)` |

Semantics:

- `RED`: urgent collection date, battery emergency range, sunrise.
- `GREEN`: organic waste and battery above 40 percent.
- `BLUE`: paper, cold temperatures, sunset, precipitation.
- `YELLOW`: heat, recyclables, the weather curve area.
- `BLACK`/`WHITE`: structure and text contrast.

`BIN_COLORS` and `BIN_TEXT_COLORS` are in `dashboard_render.py:159-171`. Yellow
gets black text, because white on yellow is unreadable.

## Fonts and the fallback chain

Home Assistant OS had no fonts at all on the target installation (`fc-list` was
empty). Pillow therefore fell back silently to a tiny bitmap font. The fix is a
bundled Inter under `/config/foredogs_data/fonts/`.

Bold candidates (`dashboard_render.py:302-310`):

1. `/config/foredogs_data/fonts/Inter-Bold.ttf`
2. `/config/foredogs_data/fonts/Inter-SemiBold.ttf`
3. repository path `foredogs_data/fonts/Inter-Bold.ttf`
4. repository path `foredogs_data/fonts/Inter-SemiBold.ttf`
5. `/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf`
6. `/System/Library/Fonts/Helvetica.ttc`

Regular candidates (`dashboard_render.py:312-319`):

1. `/config/foredogs_data/fonts/Inter-Medium.ttf`
2. `/config/foredogs_data/fonts/Inter-Regular.ttf`
3. repository path `foredogs_data/fonts/Inter-Medium.ttf`
4. repository path `foredogs_data/fonts/Inter-Regular.ttf`
5. `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf`
6. `/System/Library/Fonts/Helvetica.ttc`

The fonts are not committed to this repository. Download Inter (or any other
TrueType family) and place it in `/config/foredogs_data/fonts/`.

All dashboard sizes are bold (`64`, `34`, `24`, `18`, `14`, `11`). Against six
colours, thin regular strokes break into isolated pixels after quantization;
size and colour differences carry the hierarchy instead
(`dashboard_render.py:341-355`).

With no TrueType file at all, the renderer logs:

```text
No TrueType font found — install one in /config/foredogs_data/fonts/. Falling back to Pillow's bitmap font; text will look wrong.
```

## Dithering and quantization

### Why Atkinson rather than full Floyd-Steinberg?

`atkinson_dither()` spreads the error as `err = (old - nearest) / 8.0` across six
neighbours (`dashboard_render.py:936-983`):

- current row: `x+1`, `x+2`,
- next row: `x-1`, `x`, `x+1`,
- row after that: `x`.

Six of eight shares are distributed: **75 percent error diffusion**.
Floyd-Steinberg pushes 100 percent onward. On a layout made mostly of flat UI
areas, text and six colours, full diffusion produces colour fringing and smeared
fills. Atkinson keeps edges cleaner and accepts a smaller tonal range in return
(`dashboard_render.py:936-945`).

### NumPy fallback

Atkinson needs NumPy. Without it the renderer uses Pillow's Floyd-Steinberg
rather than failing (`dashboard_render.py:52-61`, `946-957`). That is a
deliberate degradation path, which is why `numpy` is not hard-pinned in
`manifest.json`.

### Picture versus chrome

- `dither_region()` is applied only to photo and picture regions.
- Before that, contrast is raised by `1.2` and saturation by `1.3`
  (`dashboard_render.py:998-1010`).
- `quantize_flat()` maps UI chrome without diffusion (`Image.Dither.NONE`).
- Text, black frames and colour blocks therefore stay clean while the picture
  keeps enough tonal structure.

## `DashboardData`: the full contract

The dataclass is in `dashboard_render.py:242-292`. `dashboard_service.py`
assembles it; the renderer only ever draws that object.

| Field | Type | Purpose |
|---|---|---|
| `now` | `datetime` | Local render time; header date and fingerprint hour. |
| `outside_temp` | `float \| None` | Current outdoor temperature. |
| `outside_condition` | `str` | Home Assistant condition slug; header and icon. |
| `outside_humidity` | `float \| None` | Outdoor humidity; header metric. |
| `inside_temp` | `float \| None` | Indoor temperature from the E1002 SHT40. |
| `inside_humidity` | `float \| None` | Indoor humidity. |
| `wind_speed` | `float \| None` | Wind speed. |
| `uv_index` | `float \| None` | UV; shown as a header metric only from `>=3`. |
| `precipitation` | `float \| None` | General precipitation field; currently no dedicated header path. |
| `precipitation_probability` | `float \| None` | Fallback rain metric when there is no `rain_total`. |
| `feels_like` | `float \| None` | Apparent temperature; collected but not drawn separately. |
| `sun_rise` | `str` | Local time, e.g. `05:42`. |
| `sun_set` | `str` | Local time, e.g. `21:38`. |
| `temp_high` | `float \| None` | Daily maximum from the daily forecast. |
| `temp_low` | `float \| None` | Daily minimum from the daily forecast. |
| `rain_total` | `float \| None` | Daily precipitation from the relevant hours. |
| `rain_window` | `str` | Simplified window, e.g. `14-17 Uhr`. |
| `daylight_hours` | `str` | Computed day length, e.g. `15h 42m`. |
| `page` | `int` | Renderer page: `1`, `2` or `3`. |
| `page_count` | `int` | Total for the page marker. |
| `battery` | `float \| None` | Battery percentage. |
| `battery_voltage` | `float \| None` | Voltage; collected for preview, deliberately not shown on the panel. |
| `waste` | `list[WasteEntry]` | Sorted upcoming collection dates. |
| `forecast` | `list[ForecastDay]` | Up to seven upcoming days; page 1 uses four. |
| `image_path` | `Path \| None` | Selected generator picture. |
| `hourly` | `list[HourPoint]` | Up to 24 hours for page 2. |
| `photo` | `PhotoInfo \| None` | Photo for page 3. |
| `photo_error` | `str` | Visible reason when the photo source is missing. |

## Data collection in `dashboard_service.py`

### `_state_float_sticky`: why the cache exists

`_state_float_sticky()` (`dashboard_service.py:63-92`) checks, in this order:

1. the **live state** from `hass.states`,
2. the **process cache** `_LAST_KNOWN`,
3. the **recorder**: the last numeric value of the past twelve hours.

The reason: the E1002 sleeps for most of the cycle, so its own entities are
frequently `unavailable` during a render. After a Home Assistant restart the
process cache is empty as well, before the device next wakes. A 25-minute-old
temperature, humidity or battery value is more useful on a static dashboard than
`--.-`.

The recorder query (`dashboard_service.py:95-123`):

- period: `utcnow() - 12h` to `utcnow()`,
- `history.state_changes_during_period(..., include_start_time_state=True, no_attributes=True)`,
- scanning backwards for the last numeric value,
- if the recorder is missing, busy or broken: `None`, and the render still runs.

### Weather state and daily values

`collect_dashboard_data()` (`dashboard_service.py:126-245`):

- reads the current weather attributes `temperature`, `humidity`, `wind_speed`,
  `uv_index`, `apparent_temperature`,
- reads the daily forecast via `weather.get_forecasts` and sets `temp_high`,
  `temp_low` and the daily condition slug,
- reads one shared hourly list for the rain summary and page 2,
- reads the sun times,
- attaches the picture path and optionally the page 3 photo.

The header uses the daily high and low rather than the current temperature: the
render happens in the morning and should say what the day will be like, not how
cold it happens to be at render time (`dashboard_service.py:206-227`).

### `_forecast_entries`

`_forecast_entries()` (`dashboard_service.py:302-331`) wraps both forecast
types:

```python
await hass.services.async_call(
    "weather",
    "get_forecasts",
    {"entity_id": weather_entity, "type": kind},
    blocking=True,
    return_response=True,
)
```

The service response is unpacked by looking for a dict with a `forecast` key. A
missing forecast is not a fatal render error.

### `_collect_forecast`

`_collect_forecast()` (`dashboard_service.py:475-529`):

- requests `daily`,
- drops today, because today is already in the header,
- uses `Morgen` for offset 1, otherwise `Mo`/`Di`/...,
- takes at most `FORECAST_DAYS_LONG = 7` days.

One forecast query therefore feeds both pages: page 1 slices four days off the
front, page 2 draws the whole week.

### `_collect_today`, `RAIN_THRESHOLD_MM`, `RAIN_LOOKAHEAD_H`

Constants (`dashboard_service.py:39-41`):

| Name | Value | Actual behaviour |
|---|---:|---|
| `RAIN_THRESHOLD_MM` | `0.2` | Hours below 0.2 mm count as dry and are not reported as rain. |
| `RAIN_LOOKAHEAD_H` | `36` | Not used by the current `_collect_today()`; a leftover from an earlier lookahead design. |

`_collect_today()` (`dashboard_service.py:372-404`) takes only hours whose local
date is **today**, filters `mm >= 0.2`, sums `rain_total` and builds one
contiguous reading window from the first to the last wet hour. Two separate
showers are deliberately merged into one wider window: the question being
answered is "when do I need a jacket?", not meteorological segmentation.

`RAIN_LOOKAHEAD_H` is still mentioned in the local `tools/preview_dashboard.py`
scaffold (`preview_dashboard.py:67-69`, `304-317`), where the old "next rain"
path is likewise unfinished. Do not document it as active 36-hour logic.

### `_collect_sun_times`

`_collect_sun_times()` (`dashboard_service.py:264-300`) reads:

- `sensor.sun_next_rising`,
- `sensor.sun_next_setting`.

ISO timestamps are localized and stored as `HH:MM`. Day length is computed from
the raw datetimes. If sunset comes out before sunrise, a day is added — which
prevents a negative day length on a render that lands between the two events.

### Waste sensors and waste calendars

Sensor values are interpreted by `parse_waste_sensor()`
(`dashboard_service.py:168-182`, `dashboard_render.py:1166-1200`).
`waste_collection_schedule` sensors carry the real collection dates as attribute
keys such as `2026-07-29`; the human-readable state `in 3 Tagen` is only a
fallback.

Calendars are grouped so that one `calendar.get_events` call serves several bin
specifications (`dashboard_service.py:184-202`, `406-472`). The query window is
today through 60 days. `match` filters summary text case-insensitively. Per
`kind`, the earliest date wins.

## Dashboard page 1

### Header: `draw_header`

`draw_header()` (`dashboard_render.py:552-657`):

- a black `64px` bar,
- the full German weekday plus date on the left,
- the daily high large and the daily low smaller on the right,
- the weather condition and a vector icon,
- optional metrics in the middle: rain amount and window or a percentage
  fallback, wind, humidity, UV from 3 upwards.

Examples of the deliberately coarse colour semantics:

- daily high `>=25°C` → `YELLOW`,
- daily high `<=0°C` → `BLUE`,
- otherwise `WHITE`.

An older "next rain in 1h" countdown was replaced by a daily total plus a time
window. A countdown is wrong by the evening; a total and a window stay useful
all day.

### Sidebar: `TAGESLICHT`

`_draw_sidebar()` (`dashboard_render.py:688-757`) reserves exactly `90px` at the
top for:

```text
TAGESLICHT              15h 42m
☀ 07:13
☾ 19:36
```

- sunrise: `RED`,
- sunset: `BLUE`,
- a sun and a crescent rather than up and down arrows: both say the same
  thing, but an icon says it without being read, which is the whole point of a
  panel meant to be understood from across the room,
- missing times: `--:--`.

Three details decide whether a crescent reads as a moon at fourteen pixels
across, and the first version got all three wrong:

- The punch is offset **up as well as right**. A purely horizontal offset
  leaves a symmetric lune, which reads as half a disc rather than a moon.
- The punch carries **no outline**. Outlining it draws a second circle, and the
  result reads as two overlapping discs.
- The punch is painted in whatever the icon sits on — black in the header,
  white in the sidebar. The same helper draws the `clear-night` weather icon,
  where a black punch on the white forecast band used to render a bite.

Both icons share a radius, so neither looks like the other's afterthought.

This block used to show the indoor temperature. On a render in the small hours
that was the coldest moment of the day and already misleading by breakfast.
Sunrise and sunset shift daily and stay true all day
(`dashboard_render.py:698-727`).

### Waste blocks

Below `TAGESLICHT` the collection blocks share whatever space is left. At least
`58px` per block keeps them legible; if that does not fit, later blocks are cut
and the earliest ones are kept (`dashboard_render.py:731-756`).

- `rest` → black,
- `bio` → green,
- `papier` → blue,
- `gelb` → yellow,
- `urgent` for today or tomorrow → red with a black double frame.

A non-urgent block:

```text
RESTABFALL                 in 3 Tagen
Mittwoch 29.07.
```

An urgent block promotes `HEUTE`/`MORGEN` to the large line and shows the exact
date smaller. The label is truncated so that a long bin name does not collide
with the countdown (`dashboard_render.py:760-801`).

### Forecast band and battery

`_draw_footer()` (`dashboard_render.py:804-889`):

- four upcoming days,
- daily high large, low small,
- icon and short condition,
- precipitation in millimetres in blue,
- a dedicated battery and page-marker area on the right, `118px` reserved.

The page marker is only drawn when there is more than one page. "Seite 1/1"
answers a question nobody asked and costs the gauge half its cell, so on a
single-page display the battery takes the whole cell and is centred in it.

Centring uses the block's measured ink extent rather than its nominal size. The
runtime estimate turned out to be wider than the gauge above it, and a text's
ink box is taller than its nominal size, so centring against constants left the
block visibly off in both axes.

`draw_battery()` (`dashboard_render.py:891-920`):

- unknown → empty battery plus `—%`,
- above 40% → green,
- above 15% → yellow,
- 15% or below → red.

Voltage is not displayed: a voltage is not understandable status information as
you walk past. A percentage plus a visible gauge is the better UI.

### Runtime estimate

Under the gauge, in small type: `~23 Tage`, the days left at the current
discharge rate.

It is computed in `dashboard_service.py` from **long-term statistics**, not from
states. The panel reports once a day, and the recorder keeps ten days by
default, so a forecast built on states would have at most ten points and would
vanish entirely after a quiet week. Statistics are hourly, survive purging, and
reach back as far as the sensor has existed.

Only the stretch since the last charge counts: a rise of more than
`_ESTIMATE_CHARGE_TOLERANCE` (1.5 %) means the cell was charged, and everything
before it describes a different cycle. The estimate is withheld unless that
stretch spans at least `_ESTIMATE_MIN_SPAN_DAYS` (2.0) and shows at least
`_ESTIMATE_MIN_DROP` (2.0 %) of drop, and it is capped at
`_ESTIMATE_MAX_DAYS` (99), shown as `99+ Tage`. The window it looks back over is
`_ESTIMATE_WINDOW`, 30 days (`dashboard_service.py:176-184`).

Withholding it is the point. A confident wrong number on this panel is exactly
what let a draining battery go unnoticed for nine days, and a forecast is the
kind of figure nobody double-checks. The line is simply absent for the first
days after a charge or a firmware change — which is also why it did not appear
immediately after the battery fix, even though the gauge did.

## Page 2: weather detail

The page the right button turns to. Implemented in `dashboard_pages.py`.

It answers two questions page 1 cannot: *when* today's weather happens, and
which of the coming days is the one worth planning around.

### The 24-hour curve

Twenty-four points, one per hour, starting at the current hour. The area under
the line is filled yellow and the line itself is black and three pixels wide —
a one-pixel diagonal breaks into disconnected dots when dithered to six
colours, whereas a filled shape keeps its silhouette.

**Four numbers, not twenty-four.** The curve is labelled at its two ends plus
the warmest and coldest hour. Four are read; twenty-four are skimmed past. Each
sits on whichever side of its point is free — above in the white, or below on
the yellow fill where the curve climbs into the space above. `max` and `min` on
the right edge name the extremes of the plotted window.

**The blue bars along the hour axis are rainfall — one bar per hour, in
millimetres.** They hang below the temperature baseline with a gap, so a heavy
hour does not read as part of the curve.

| | |
|---|---|
| Nothing drawn | at or below `0.05 mm` in that hour |
| Bar height | proportional to `RAIN_FULL_SCALE_MM`, which is `4.0 mm/h` |
| Above 4 mm/h | clipped to full height |

The clipping is deliberate. The chart supports one decision — coat or no coat —
and past "heavy" the exact figure stops changing the answer.

Hour labels appear every third hour, which is as dense as 24 points across
700 px can be without the numbers touching.

With fewer than two hours of forecast available, the page says
`keine Stundenvorhersage` rather than drawing an empty frame.

### The week band

One row per day, up to seven. Today is not in it — today is the header — so the
first row is tomorrow.

Left to right: weekday, low, **range bar**, high, icon, condition, millimetres.

**The bar between the two temperatures is that day's low-to-high range, drawn
on a scale shared by every row.** That is the whole point of it: the rows are
comparable, so "Tuesday is the warm one" is answerable without reading a single
digit.

| What you see | What it means |
|---|---|
| Bar further right | warmer day |
| Wider bar | bigger swing between night and afternoon |
| Thin line behind the bar | the full scale, so a short range still reads as a position rather than a stray dash |
| Red bar | that day's high is at or above 28 °C |
| Blue bar | the high is at or below 5 °C |
| Black bar | everything in between |
| Blue low number | that night drops to 5 °C or colder |

The scale auto-ranges over the days actually shown and is never narrower than
6 °C — otherwise a settled week of 17–19 °C would be drawn as dramatic swings.

The millimetre figure on the right is that day's total, and is omitted when
nothing is expected.

### The footer

Sunrise, sunset, inside temperature and wind as label/value pairs, then the
page marker and the battery gauge in the same corner as on page 1 — a gauge
that moved between pages would read as two different devices.

## Page 3: photo

`render_photo_page()` (`dashboard_pages.py:333-411`) draws a photo full-bleed
down to the slim footer. A black `34px` caption bar lays date, age and source
over it. White on black stays readable over any photo.

`PhotoInfo` carries:

- `path`,
- `taken`,
- `caption`,
- `source`,
- `asset_id`.

The page renders correctly but nothing asks for it. The firmware turns between
pages 1 and 2 only, so `page: 3` reaches the renderer solely from a hand-made
service call or the preview tool. Wiring it in would mean a third stop on the
button, on a panel that is awake for forty seconds a day — see
[display.md](display.md#turning-the-page).

## `fingerprint(data)`

`fingerprint()` is in `dashboard_render.py:1127-1163`. It includes:

- the page,
- the local hour (`%Y-%m-%d-%H`),
- outdoor temperature and condition,
- indoor temperature,
- up to three collection dates,
- the first four forecast days,
- up to 24 hours on page 2,
- the `asset_id` or path on page 3.

Deliberately excluded: minutes, battery percentage and other continuous values.
Otherwise every wake would count as a change. The hour is included because older
render paths had time-dependent header and rain logic that differed hourly. The
current firmware ignores `changed` and always refreshes after a successful
download.

## Picture selection: six usable stages

`image_source.py` is deliberately decoupled from the generator. `pick_image()`
(`image_source.py:95-158`) implements six usable match stages:

1. **Today at the top:** `dogs/YYYY-MM-DD.png`.
2. **Today in the condition folder:** `dogs/<folder>/YYYY-MM-DD.png`.
3. **Condition pool:** any picture in `dogs/<folder>/`, seeded deterministically
   by date and folder.
4. **Most recent dated picture:** the newest `YYYY-MM-DD.*` anywhere under
   `dogs/`.
5. **Any picture:** a direct file in the root, also randomized stably per day.
6. **Explicit fallback:** the single `source_image`.

If even stage 6 is missing, `pick_image()` returns `(None, "nothing found")` and
the renderer draws `kein Bild`. `CONDITION_FOLDERS` (`image_source.py:40-59`)
maps, for instance, `pouring` and `snowy-rainy` onto the `rainy` pool, `hail`
onto `snowy`, and `clear-night` onto `sunny`.

`prune_old()` deletes only files with a date in the name, and only when
`keep_days > 0` (`image_source.py:161-181`). Hand-placed, undated files survive.

The weather subfolders exist as a concept in the code but are unpopulated in a
setup driven by the macOS generator: its publishes land in `dogs/` as the day's
file and do not automatically create condition-specific stock pictures.

## Photo source and Immich

For page 3, `photo_source.py` can use:

1. an Immich album via `api_key` plus `album_id`,
2. an album-bound `share_key`,
3. a local fallback folder.

Assets are filtered by aspect ratio (`MIN_ASPECT = 1.1`), selected
deterministically per rotation slot, and written atomically into the cache as
`page3_photo.jpg` (`photo_source.py:39-47`, `124-159`).

Worth knowing: in the setup this was built for, Immich is only an **archive
sink** for the Mac pictures. Immich as a live display source is prepared
functionality rather than a wired-up path.

