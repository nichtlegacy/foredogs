# Automation and scheduling

Three schedulers run in this system and none of them knows about the others.
That is deliberate — each side degrades on its own — but it means "when does
anything happen" is a question with three answers.

| Scheduler | Where | What it triggers |
|---|---|---|
| `launchd` / `systemd` timer | generator host | The daily picture, 04:30 |
| Home Assistant automation | Home Assistant | The composite render, every 30 min |
| `deep_sleep` slot arithmetic | the panel | One wake a day, 05:45 |

The full timeline is in [architecture.md](architecture.md#a-day-in-the-life).
This page is about the wiring.

## Helper entities

Three helpers, all in `examples/homeassistant/kitchen_dashboard_helpers.yaml`.
Merge them into `configuration.yaml`, or create them in the UI under
**Settings → Devices & services → Helpers**.

| Entity | Type | Purpose |
|---|---|---|
| `input_boolean.e1002_stay_awake` | toggle | Turn on before flashing. The device then skips deep sleep and stays reachable instead of vanishing for hours. **Turn it off again** — left on, it flattens the battery in a night. |
| `input_button.foredogs_generation_trigger` | button | Manual "generate now". |
| `input_text.foredogs_generation_status` | text | The generator host writes `running since HH:MM`, `done HH:MM` or `failed HH:MM` here. |

If you let the UI pick different entity IDs, do not rename them afterwards — set
`FOREDOGS_TRIGGER_ENTITY` and `FOREDOGS_STATUS_ENTITY` in the scheduler unit
instead. See [configuration.md](configuration.md#environment-variables).

## The render script

Everything about *what* gets rendered lives in one Home Assistant script,
`script.foredogs_render_dashboard`
(`examples/homeassistant/render_dashboard_script.yaml`). Both the panel and the
generator host call it by name and pass nothing.

That is not an accident. ESPHome's `homeassistant.action` can only send flat
string key/values, and the waste-calendar configuration is a list of mappings —
it physically cannot be passed from the device. Keeping the entity wiring on the
Home Assistant side also means you can change which sensors feed the panel
without reflashing it.

`mode: queued, max: 3`, because the panel and the generator can both ask within
the same second and the second request should wait rather than be dropped.

Every field the script can pass is documented in
[dashboard.md](dashboard.md#configuring-the-render-call).

> If you flashed firmware before this script was renamed, the device still calls
> `script.e1002_render_dashboard`. Either reflash, or keep a one-line script
> under the old name that calls the new one.

## The automations

`examples/homeassistant/kitchen_dashboard_automations.yaml` ships three.

**1. Render on a schedule.** Every 30 minutes between 05:30 and 22:15, plus
immediately when the generation status changes to `done`. Costs nothing, because
no model is called. The panel triggers its own render on every wake, so this
schedule is mainly a safety net that keeps `dashboard.png` current even when the
display is flat or off — a device that wakes to a missing file shows nothing.

**2. Low battery warning.** A persistent notification below 15 %, with
`for: "00:30:00"` so a single noisy ADC reading cannot fire it. At roughly a
month per charge there is no reason to be surprised by an empty cell.

**3. Generation inside Home Assistant.** Optional, and only for installs without
a separate generator host. If you run the generator on a Mac or a Linux box,
that host schedules itself and this automation should not exist — two schedules
means two pictures a day and two bills.

A fourth is worth adding by hand: a watchdog that notifies when the battery
sensor reports nothing for more than about 36 hours. A missing sensor looks
exactly like a sleeping device, which is how a dead battery reading once went
unnoticed for nine days.

## The manual trigger, and why it polls

Pressing `input_button.foredogs_generation_trigger` does not call the generator.
Home Assistant has no SSH key for the generator host — and giving it one would
make a home automation server able to execute code on a workstation, which is a
poor trade for a button.

Instead the host polls, every 120 seconds, from `trigger_watch.sh`:

```
input_button state == timestamp of last press
    -> compare to the timestamp in state/
        -> different? run, then store the new timestamp
```

The state of an `input_button` *is* the timestamp of its last press, which makes
the poll naturally idempotent: one press, one picture, no lock file and no
at-least-once semantics to reason about. A missed poll is not a missed run; it
is a run two minutes later.

Expect up to two minutes before anything starts, and several more before the
picture appears. The status helper is what makes that wait legible.

Details, including why the poller always exits 0, are in
[mac-pipeline.md](mac-pipeline.md#manual-button-pipeline-trigger_watchsh).

## What the panel triggers itself

On every wake, before downloading:

```yaml
- homeassistant.action:
    action: script.${render_script}
- delay: 8s      # Atkinson dithering takes ~3 s; leave headroom
```

Two details that each cost a day to find:

- `actions:` must be declared in the `api:` block, or ESPHome does not compile
  the outbound-call code at all and the request silently never happens.
- The call waits for `api.connected` **plus three seconds**. `api.connected`
  goes true when the encrypted handshake finishes, but Home Assistant subscribes
  to device-initiated actions slightly later. A call in that gap is dropped with
  `client has not subscribed to actions (yet)` — and the panel shows yesterday's
  render, which looks like a rendering bug rather than a timing one.

## Scheduling the generator host

macOS uses `launchd`, Linux uses a `systemd` timer. Both units are in the
repository:

| | macOS | Linux |
|---|---|---|
| Daily run | `generator/scripts/macos/com.foredogs.daily.plist` | `generator/scripts/linux/foredogs-daily.{service,timer}` |
| Button poll | `generator/scripts/macos/com.foredogs.trigger.plist` | `generator/scripts/linux/foredogs-trigger.{service,timer}` |

Neither reads your shell profile, so both load credentials from files in
`~/.config/foredogs/` rather than from exported variables. Installation and the
exact contents are in [mac-pipeline.md](mac-pipeline.md#launchd-agents).

### Why 04:30 and 05:45

The generator starts at 04:30 and the panel wakes at 05:45. The gap is the
generation budget: a run takes three to six minutes normally, and the spare hour
absorbs a slow model, a retry, or a quota that only frees up later.

If the generator fails entirely, the panel still wakes and still renders — with
yesterday's picture and today's weather. That is the whole reason the picture
and the composite are separate steps.
