# Setup: from nothing to a running panel

This guide builds the system in the order the parts depend on each other. Every
section ends with a check, because each one can fail quietly and you do not want
to discover that three steps later.

The other chapters explain how things work. This one only gets them running.

## What you need

| | |
|---|---|
| **Home Assistant** | Any installation you can add a custom component to. Needs a weather integration; the examples assume `weather.forecast_home`. |
| **A machine that stays reachable** | macOS or Linux, running the generator. It has to be awake around 04:30, or at least some time before you look at the panel. A laptop is fine — a missed run falls back to the previous picture. |
| **A model** | The generator talks to the `codex` CLI. Provider and cost are whatever your CLI is pointed at. |
| **Photos of your dog** | Four or so, different angles, decent light. |
| **Optional: the panel** | A Seeed reTerminal E1002. Without it you still get a picture and a rendered dashboard PNG, just no e-paper. |
| **Optional: Immich** | For archiving the originals. Skipped cleanly if absent. |

You can stop after part 1 and have daily pictures, or after part 2 and have a
dashboard PNG you can put on any screen.

---

## Part 1 · The generator

### 1.1 Get the code

The generator keeps runtime state — prompt history, style history, the day's
output — next to itself. Keep it out of any folder that syncs, or two machines
will fight over the same files mid-run.

```sh
git clone https://github.com/nichtlegacy/foredogs.git ~/opt/foredogs
cd ~/opt/foredogs/generator
python3 -m pip install -r requirements.txt
```

### 1.2 Check the interpreter

The generator needs a `python3` that can import Pillow. If you have several
Pythons, the scheduler may not pick the one you installed Pillow into — this is
the single most common first-run failure.

```sh
python3 -c "import PIL; print(PIL.__version__)"
```

`daily_run.sh` searches for a working interpreter by itself, so as long as one
of them has Pillow, it will be found.

### 1.3 Pick a model backend

Two options. Neither is better; they differ in what you already have.

**The `codex` CLI** (default). No API key and no endpoint live in this project —
whatever the CLI is logged into answers.

```sh
codex --version
```

**Any OpenAI-compatible endpoint.** OpenAI, Google, LiteLLM, OpenRouter, Ollama
or a local proxy. Lower barrier if you already hold an API key.

```sh
python3 -m pip install openai
```

You configure which one in the next step. If neither is available the generator
cannot produce anything; everything else in this guide still works, but you
would be building a dashboard around a picture that never changes.

### 1.4 Configuration

Three files are deliberately untracked, because they describe you rather than
the project.

```sh
cp ../examples/generator/config.example.json config.json
cp ../examples/generator/dogs.example.json config/dogs.json
cp ../examples/generator/celebrations.example.yaml config/celebrations.yaml
```

Edit `config.json`:

| Key | Set it to |
|---|---|
| `location` | Your town, as you would type it into a map. It reaches both prompts, so activities are set where you actually live. |
| `weather.geocoding_language` | `en`, or your own language for better local name matching. |
| `generator.final_image_size` | Your display's resolution. `800x480` for the E1002. |
| `generator.display_profile` | `spectra6` for a six-colour e-paper, otherwise remove it. |
| `publish.*` | Leave for now. Part 2 fills these in. |
| `immich.*` | Leave `album_id` empty to skip archiving. |
| `provider.kind` | `codex`, or `openai` for an OpenAI-compatible endpoint. |

For the `openai` backend, also set `provider.base_url`, `provider.text_model`
and `provider.image_model`, then export the key:

```sh
install -m 700 -d ~/.config/foredogs
printf '%s' 'PASTE_KEY_HERE' > ~/.config/foredogs/api_key
chmod 600 ~/.config/foredogs/api_key
export FOREDOGS_API_KEY="$(cat ~/.config/foredogs/api_key)"
```

The key is read from the environment variable named in `provider.api_key_env`,
so it never lands in `config.json`.

Edit `config/dogs.json` with your dogs' names, a short description each, and
the paths to their photos.

`config/celebrations.yaml` is optional. Delete it if you do not want private
dates in the pictures — 32 public holidays and season starts work without it.

### 1.5 Photos

```sh
cp ~/Pictures/rex_*.jpg assets/input_images/
```

Four shots per dog, from different angles, work well. These lock identity —
breed, fur colour, markings, ear and snout shape — and the style then redraws
the dog in its own visual language.

### ✅ Check: prompts without spending anything

```sh
./scripts/daily_run.sh --dry-run
```

This runs the whole pipeline except the image call. It costs one small text
generation.

```sh
cat output/latest_image_prompt.txt
```

You should see your town, your dogs' descriptions, an art style, and an activity
that suits today's weather. If the location came out wrong, `weather.py` tried
six spellings and failed at all of them — simplify `location` to just the town
name.

