# Display firmware

## Status and basic idea

`esphome/e1002-kitchen.yaml` is the firmware for the Seeed reTerminal E1002.
All credentials in it are `!secret` references; the real values belong in the
ESPHome `secrets.yaml` (see `esphome/secrets.yaml.example`).

The firmware is deliberately thin:

- Home Assistant renders the complete `800x480` picture.
- ESPHome waits for network and API, requests a render, downloads the PNG and
  calls `component.update`.
- The e-paper holds the image during deep sleep.
- The ESP32 computes no forecast and draws no dashboard elements.

The file names the build invocation explicitly:

```zsh
ESPHOME_BUILD_PATH=/tmp/esphome-build \
  esphome run esphome/e1002-kitchen.yaml --device /dev/cu.usbserial-210
```

Always set a build path free of square brackets. ESP-IDF's `kconfgen`
misparses `[` and `]` in a path, so a checkout under a directory like
`[Github]` fails to build unless `ESPHOME_BUILD_PATH` points elsewhere
(`esphome/e1002-kitchen.yaml:18-22`).

## `substitutions`

All substitutions are in `esphome/e1002-kitchen.yaml:24-59`.

| Key | Value | What it controls |
|---|---|---|
| `device_name` | `e1002-playground` | ESPHome device name and entity ID prefix. The historical name is kept stable so Home Assistant keeps the same device and registry set. |
| `friendly_name` | `Kitchen Display` | Display name of the Home Assistant device. |
| `timezone` | `Europe/Berlin` | Home Assistant time and slot arithmetic. |
| `refresh_hour` | `5` | Scheduled slot hour. |
| `refresh_minute` | `45` | Scheduled slot minute; normal wake at 05:45. |
| `retry_minutes` | `60` | Fallback when the time is missing, invalid or too close. |
| `page_count` | `3` | Intended total number of pages; not used by the current firmware code. |
| `rotate_every_wakes` | `2` | Intended page change every two wakes; not used by the current firmware code. |
| `interaction_window_s` | `25` | Intended stay-awake window after a button press; not used by the current firmware code. |

WiFi credentials, the API encryption key, the OTA password, the fallback AP
password and `dashboard_url` are `!secret` references rather than substitutions.

`dashboard_url` is a secret not because the URL is sensitive but because it
names a host on your network — and because it should almost always be an IP
address. The ESP32's HTTP client does not resolve other hosts over mDNS, so a
`.local` name that works from a laptop can still fail on the device.

The comments in the file describe a three-page concept, but the active firmware
actions pass no page ID to Home Assistant. That difference matters and is
documented separately below.

## Hardware mapping

### Display and buses

| Function | Pin / config | Source |
|---|---|---|
| E-paper SPI clock | `GPIO7` | `spi.clk_pin` |
| E-paper SPI MOSI | `GPIO9` | `spi.mosi_pin` |
| SHT4x I2C SCL | `GPIO20` | `i2c.scl` |
| SHT4x I2C SDA | `GPIO19` | `i2c.sda` |
| Battery divider enable | `GPIO21` | `output.bsp_battery_enable` |
| Battery ADC | `GPIO1` | `sensor.battery_voltage` |
| Green button / manual refresh | `GPIO3` | GPIO binary sensor |
| Right white button / wake pin | `GPIO4` | GPIO binary sensor plus `deep_sleep.wakeup_pin` |
| Left white button | `GPIO5` | GPIO binary sensor |
| Onboard LED | `GPIO6`, inverted | `output.led_output` |
| Buzzer | `GPIO45`, `ledc`, `2000Hz` default | `output.buzzer_pwm` |

Sources: `esphome/e1002-kitchen.yaml:142-162`, `221-335`, `337-354`.

### ESP32 and network

```yaml
esp32:
  board: esp32-s3-devkitc-1
  framework:
    type: esp-idf

psram:
  mode: octal
  speed: 80MHz
```

