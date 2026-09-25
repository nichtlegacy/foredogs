# Operations and troubleshooting

## Operating principles

1. **Generate a picture once a day.** `gpt-5.6-luna` at `max` reasoning costs
   time and money.
2. **Re-render the dashboard as often as you like.**
   `foredogs.render_dashboard` calls no image provider.
3. **Do not keep the display awake.** Deep sleep is the single biggest battery
   lever.
4. **Never publish half a file.** Both the Mac and Home Assistant write to a
   temporary name and replace atomically.
5. **Never destroy the last good screen over a transient network error.** The
   download gets two attempts; on failure the e-paper stays as it is.
6. **The live installation beats the example YAML.** Many files under
   `examples/` come from older device, time or entity states.

## Daily operations check

### Mac

```zsh
launchctl list | grep foredogs

# Generator log
less generator/logs/daily_run.log

# Button polling
less generator/logs/trigger_watch.log

# Manual prompt check, no cost and no upload
cd generator
scripts/daily_run.sh --dry-run
```

Expected artifacts after a successful run:

```text
output/foredogs_original.png
output/foredogs_optimized.png
output/foredogs_generation_status.json
output/latest_activity_prompt.txt
output/latest_image_prompt.txt
```

### Home Assistant

Check that:

- the render script exists,
- the render automation is enabled,
- the dashboard camera points at `dogs/latest.png`,
- `input_boolean.e1002_stay_awake` is `off` in normal operation,
- the battery sensor is numeric,
- the live recycling sensor is the one actually in use, not a dead restored
  entity,
- the refresh counter only rises on real panel refreshes.

### ESPHome and serial

Before OTA:

1. Set `input_boolean.e1002_stay_awake` to `on`.
2. Watch the API connection and the logs.
3. Compile the OTA with `ESPHOME_BUILD_PATH=/tmp/esphome-build`.
4. Wait until the device has run stably for at least `20s`.
5. Only switch `stay_awake` off after a successful check.

A typical healthy log sequence:

```text
Wake #...
Dashboard image downloaded
Render requested from HA
Battery: ... V -> ...%
Sleeping 906 min until 05:45
```

## Battery

Measured drain, expected runtime, the calibration curve and
`tools/battery_report.py` all live in [power.md](power.md). The one number to
carry into troubleshooting: a healthy device loses 0.12–0.17 %/h. Anything
above roughly 0.3 %/h means it is not reaching deep sleep, and the cases below
cover every way that has happened so far.

## Cases already diagnosed

The following are established causes and fixes. No new root-cause analysis is
needed; on the same symptom, check the documented fix first.

### 1. Square brackets in the repository path break ESP-IDF

- **Symptom:** ESPHome/ESP-IDF aborts while generating Kconfig; the error looks
  like a Kconfig or path problem.
- **Root cause:** `kconfgen` mishandles square brackets in an absolute path.
- **Fix:** always set a bracket-free build path:

  ```zsh
  ESPHOME_BUILD_PATH=/tmp/esphome-build \
    esphome run esphome/e1002-kitchen.yaml --device /dev/cu.usbserial-210
  ```

Source: `esphome/e1002-kitchen.yaml:18-22`.

### 2. OTA silently rolled back three times

- **Symptom:** OTA reports success, but the next wake still runs the old
  firmware.
- **Root cause:** ESPHome marks an image as good only after 60 seconds by
  default. An E1002 wake takes about 40 seconds and then sleeps, so the
  good-boot marker was never reached.
- **Fix:**

  ```yaml
  safe_mode:
    boot_is_good_after: 20s
  ```

  Flashing over USB serial bypasses safe mode entirely.

Source: `esphome/e1002-kitchen.yaml:114-123`.

### 3. The device never slept, 32 percent overnight

- **Symptom:** `32%` consumed in one night; `447` sensor values between
  `22:30` and `06:30`.
- **Root cause:** `wake_cycle` runs only in `on_boot`, and `sleep_evaluation`
  only at the end of the cycle. With `stay_awake` on during OTA there was no
  reboot and therefore no further call to sleep.
- **Fix:** an `interval: 5min` safety net that calls `sleep_evaluation` when
  `stay_awake` is off, guarded by `api.connected` and `millis() > 300000`.