Also worth running now:

```sh
python3 -m foredogs_generator.main --config config.json --list-celebrations --year 2027
```

That walks a whole year through the same code the generator uses, so whatever it
prints really will fire.

### ✅ Check: a real picture

```sh
python3 -m foredogs_generator.main --config config.json --json
open output/foredogs_original.png
```

Expect several minutes. If the dog came out photo-realistic in a stylized scene,
the style embodiment block did not take — that is a model-quality problem, not a
configuration one, and `docs/generator.md` explains what the prompt
already does about it.

**Stop here** if you only wanted daily pictures. Schedule it (1.6) and you are
done.

### 1.6 Scheduling

Both units assume `~/opt/foredogs`. Edit the path if yours differs.

**macOS:**

```sh
cp scripts/macos/com.foredogs.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.foredogs.daily.plist
launchctl list | grep foredogs
```

**Linux:** see [`generator/scripts/linux/README.md`](../generator/scripts/linux/README.md).

The daily slot is 04:30, well before a display that wakes at 05:45. Generation
usually takes 3–7 minutes but has been measured at 19.7, and a 15-minute gap
once meant the panel fetched yesterday's picture and slept again before the new
one existed.

---

## Part 2 · Home Assistant

### 2.1 Install the integration

Via HACS as a custom repository, or by hand:

```sh
mkdir -p /config/custom_components
cp -r custom_components/foredogs /config/custom_components/
```

Add to `configuration.yaml`:

```yaml
foredogs:
```

Restart Home Assistant. Not a reload — a restart.

### ✅ Check: the services exist

Developer Tools → Actions. Both should be listed:

```text
foredogs.render_dashboard
foredogs.generate_dog_picture
```

If not, the integration did not load. Look in the log for `foredogs`; a missing
Pillow is the usual cause.

### 2.2 Fonts

Home Assistant OS ships no fonts at all. Without one, Pillow silently falls back
to a bitmap font and the dashboard looks broken rather than failing.