Octal PSRAM allows the decoded RGB565 frame (`800*480*2 = 768 KB`) to live
outside normal SRAM. It also raises the measured deep-sleep floor to roughly
`2.5–3.3 mA`, and that PSRAM load is what dominates battery life
(`esphome/e1002-kitchen.yaml:77-84`, comment lines 13-16).

WiFi:

- `reboot_timeout: 0s`: with a weak signal around `-85 dBm`, do not fall into a
  reboot and drain loop.
- A fallback AP is configured.
- `http_request.timeout: 25s` and `watchdog_timeout: 30s`.

## Home Assistant API and OTA

```yaml
api:
  encryption:
    key: !secret api_encryption_key
  homeassistant_services: true
  homeassistant_states: true
  actions:
    - action: refresh_now
      then:
        - script.execute: force_refresh
    - action: panel_clean
      then:
        - script.execute: panel_clean
```

`homeassistant_services: true` is not optional convenience. Without that key,
ESPHome does not compile the code for outgoing `homeassistant.action` calls, and
the device's render request cannot work at all
(`esphome/e1002-kitchen.yaml:90-107`). `homeassistant_states: true` is needed
for `input_boolean.e1002_stay_awake`.

On the Home Assistant side the ESPHome config entry additionally has to allow
`allow_service_calls`. That part needs a **full Home Assistant restart**, not
just a reload. In the options flow both `allow_service_calls` and
`subscribe_logs` have to be set; both fields are marked required. Sending only
one writes an empty options dict and does not enable the service call.

### OTA rollback protection

```yaml
safe_mode:
  boot_is_good_after: 20s
```

ESPHome marks firmware as good only after 60 seconds by default. A normal wake
takes roughly 40 seconds and then enters deep sleep. The result with the old
default: OTA reports success, and on the next boot ESPHome silently rolls back
to the old firmware three times. `20s` fits inside a healthy wake cycle and
still catches an image that crashes immediately
(`esphome/e1002-kitchen.yaml:110-123`). Flashing over USB serial bypasses the
safe-mode mechanism entirely.

## Globals that survive RTC

Deep sleep loses normal RAM. Globals with `restore_value: yes` are restored
across sleep. In the current file (`esphome/e1002-kitchen.yaml:176-219`):

| Global | Type | Initial | Why persist? |
|---|---|---:|---|
| `refresh_count` | `int` | `0` | The refresh counter has to keep running across daily wakes and stay visible in the refresh sensor. |
| `wake_count` | `int` | `0` | Diagnoses the real number of wakes; exposes retry loops and unplanned wakes. |
| `last_clean_day` | `int` | `0` | Weekly panel maintenance must not run again after every wake. |
| `sounds_enabled` | `bool` | `false` | The buzzer setting should survive OTA and deep sleep; the kitchen stays silent by default. |
| `current_page` | `int` | `1` | Intended to keep the displayed page across sleep; not yet read by the active firmware path. |

Non-persistent globals:

| Global | Reason |
|---|---|
| `image_ready` | Only the state of the running download; set to `false` on every wake. |
| `panel_mode` | Only the current flush mode (`0` image, `1` white, `2` black). |
| `button_wake` | Intended to prevent competing rotation during a button interaction; unused in the current code. |

## Deep sleep and `online_image`

```yaml
deep_sleep:
  id: deep_sleep_ctrl
  wakeup_pin:
    number: GPIO4
    allow_other_uses: true
  wakeup_pin_mode: INVERT_WAKEUP
```

The sleep duration is **always set explicitly** in `sleep_evaluation`. There is
no automatic default duration. That prevents a wake that is still waiting for
WiFi or a download from accidentally sleeping early and missing the render state
(`esphome/e1002-kitchen.yaml:337-344`).

```yaml
online_image:
  - id: dashboard_image
    url: ${dashboard_url}
    format: PNG
    type: RGB565
    buffer_size: 65536
    update_interval: never
```

`buffer_size: 65536` is the **download chunk buffer**, not a maximum PNG file
size. The PNG is streamed and decoded into RGB565; a file of roughly `130 KB` is
perfectly fine. The decoded frame lives in PSRAM
(`esphome/e1002-kitchen.yaml:346-361`).