Source: `esphome/e1002-kitchen.yaml:675-713`.

### 4. Hourly retry loop

- **Symptom:** `18 cycles in 21.5h`, about `0.51 %/h`; the expected 25–33 days
  became about 8.
- **Root cause:** `sleep_evaluation` computed with `ha_time` before Home
  Assistant had sent the time. `now.is_valid()` was `false`, so the lambda fell
  back to `retry_minutes: 60`.
- **Fix:** wait for a valid Home Assistant time before the slot lambda:

  ```yaml
  - wait_until:
      condition:
        lambda: 'return id(ha_time).now().is_valid();'
      timeout: 30s
  ```

- **Verification:**

  ```text
  Sleeping 906 min until 05:45
  ```

Source: `esphome/e1002-kitchen.yaml:591-635`.

### 5. The five-minute net slept in the middle of an OTA

- **Symptom:** the log showed `Awake without stay_awake; sleeping` although
  `stay_awake` was already `ON` in Home Assistant.
- **Root cause:** a `homeassistant` platform binary sensor reads `off` locally
  until Home Assistant pushes its real state. An early five-minute tick read
  that intermediate window as permission to sleep.
- **Fix:** evaluate the safety net only when both conditions hold:

  ```yaml
  - api.connected:
  - lambda: 'return millis() > 300000;'
  ```

Source: `esphome/e1002-kitchen.yaml:683-695`.

### 6. The render request was dropped

- **Symptom:** the display shows the previous cycle's picture; Home Assistant
  logs first:

  ```text
  Home Assistant action 'script.foredogs_render_dashboard' dropped; no client connected
  ```

  and after the first fix:

  ```text
  dropped; client has not subscribed to actions (yet)
  ```

- **Root cause, phase 1:** the action was sent about one second before the API
  handshake.
- **Fix, phase 1:** `wait_until api.connected`.
- **Root cause, phase 2:** `api.connected` becomes true at the handshake, but
  Home Assistant registers the device-initiated action subscription slightly
  later.
- **Fix, phase 2:** an explicit `delay: 3s` after the API gate.

Source: `esphome/e1002-kitchen.yaml:461-481`.

### 7. `homeassistant.action` needs two permissions

- **Symptom:** the action code is missing, or the service call does not run,
  although the YAML looks plausible.
- **Root cause:** both sides have to be active:
  1. the firmware needs `api: homeassistant_services: true`, otherwise ESPHome
     does not compile the outbound call code,
  2. the Home Assistant config entry needs `allow_service_calls`.
- **Fix:** set both, then restart Home Assistant fully. Submit the options flow
  with `allow_service_calls` **and** `subscribe_logs`. Sending only one field
  writes an empty options dict, because both options are required.

Source: `esphome/e1002-kitchen.yaml:90-107`.

### 8. `Display already in state POWER_OFF`

- **Symptom:** a second refresh trigger is rejected by the panel.
- **Root cause:** `component.update` returns immediately while the panel state
  machine still runs for roughly 20 seconds. `mode: single` no longer protects
  anything once the script has returned.
- **Fix:** an explicit 22 to 25 second delay in the refresh script. Currently:

  ```yaml
  - component.update: epaper
  - delay: 25s
  ```

Source: `esphome/e1002-kitchen.yaml:522-533`.

### 9. `calibrate_linear` with NaN

- **Symptom:** "Battery Level" is missing in Home Assistant while "Battery
  Voltage" exists.
- **Root cause:** `calibrate_linear` maps NaN to NaN, and a NaN state creates no
  entity.
- **Fix:** compute the curve in a lambda and `return {};` early on NaN, so no
  invalid state is published.

### 10. No fonts in Home Assistant OS

- **Symptom:** text appears as a tiny Pillow bitmap font and the layout looks
  wrong.
- **Root cause:** `fc-list` was empty; Home Assistant OS had no usable fonts.
- **Fix:** copy TrueType files into `/config/foredogs_data/fonts/`:

  ```text
  Inter-Bold.ttf
  Inter-SemiBold.ttf
  Inter-Medium.ttf
  Inter-Regular.ttf
  ```

