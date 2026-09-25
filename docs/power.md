# Power and battery life

There is no mains socket behind this panel. Everything about the firmware's
shape — one wake a day, a header that carries nothing time-of-day dependent,
a render that happens on a server rather than on the device — follows from that.

This page collects what the battery actually does, what runtime to expect, and
how to tell a healthy device from a sick one.

## The energy budget

A 2000 mAh cell, and the load splits like this:

| Item | Cost | Share of a day |
|---|---:|---|
| 24 h of deep sleep | ~80 mAh | ~99 % |
| One wake: WiFi, download, panel refresh, battery read | ~0.6 mAh | ~1 % |

**The sleep floor dominates, by two orders of magnitude.** That single fact
decides every trade-off here:

- Refreshing more often is nearly free. Three extra refreshes a day cost about
  1.8 mAh, roughly half an hour of sleep — about 2 % more drain.
- Failing to reach deep sleep is catastrophic. A device that stays awake drains
  in about a day.

So the thing worth protecting is not the refresh count. It is that every wake
ends in `deep_sleep`, every time. See
[display.md](display.md#sleep_evaluation-the-full-slot-arithmetic) for how the
firmware guarantees that, and
[operations.md](operations.md#cases-already-diagnosed) for the three separate
bugs that broke it.

The sleep floor is about 2.5–3.3 mA, higher than an ESP32-S3 datasheet suggests.
The octal PSRAM is the main contributor, and it is not optional: the decoded
800x480 RGB565 frame is 768 KB and has nowhere else to live.

## Expected runtime

| State | Drain | Runtime on a full charge |
|---|---:|---|
| Healthy, one refresh a day | 0.12–0.17 %/h | **25–33 days** |
| Never reaches deep sleep | 3.57 %/h | ~1.2 days |
| Hourly retry loop (no valid Home Assistant time) | 0.51 %/h | ~8 days |
| One night awake during an OTA | 32 % in 8 hours | — |

The measured live figure is 0.17 %/h, or about 25 days. Take the top row as the
number to plan around: charge roughly monthly.

Cold helps nothing and hurts a little — a kitchen is fine, an unheated porch in
winter will read low and recover when it warms.

## What the percentage actually is

The cell sits behind a 1:2 resistor divider on `GPIO1`, read by the ADC at 12 dB
attenuation. `battery_voltage` multiplies by 2.0 to undo the divider and
publishes volts; `battery_level` maps volts to percent through a
`calibrate_linear` curve (`e1002-kitchen.yaml:236-296`):

| V | % | | V | % |
|---:|---:|---|---:|---:|
| 4.15 | 100 | | 3.68 | 40 |
| 3.96 | 90 | | 3.58 | 30 |
| 3.91 | 80 | | 3.49 | 20 |
| 3.85 | 70 | | 3.41 | 10 |
| 3.80 | 60 | | 3.30 | 5 |
| 3.75 | 50 | | 3.27 | 0 |

It is a lithium discharge curve, so it is deliberately not linear: the long flat
middle is compressed and the steep ends are stretched. A reading of 3.919 V is
75.7 %.

Three defensive details, each of which exists because of a real failure:

- **Eight samples, averaged over five.** One ADC reading on this divider scatters
  by a few percent, which is the same order as a whole day's real discharge.
  Eight samples leave room for three to be dropped and still fill the window.
- **`send_every: 1`.** The moving average publishes a running value from the
  first sample. With `send_every: 5` a single dropped sample left the sensor at
  `NaN` until the next day's wake.
- **`NaN` returns nothing rather than publishing.** Publishing `NaN` blanks the
  entity in Home Assistant, which looks exactly like a device that is merely
  asleep. Keeping the last good value keeps the failure visible as a stale
  timestamp instead of an empty one.

### Why the reading happens at the end of the cycle

Measuring at boot measures the WiFi radio, not the battery. That is not a small
error: an early version read during association and reported **3.57 %/h**, a
projection of 1.2 days, on a device that was in fact fine. `measure_battery`
runs after the refresh, waits 2 s for the rail to settle, and explicitly turns
the divider on first in case a reordered wake skipped that step.

The sleep script then waits for the API to flush before cutting the radio. The
device used to sleep about five milliseconds after publishing: the state change
was queued, the connection went down before the packet left, and the percentage
was correct on the device and absent in Home Assistant every single morning.

## Measuring it yourself: `tools/battery_report.py`

```sh
export HA_URL=http://homeassistant.local:8123
export HA_TOKEN='...'          # from a secure source, not your shell history
python3 tools/battery_report.py
python3 tools/battery_report.py --hours 72
```

It reads `/api/history/period/...` and prints the number and span of readings,
the cleaned discharge stretches, `%/h` and `mAh/h`, a projection in days, the
refresh counter, and a rough split between awake and sleep time.

Entity IDs and cell size are constants at the top — adjust them for your install
(`battery_report.py:29-33`):

```python
BATTERY_ENTITY = "sensor.e_ink_frame_battery_level"
REFRESH_ENTITY = "sensor.e1002_test_display_refreshes"
CELL_MAH = 2000.0
```

It discards `unknown`, `unavailable` and non-numeric states, anything outside
`0 < value <= 100` (USB charging produces artifacts like `-371 %`), and segments
shorter than 20 minutes, where ADC noise dominates the real load. A rise of more
than 1.5 % ends a discharge segment, so charging is never counted as
consumption (`battery_report.py:53-105`).

`tools/battery_watch.sh` and `tools/com.foredogs.batterywatch.plist` run the same
report on a schedule if you want a log rather than a one-off.

## The estimate on the panel

The small `~23 Tage` under the battery icon is computed in Home Assistant, from
long-term statistics rather than states, and is withheld until it has enough
history to be worth printing. The reasoning and the exact thresholds are in
[dashboard.md](dashboard.md#runtime-estimate).

Short version: it is absent for the first two days after a charge, and that is
deliberate.

## Charging

The panel charges over USB-C. Two practical notes:

- While charging, the percentage is meaningless — the divider sees the charger,
  not the cell. Readings during charging are what `battery_report.py` filters
  out.
- The runtime estimate resets itself. The next reading above the previous one by
  more than 1.5 % starts a new cycle, and the estimate disappears for two days
  until the new cycle has a slope.

There is no fuel gauge IC. Everything above is a voltage curve, so treat the
percentage as an indication and the *trend* as the real signal.