`image_ready` is set to `true` on `on_download_finished` and to `false` on
`on_error`. The download can therefore be controlled with two attempts and
without assuming a file size.

## The full `wake_cycle`

`wake_cycle` runs on every normal boot at priority `-100`
(`esphome/e1002-kitchen.yaml:64-75`, `384-436`). Before that, the priority-600
boot action switches GPIO21 on, because the voltage divider needs power before
the ADC is used.

### Step by step

1. **Counter and log:**

   ```yaml
   - lambda: 'id(wake_count) += 1;'
   - logger.log:
       format: "Wake #%d (refreshes so far: %d)"
   ```

   This makes unexpected repetitions visible in Home Assistant history and over
   UART.

2. **WiFi, up to 90 seconds:**

   ```yaml
   - wait_until:
       condition:
         wifi.connected:
       timeout: 90s
   ```

   Guard: an image download before an IP connection exists fails reliably. A
   weak kitchen WiFi can need roughly 20 seconds to associate.

3. **Home Assistant time, up to 40 seconds:**

   ```yaml
   - wait_until:
       condition:
         lambda: 'return id(ha_time).now().is_valid();'
       timeout: 40s
   ```

   Guard: slot arithmetic and night logic must not run on an invalid time. The
   RTC may carry a stale time, which does not mean Home Assistant is reachable.

4. **ESPHome API, up to 45 seconds:**

   ```yaml
   - wait_until:
       condition:
         api.connected:
       timeout: 45s
   ```

   Guard: the render action, the `stay_awake` state and the page settings all
   arrive over the API.

5. **Final time guard:**

   ```yaml
   - if:
       condition:
         lambda: 'return !id(ha_time).now().is_valid();'
       then:
         - logger.log: "No HA time; short retry sleep"
         - deep_sleep.enter:
             sleep_duration: 15min
   ```

   If the 40-second wait expires, the device does not continue computing without
   a time. 15 minutes saves battery compared with a long uncontrolled awake
   phase and gives the next wake a fresh chance.

6. **Weekly panel maintenance:** `weekly_clean_check` is started and awaited. It
   can run three flushes once every seven days, between 03:00 and 05:00.

7. **Render, download, refresh:** `fetch_and_maybe_refresh` is started and fully
   awaited with `script.wait`.

8. **Battery:** `measure_battery` is started and fully awaited.

9. **Sleep:** `sleep_evaluation` either honours `stay_awake` or sets the next
   slot.

## `fetch_and_maybe_refresh`

Source: `esphome/e1002-kitchen.yaml:455-520`.

### API gate and the three-second settle

The flow:

```yaml
- wait_until:
    condition:
      api.connected:
    timeout: 30s
- delay: 3s
- homeassistant.action:
    action: script.foredogs_render_dashboard
```

`api.connected` only means the encrypted ESPHome handshake has finished. Home
Assistant subscribes to device-initiated actions shortly afterwards. An
immediate call is dropped as:

```text
Home Assistant action 'script.foredogs_render_dashboard' dropped; no client connected
```

After the first fix, this remained:

```text
dropped; client has not subscribed to actions (yet)
```

There is no ESPHome condition for that subscription state. The three seconds are
therefore a deliberate window after the API gate. Only afterwards does Home
Assistant render reliably.

### Waiting for the Home Assistant render

After the action:

```yaml
- delay: 8s
```

Atkinson dithering takes roughly three seconds; eight seconds leaves room for
the forecast service, Pillow and the atomic write.

### Two download attempts

```yaml
- repeat:
    count: 2
```

Each attempt:

1. Only if `image_ready == false`, `component.update: dashboard_image`.
2. Wait up to 30 seconds for `image_ready`.
3. On failure, wait five seconds and start the second attempt.
4. After two failures:

   ```text
   No image after retries; leaving the current screen untouched
   ```

Why two attempts? Right after a wake, `wifi.connected` sometimes already reports
`true` while the IP stack is not yet fully ready for an HTTP request. A first
`Not connected to network` is therefore transient and must not destroy the last
good screen.