- **Check:** the renderer must not log:

  ```text
  No TrueType font found — install one in /config/foredogs_data/fonts/. Falling back to Pillow's bitmap font; text will look wrong.
  ```

Source: `dashboard_render.py:295-338`.

### 11. ESPHome cannot pass lists to Home Assistant actions

- **Symptom:** a compile error:

  ```text
  Must be string, got EList
  ```

- **Root cause:** `homeassistant.action` does not support the nested waste
  calendar list.
- **Fix:** keep the waste configuration in the Home Assistant render script. The
  firmware sends no lists, so the configuration stays changeable without a
  reflash.

Source: `esphome/e1002-kitchen.yaml:475-481`.

### 12. Waste calendar configured twice, with the wrong district

- **Symptom:** the sensors show dates that do not match; the address in the UI
  integration looks correct but produces no sensors.
- **Root cause:**
  - `configuration.yaml` carried a wrong address and was the thing actually
    creating the sensors,
  - the UI integration carried the correct address but creates no sensors.
- **Fix:** correct the address in the YAML configuration, and check which of the
  two sources actually produces the sensor entities before trusting either.
- **Operating rule:** know which recycling sensor is live. A renamed or
  reconfigured integration can leave a dead restored entity behind that still
  looks plausible in the entity picker.

### 13. The battery measurement was a load measurement

- **Symptom:** the battery jumped from about `78%` to `45%` within one wake;
  history showed `3.57 %/h`.
- **Root cause:** a single reading per boot, roughly `200ms` after the divider
  was enabled, in parallel with WiFi starting. The ADC saw load rather than cell
  voltage.
- **Fix:** move the measurement to the end of the cycle: two seconds to settle,
  five averaged samples, and only after the download, panel refresh and radio
  load.
- **Result:** `0.12–0.17 %/h`, roughly `25–33` days.

Source: `esphome/e1002-kitchen.yaml:430-453`.

### 14. launchd disabled the trigger watcher

- **Symptom:** the Home Assistant button appears dead although its state
  changes.
- **Root cause:** `trigger_watch.sh` returned non-zero on a failed generation.
  launchd throttled and then stopped the agent after repeated failures.
- **Fix:** log the generation result and write it to the status helper, but
  always `exit 0` at the end.

Source: `generator/scripts/trigger_watch.sh:95-108`.

### 15. The camera showed a months-old picture

- **Symptom:** the dashboard camera showed an old picture although new ones were
  being generated.
- **Root cause:** the camera pointed at `foredogs_original.png`, last written by
  the dead Gemini path.
- **Fix:** point the camera at `dogs/latest.png`, which the Mac publish updates
  after every upload.

### 16. The Homebrew PATH picked the wrong Python

- **Symptom:** the generator finds Python but `PIL`/Pillow is missing; a manual
  local run and a launchd run behave differently.
- **Root cause:** the Homebrew PATH was prepended and shadowed the python.org
  framework Python that has Pillow.
- **Fix:** append rather than prepend, and test candidates with
  `python3 -c "import PIL"`.

Source: `generator/scripts/daily_run.sh:35-55`.

### 17. `status` is read-only in zsh

- **Symptom:** generation succeeded but publishing never started; the shell
  aborted right before it.
- **Root cause:** zsh reserves `status` as read-only, and the script tried to
  store the return code there.
- **Fix:** use a variable named `rc`.

Source: `daily_run.sh:72-76`, `91-97`.

### 18. Provider problems with other models

- **Symptom:** one model answered with:

  ```text
  502 unknown provider
  ```

  and an image model was blocked in the proxy configuration under
  `oauth-excluded-models.codex`.
- **Fix:** switch to `gpt-5.6-luna` and run the image tool through that Codex
  host/agent:

  ```json
  "codex_model": "gpt-5.6-luna"
  ```

  Source: `generator/config.json`.

### 18a. Changing provider: foredogs does not know about the proxy

foredogs contains **no** provider configuration at all — no `base_url`, no API
key, no port. `codex_cli.py` simply starts the `codex` subprocess
(`codex_cli.py:30`). Which backend provider answers is decided entirely by
`~/.codex/config.toml`:

```toml
model = "gpt-5.6-luna"
model_reasoning_effort = "max"
model_provider = "my-provider"

[model_providers.my-provider]
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
requires_openai_auth = true
```