Download [Inter](https://rsms.me/inter/) and put four files in
`/config/foredogs_data/fonts/`:

```text
Inter-Bold.ttf
Inter-SemiBold.ttf
Inter-Medium.ttf
Inter-Regular.ttf
```

Any TrueType family works; the renderer takes the first candidate it finds.

### 2.3 Helpers and the render script

Merge [`examples/homeassistant/kitchen_dashboard_helpers.yaml`](../examples/homeassistant/kitchen_dashboard_helpers.yaml)
into `configuration.yaml`, and append
[`examples/homeassistant/render_dashboard_script.yaml`](../examples/homeassistant/render_dashboard_script.yaml)
to `scripts.yaml`.

Edit the entity IDs in the script to match your installation. The weather entity
is required; the panel sensors, the waste sensors and the waste calendars are
all optional, and the renderer skips whatever is unavailable.

Restart Home Assistant again.

### ✅ Check: a rendered PNG

Developer Tools → Actions → `script.foredogs_render_dashboard` → Perform.

```text
/config/www/daily_foredogs/dashboard.png
```

Open it at `http://<your-ha>:8123/local/daily_foredogs/dashboard.png`. You
should see a header with today's date and weather, a sidebar with sunrise and
sunset, a forecast strip, and — where the picture goes — the words `kein Bild`,
because part 3 has not connected the two halves yet.

If the text is tiny and ugly, the fonts are not where the renderer looks.

---

## Part 3 · Connect the generator to Home Assistant

### 3.1 A token

Home Assistant → your profile → Security → Long-lived access tokens → Create.

On the generator host:

```sh
install -m 700 -d ~/.config/foredogs
printf '%s' 'PASTE_TOKEN_HERE' > ~/.config/foredogs/ha_token
chmod 600 ~/.config/foredogs/ha_token
```

The token never goes into `config.json`. The scripts read it from this file.

### 3.2 SSH to Home Assistant

The generator copies the picture over `scp`, so it needs key-based SSH. Install
the *Advanced SSH & Web Terminal* add-on, put your public key in its
configuration, then add a host alias to `~/.ssh/config`:

```sshconfig
Host homeassistant
    HostName homeassistant.local
    User root
    Port 22
    IdentityFile ~/.ssh/homeassistant
```

```sh
ssh -o BatchMode=yes homeassistant "echo reachable"
```

`BatchMode=yes` matters: the generator passes it too, so a setup that only works
when you can type a password will fail from the scheduler.

### 3.3 Point the generator at Home Assistant

In `config.json`:

```json
"publish": {
  "ssh_host": "homeassistant",
  "remote_dir": "/config/www/daily_foredogs/dogs",
  "ha_url": "http://homeassistant.local:8123",
  "render_script": "script.foredogs_render_dashboard"
}
```

### ✅ Check: the full round trip

```sh
./scripts/daily_run.sh --publish-only
```

This re-uploads the picture you already generated rather than making a new one.
Expect `published` at the end.

Then look at `/local/daily_foredogs/dashboard.png` again. `kein Bild` should now
be your dog.

If the upload worked but the dashboard did not change, the render POST failed.
That is not fatal by design — the picture is already there and the next
scheduled render picks it up.

### 3.4 The manual trigger

```sh
cp scripts/macos/com.foredogs.trigger.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.foredogs.trigger.plist
```

The watcher polls the button every two minutes rather than Home Assistant
pushing, because Home Assistant has no SSH key for your machine and a desktop
firewall usually drops inbound connections.

If you let the UI name your helpers differently, set `FOREDOGS_TRIGGER_ENTITY`
and `FOREDOGS_STATUS_ENTITY` in the unit instead of renaming the entities.

### ✅ Check

Press the button in Home Assistant. Within two minutes,
`input_text.foredogs_generation_status` should read `running since HH:MM`, and
some minutes later `done HH:MM`.

---

## Part 4 · The e-paper panel

Only if you have a reTerminal E1002.

### 4.1 Secrets

```sh
cp esphome/secrets.yaml.example /config/esphome/secrets.yaml
python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
```

Fill in your WiFi, the generated API key, and two passwords you choose. Generate
your own key — never reuse one from an example.

### 4.2 Flash over USB

The first flash has to be over USB; there is nothing on the device to update
yet.

```sh
ESPHOME_BUILD_PATH=/tmp/esphome-build \
  esphome run esphome/e1002-kitchen.yaml --device /dev/cu.usbserial-210
```

`ESPHOME_BUILD_PATH` is not optional if your checkout path contains square
brackets: ESP-IDF's `kconfgen` mishandles them and the build fails with a
confusing Kconfig error.

`dashboard_url` in `secrets.yaml` has to point at your Home Assistant. Use an
IP address: the ESP32 does not resolve other hosts over mDNS, so
`homeassistant.local` can fail on the device even though it works from your
laptop.

### 4.3 Adopt it, then allow service calls

Home Assistant will discover the ESPHome device. Adopt it.

Then, and this is the step that is easy to miss: the device asks Home Assistant
to render on every wake, which is a *device-initiated service call*. It is off
by default.

Settings → Devices & services → ESPHome → your device → Configure. Enable
**both** `allow_service_calls` and `subscribe_logs`, then **restart Home
Assistant fully.** Both fields are required; submitting only one writes an empty
options dict and silently changes nothing.

### 4.4 Keep it awake while you work

Turn on `input_boolean.e1002_stay_awake` before any OTA. The device then skips
deep sleep and stays reachable, instead of disappearing until the next morning.

Turn it off when you are done. Left on, it costs about a third of the battery in
one night.

### ✅ Check: one full wake cycle

Watch the ESPHome log through a reset. A healthy cycle logs, in order:

```text
Wake #...
Dashboard image downloaded
Battery: 3.9xx V -> 8x.x%
Sleeping 906 min until 05:45
```

The last line is the one that matters. If it says
`No HA time; retrying in 60 min`, the device never got a valid time and will
wake hourly instead of daily — which turns 30 days of battery into 8.

---

## Final checklist

| | Check |
|---|---|
| Generator | `launchctl list \| grep foredogs` or `systemctl --user list-timers 'foredogs-*'` |
| Picture | `output/foredogs_original.png` has today's date |
| Upload | `/config/www/daily_foredogs/dogs/YYYY-MM-DD.png` exists |
| Dashboard | `/local/daily_foredogs/dashboard.png` shows your dog, not `kein Bild` |
| Panel | Refresh counter rises once a day, battery loses 0.12–0.17 %/h |
| Secrets | `ls -l ~/.config/foredogs/` shows `-rw-------` on both files |
| Privacy | `git status` in your checkout shows no `config.json`, `dogs.json` or `celebrations.yaml` |

## When something breaks

[`operations.md`](operations.md)
documents 21 diagnosed failures with root cause and fix. The ones most likely to
hit a fresh install:

| Symptom | Case |
|---|---|
| Tiny ugly text on the dashboard | 10, no fonts in Home Assistant OS |
| Generation works by hand, fails from the scheduler | 16, the wrong Python got picked |
| OTA reports success, old firmware keeps running | 2, `boot_is_good_after` |
| Device wakes hourly instead of daily | 4, the slot was computed without a valid time |
| Render request dropped | 6 and 7, the API subscription and `allow_service_calls` |
| Battery jumps around wildly | 13, the reading was taken during WiFi startup |