The firmware passes no waste list to the Home Assistant action. ESPHome cannot
serialize lists as action parameters (`Must be string, got EList`). The list
stays in the Home Assistant script, which means the waste configuration can be
changed without reflashing firmware.

## Refresh lock and the e-paper state machine

`component.update: epaper` does not block until the roughly 20-second panel
state machine finishes. `do_refresh` therefore stays active with `mode: single`
plus `delay: 25s` (`esphome/e1002-kitchen.yaml:522-533`).

Without an explicit 22 to 25 second pause, `mode: single` had already finished
while the panel controller was still processing `POWER_OFF`. A second trigger
could then log:

```text
Display already in state POWER_OFF
```

The current `do_refresh` increments `refresh_count`, sets `panel_mode = 0`,
turns the LED on, updates the panel, waits 25 seconds and turns the LED off.

## Battery measurement at the end of the cycle

### Current flow

```yaml
- delay: 2s
- repeat:
    count: 5
    then:
      - component.update: battery_voltage
      - delay: 300ms
- component.update: battery_level
```

Source: `esphome/e1002-kitchen.yaml:438-453`.

The ADC filter averages five values on top of that:

```yaml
- sliding_window_moving_average:
    window_size: 5
    send_every: 5
```

Source: `esphome/e1002-kitchen.yaml:233-246`.

### Why at the end of the cycle?

Measuring at boot was wrong: the voltage divider was read once roughly 200
milliseconds after enable, while WiFi was starting. The values tracked radio and
CPU load rather than charge state; within a single wake the reading could
apparently fall from `78%` to `45%`.

At the end of the cycle:

- the image download has finished,
- the e-paper refresh has finished,
- the radio is largely quiet,
- two seconds let the measurement path settle,
- five samples reduce ADC noise,
- the divider is already enabled.

The implementation switches GPIO21 on early at boot
(`esphome/e1002-kitchen.yaml:64-72`); `measure_battery` does not switch it
again. "Rail quiet" here means the measurement is taken after the load phase,
not that the rail is power-cycled inside that script.

Log format:

```text
Battery: %.3f V -> %.1f%%
```

### Calibration

`battery_level` uses a linear multi-point curve from `4.15 -> 100.0` down to
`3.27 -> 0.0`, clamped to 0..100 (`esphome/e1002-kitchen.yaml:248-274`).

`calibrate_linear` maps NaN to NaN. A NaN state creates no entity in Home
Assistant, which is why "Battery Level" used to be missing while "Battery
Voltage" appeared. The fix is to compute the curve in a lambda and return `{}`
early on NaN. Keep that NaN guard when changing this part.

## `sleep_evaluation`: the full slot arithmetic

Source: `esphome/e1002-kitchen.yaml:580-635`.

### `stay_awake`

The first guard:

```yaml
- if:
    condition:
      and:
        - binary_sensor.is_on: stay_awake
    then:
      - logger.log: "stay_awake is on; not sleeping"
```

`input_boolean.e1002_stay_awake` is switched on for OTA, USB/UART debugging and
longer work sessions. The device then stays reachable.

### Valid time

With `stay_awake` off:

```yaml
- wait_until:
    condition:
      lambda: 'return id(ha_time).now().is_valid();'
    timeout: 30s
```

This wait fixes the hourly retry bug. Previously the lambda ran too early,
`now.is_valid()` was `false` and the device fell back to `retry_minutes: 60`.
The result was 18 cycles in 21.5 hours instead of one daily wake.

### Lambda

```cpp
auto now = id(ha_time).now();
int minutes = ${retry_minutes};

if (now.is_valid()) {
  const int target_h = ${refresh_hour};
  const int target_m = ${refresh_minute};

  int now_total = now.hour * 60 + now.minute;
  int target_total = target_h * 60 + target_m;
  int delta = target_total - now_total;

  if (delta <= 0) {
    delta += 24 * 60;
  }

  minutes = (delta > 2) ? delta : ${retry_minutes};

  ESP_LOGI("sleep", "Sleeping %d min until %02d:%02d",
           minutes, target_h, target_m);
} else {
  ESP_LOGW("sleep", "No HA time; retrying in %d min", minutes);
}

id(onboard_led).turn_off();
id(deep_sleep_ctrl).set_sleep_duration((uint32_t) minutes * 60 * 1000);
id(deep_sleep_ctrl).begin_sleep();
```