Changing the proxy therefore requires **no change to foredogs**. All that has to
be checked is whether the new provider is reachable and offers the same model:

```bash
lsof -nP -iTCP:2455 -sTCP:LISTEN   # is the load balancer listening?
```

If the provider fails, generation fails with `CodexCliError`.
`trigger_watch.sh` then writes `fehlgeschlagen HH:MM` into the status helper,
and the display keeps showing the last successful picture through the fallback
chain in `image_source.py`.

### 19. `codex exec` has no reasoning flag

- **Symptom:** an expected `--reasoning-effort` CLI flag does not work.
- **Root cause:** `codex exec` does not expose that flag.
- **Fix:** pass a config override instead:

  ```zsh
  codex exec -c model_reasoning_effort="max" ...
  ```

Source: `generator/foredogs_generator/codex_cli.py:46-50`.

### 20. Hand-written `.storage` entries created no entity

- **Symptom:** a manually created `.storage` `config_entries` structure exists
  on disk, but Home Assistant creates no usable entity.
- **Root cause:** Home Assistant creates registry and entity data through the
  real integration setup flow, not from hand-written registry fragments.
- **Fix:** set the integration up through the proper setup or options flow, then
  restart Home Assistant fully.

### 21. The battery stayed `unknown` for 9 days and the display ran flat

- **Symptom:** the battery sensor sat permanently on `unknown` from 29 July
  12:38. The dashboard still confidently showed 28 %. The low-battery warning
  never fired. On 2 August 12:21 the device woke for the last time; five days of
  silence followed.
- **Root cause, part 1 — the value never left the device.**
  `measure_battery` published the value, and then `sleep_evaluation` entered
  sleep in the same lambda. From the serial log:

  ```text
  [14:33:08.092][I][main:460]: Battery: 3.572 V -> 33.1%
  [14:33:08.097][I][deep_sleep:060]: Beginning sleep
  ```

  Five milliseconds. The state change was queued for transmission, but the
  connection went down before the packet left. The device measured correctly
  every morning and Home Assistant never found out.
- **Root cause, part 2 — the renderer hid it.** `_state_float_sticky` cached the
  last known value without a timestamp and kept serving it for as long as Home
  Assistant stayed up. That is why the panel showed 28 % long after nothing was
  being measured.
- **Root cause, part 3 — the alarm was blind.** The low-battery automation
  triggers on `numeric_state below 15`. `unknown` is not a numeric state, so the
  automation could not fire at all. `last_triggered: None`.
- **Fix:**
  1. Firmware: `delay: 3s` between the last publish and `begin_sleep()`, so the
     API can drain. Costs roughly 0.003 mAh per day.
  2. Firmware: `send_every: 5` changed to `1`, and eight readings instead of
     five, so one failed reading no longer costs the whole day. A NaN guard in
     the template keeps a missing value from overwriting the last good one.
     `battery_voltage` is exposed as a diagnostic entity.
  3. Renderer: cache entries carry a timestamp and expire after 30 h. After
     that the display honestly shows `—%`. 30 h, because the device only reports
     once a day and a render shortly before that legitimately uses a value
     around 23 h old.
  4. A new automation warns when the value has been `unknown` or `unavailable`
     for more than 31 h. A missing reading is itself the symptom worth alerting
     on.

> **Lesson:** a fallback that cannot age turns a loud failure into a silent one.
> The cache was meant as a convenience and became the reason a draining battery
> went unnoticed for nine days.

## Open problems

### The Home Assistant-side `input_button` trigger

A Home Assistant trigger on the generation `input_button` does not fire
reliably, or at all. Already tried:

- `state`,
- `state` with `not_to`,
- `attribute: null`,
- a template trigger.

Established:

- the actions work when triggered directly,
- the button state demonstrably changes,
- Home Assistant loads the right configuration,
- the log contains no matching error.

Unresolved but cosmetic: the Mac polls the button state itself every two
minutes, so the manual workflow works anyway.

### No genuinely unattended 05:45 run observed yet

Verification so far came from manual serial resets. A fully unattended cycle at
05:45 had not been observed as of the documentation state `2026-07-29`.