What it means:

- the default `minutes` is `60`,
- the target is `05:45`,
- `delta <= 0` means today's slot has passed, so aim for tomorrow (`+1440`),
- a gap of two minutes or less counts as too close, and the fallback prevents an
  immediate re-wake on the same slot edge,
- only then is the LED turned off, the duration set and deep sleep begun.

A verified healthy log line:

```text
Sleeping 906 min until 05:45
```

## The five-minute safety net

Source: `esphome/e1002-kitchen.yaml:675-713`.

Background: `wake_cycle` runs only from `on_boot`, and `sleep_evaluation` only
at the end. If OTA or debugging left `stay_awake` on and no reboot followed, the
device stayed awake indefinitely. That already cost 32 percent in one night and
produced 447 sensor values between 22:30 and 06:30.

The net runs every five minutes, but only with both guards:

```yaml
- api.connected:
- lambda: 'return millis() > 300000;'
```

Why both?

1. Without `api.connected`, the `homeassistant` binary sensor is not yet
   populated with the real Home Assistant state.
2. Without `millis() > 300000`, the first tick during boot or OTA would see the
   local default interpretation `off` and sleep in the middle of an OTA.

The observed message was:

```text
Awake without stay_awake; sleeping
```

After both guards:

- `stay_awake` on → `Awake by request; not sleeping`,
- `stay_awake` off → `sleep_evaluation` as the repair path.

## Buzzer and `sounds_enabled`

`sounds_enabled` is a persistent bool, initially `false`. The template switch:

```yaml
switch:
  - platform: template
    name: "Sounds"
    id: sounds_switch
    lambda: 'return id(sounds_enabled);'
    turn_on_action:
      - lambda: 'id(sounds_enabled) = true;'
      - script.execute: beep_ack
    turn_off_action:
      - lambda: 'id(sounds_enabled) = false;'
```

Source: `esphome/e1002-kitchen.yaml:714-728`.

`beep_ack` produces a 60 ms tone at 2200 Hz when sounds are allowed.
`beep_clean` plays a descending two-tone at 2400/1400 Hz for panel maintenance
(`esphome/e1002-kitchen.yaml:637-673`). Silent is the default: 20 seconds of
visible panel movement is feedback enough, and a chirp on every daily refresh is
annoying in a kitchen.

## Buttons

Current code (`esphome/e1002-kitchen.yaml:292-335`):

| Button | Pin | Debounce | Action |
|---|---|---|---|
| Green | `GPIO3` | `delayed_on: 50ms` | `force_refresh`; can wake from deep sleep. |
| White right | `GPIO4` | `delayed_on: 50ms` | `force_refresh`; same pin as the deep-sleep wake, `allow_other_uses: true`. |
| White left | `GPIO5` | `delayed_on: 3s` | `panel_clean`; deliberately a long press, so brushing past does not start three full flushes. |

Template buttons in Home Assistant:

- `Refresh Dashboard` → `force_refresh`,
- `Panel Clean` → `panel_clean`,
- `Sleep Now` → `sleep_evaluation`.

## Turning the page

Two pages exist: 1 is home (the daily picture, waste, forecast) and 2 is the
weather detail (24-hour curve, week band). The **right white button** turns
between them, and it is the only button that can — GPIO4 doubles as the deep
sleep wake pin, so it is the single press the panel can feel while asleep.

A press does the whole cycle:

```text
press -> wake -> current_page flips -> render request with that page
      -> download -> repaint -> sleep
```

The page lives in `current_page`, a global with `restore_value: yes`, so it
survives the sleep and the panel keeps showing what you left it on.

### Which page a wake is for

Decided once, at the top of `wake_cycle`, from the wake cause:

```cpp
const auto cause = esp_sleep_get_wakeup_cause();
const bool from_button = cause == ESP_SLEEP_WAKEUP_EXT0 ||
                         cause == ESP_SLEEP_WAKEUP_EXT1 ||
                         cause == ESP_SLEEP_WAKEUP_GPIO;
```

A pin wake turns the page. A timer wake — or a cold boot, which reports no
cause at all — resets to page 1, because the morning should be the picture
rather than whatever was left on the glass at bedtime. ESP32-S3 reports pin
wakes as `EXT1` or `GPIO` depending on how the deep sleep component armed them,
so all three variants count.

### Why the button handler does not also turn it

On a wake caused by the button, the press is already spent: the page turned in
`wake_cycle` before the GPIO was even sampled. If `on_press` acted on the same
press it would turn the page straight back. The handler therefore ignores
presses in the first fifteen seconds after boot:

```yaml
- if:
    condition:
      lambda: 'return millis() > 15000;'
```

Fifteen seconds is far longer than a boot and far shorter than the ~40 s the
device stays up, so a deliberate press on an awake panel still works. It is the
same shape as the guard on the five-minute sleep net.

### How the page reaches Home Assistant

As a flat string key, in two static branches:

```yaml
- if:
    condition:
      lambda: 'return id(current_page) == 2;'
    then:
      - homeassistant.action:
          action: script.${render_script}
          data:
            page: "2"
    else:
      - homeassistant.action:
          action: script.${render_script}
          data:
            page: "1"
```

Two literals rather than one templated call: `homeassistant.action` can only
send flat string key/values, and a literal is one less moving part than a
lambda that has to stringify an int. The Home Assistant script takes `page` as
a field and defaults it to 1, so every other caller — the generator, the
periodic automation — keeps getting the home page without knowing pages exist.

### Two ways the request can go missing

Both were observed while wiring this up, and both look identical from the
sofa — the panel simply shows the wrong page.

**The request is dropped.** `api.connected` goes true when the encrypted
handshake finishes, but Home Assistant subscribes to device-initiated actions a
little later, and a call in that gap is silently discarded. The settle after
`api.connected` is therefore **six seconds**; a cold boot was measured losing a
request sent 3.05 s after the handshake. Three seconds was enough while every
render was the same page, because a dropped request only meant repainting what
was already on the glass.

**The fetch overtakes the render.** There is one `dashboard.png` and it holds
whatever was drawn last, so arriving early does not fail cleanly:

| Timing | What the panel does |
|---|---|
| File unchanged since the last fetch | Home Assistant answers `304 Not Modified`, the download is skipped, the old page stays |
| File changed, but by an *earlier* render | the previous page is downloaded and painted |

So the wait before fetching is sized to the page being asked for, measured on
this installation:

| Page | What it draws | Render time |
|---|---|---:|
| 2 | chrome only | 0.2–0.3 s |
| 1 | composites the picture and Atkinson-dithers it | 8.2–8.4 s |

Page 2 waits 3 s, page 1 waits 13 s. A flat 8 s — which is what the firmware
used before pages could be turned — is a coin flip for page 1.

### Cost

One page turn is one extra wake: roughly 40 seconds awake and a ~20 s panel
refresh, about 0.6 mAh. Against ~80 mAh a day of sleep that is under a percent,
so turning the page a few times a day is free in any sense that matters. What
is not free is leaving the panel awake — see [power.md](power.md).

### What is deliberately not cached

There is one `dashboard.png`, and every render overwrites it. A page turn
therefore always costs a fresh render.

That is the right trade rather than an omission. The render is free and takes
about three seconds, against the ~20 seconds the e-paper itself needs, so
caching would save nothing a human could notice. And a cached page 2 would show
the weather from whenever it was last drawn — the whole point of the page is
the current curve.

## Panel maintenance

`weekly_clean_check`:

- reads a valid Home Assistant time,
- checks for the first run, at least seven days since `last_clean_day`, or a
  year rollover,
- runs only between 03:00 and 05:00,
- persists `last_clean_day`,
- starts white → 25s → black → 25s → white → 25s.

That counters Spectra 6 ghosting without a daily cost.
