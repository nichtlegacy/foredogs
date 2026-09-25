# Configuration

Every knob in the stack, and which file it lives in. Three layers configure
themselves separately, and none of them reads the others' files:

| Layer | File | Tracked in git? |
|---|---|---|
| Generator (Mac/Linux) | `generator/config.json` | No — copy `examples/generator/config.example.json` |
| Home Assistant | `configuration.yaml`, `scripts.yaml`, helper entities | No — see `examples/homeassistant/` |
| Display firmware | `esphome/e1002-kitchen.yaml` + `esphome/secrets.yaml` | YAML yes, secrets no |

Secrets never appear in any of them. They are read from the environment or from
`!secret` references; see [Secrets and credentials](#secrets-and-credentials)
below.

## Configuration source

Active file: `generator/config.json`. It is not tracked in git — copy
`examples/generator/config.example.json` and fill in your own values. The loader
and its dataclasses live in `generator/foredogs_generator/config.py:10-139`.

### Every key in `config.json`

| Path | Example value | Function |
|---|---|---|
| `location` | `Berlin, Germany` | Geocoding and forecast location; ends up in both prompts. |
| `paths.dogs_file` | `./config/dogs.json` | Relative file holding dog profiles and reference images. |
| `paths.art_styles_file` | `./config/art_styles.json` | Pool of style objects; each may carry `outfit`, `universe` and `render_as`. |
| `weather.provider` | `open-meteo` | The only provider currently supported. Anything else raises `Unsupported weather provider`. |
| `weather.geocoding_url` | `https://geocoding-api.open-meteo.com/v1/search` | Geocoding endpoint. |
| `weather.forecast_url` | `https://api.open-meteo.com/v1/forecast` | Forecast endpoint. |
| `weather.geocoding_language` | `en` | Language for geocoding results. |
| `weather.timezone` | `auto` | Open-Meteo timezone. |
| `weather.forecast_days` | `1` | One daily forecast per generation. |
| `generator.image_gen_aspect_ratio` | `16:9` | Composition hint in the image prompt. |
| `generator.image_gen_resolution` | `1K` | Target resolution stated in the prompt. |
| `generator.final_image_size` | `800x480` | Local output size for the optimized copy. |
| `generator.display_profile` | `spectra6` | Colour profile for the local optimized copy. |
| `generator.state_dir` | `./state` | Prompt and style history. |
| `generator.output_dir` | `./output` | Image, prompt and status files. |
| `generator.archive_dir` | `./output/archive` | Dated copy of every generated original, written before publishing. The working files are overwritten each run, so this is the only copy on the machine that made the picture. |
| `generator.archive_keep_days` | `0` | Prune archived originals older than this many days. `0` keeps everything. Only files named `YYYY-MM-DD.<ext>` are ever deleted, so anything you put there by hand survives. |
| `generator.seed_prompt_history_path` | `null` | Optional starting history, used only until `state/foredogs_prompt_history.txt` exists. |
| `generator.seed_style_history_path` | `null` | Optional starting style history. |
| `generator.codex_model` | `gpt-5.6-luna` | Legacy. Superseded by `provider.text_model`; still read when there is no `provider` block. |
| `generator.generation_timeout_seconds` | `1800` | Timeout per Codex subprocess, so 30 minutes. |
| `generator.include_weather_box` | `false` | Whether the picture should paint its own weather box. |
| `generator.codex_reasoning_effort` | `max` | Legacy. Superseded by `provider.reasoning_effort`. |
| `publish.ssh_host` | `homeassistant` | SSH alias used by `scp` and `ssh`. |
| `publish.remote_dir` | `/config/www/daily_foredogs/dogs` | Destination for dated images and weather subfolders. |
| `publish.ha_url` | `http://homeassistant.local:8123` | URL used for the render POST. |
| `publish.render_script` | `script.foredogs_render_dashboard` | Home Assistant service triggered after upload. |
| `publish._comment` | Token from the environment | No `ha_token` in `config.json`; the loader expects `FOREDOGS_HA_TOKEN`. |
| `immich.url` | `https://immich.example.com` | Immich server used for archiving. |
| `immich.album_id` | empty | Pins the archive target to one album. Not a secret, but a stable object ID. Leave empty to resolve by name. |
| `immich.album_name` | `Foredogs Archive` | Fallback name when no album ID is set. |
| `immich._comment` | API key from the environment | The loader expects `FOREDOGS_IMMICH_KEY`. |
| `provider.kind` | `codex` | Which backend answers. `codex` drives the Codex CLI; `openai` talks to any OpenAI-compatible endpoint. Aliases: `openai-compatible`, `gemini`, `litellm`, `ollama`. |
| `provider.text_model` | `gpt-5.6-luna` | Model for the activity sentence. |
| `provider.image_model` | `null` | Model for the picture. `null` means "same as `text_model`", which is what the Codex CLI wants. |
| `provider.reasoning_effort` | `max` | Codex only. |
| `provider.timeout_seconds` | `1800` | Per-call timeout. |
| `provider.base_url` | `null` | `openai` only. Endpoint root, e.g. `https://api.openai.com/v1`. |
| `provider.api_key_env` | `FOREDOGS_API_KEY` | `openai` only. Names the environment variable holding the key. The key itself is never written here. |
| `provider.image_size` | `1280x720` | `openai` only. Requested generation size, before the local downscale to `final_image_size`. |
| `provider.reference_image_max_px` | `1024` | `openai` only. Longest edge the reference photos are scaled to before upload. |

Dog data comes from `config/dogs.json`, which is also untracked. A profile
carries:

- `name`
- `description`
- a list of reference images under `assets/input_images/`

`AppConfig.dog_names`, `dog_descriptions` and `input_image_paths` are derived
from those profiles (`config.py:58-71`).

## Weather data

`weather.py` tries several geocoding spellings before giving up
(`weather.py:81-112`):

1. the full `location` string,
2. the string without a parenthesised suffix,
3. commas replaced by spaces,
4. a cleaned alphanumeric string,
5. the first comma-separated part,
6. the first part without a parenthesised suffix.

The forecast requests these Open-Meteo daily fields
(`weather.py:121-130`):

```text
weather_code,
 temperature_2m_max,
 temperature_2m_min,
 precipitation_sum,
 precipitation_probability_max,
 wind_speed_10m_max
```

`fetch_daily_forecast()` then normalizes the result into this dict
(`weather.py:134-147`):

| Key | Meaning |
|---|---|
| `location_resolved` | Name returned by geocoding |
| `latitude`, `longitude` | Coordinates |
| `datetime` | Forecast date |
| `condition` | Home Assistant-style English slug such as `sunny`, `rainy`, `snowy` |
| `temperature` | Daily maximum, rounded |
| `templow` | Daily minimum, rounded |
| `weather_code` | Open-Meteo WMO code |
| `weather_summary_de` | Short German description, used by the German-language panel |
| `precipitation_sum` | Daily precipitation in millimetres |
| `precipitation_probability_max` | Maximum probability |
| `wind_speed_10m_max` | Maximum wind speed |

The same format can be replayed locally with `--forecast-file`. That matters for
tests against `samples/ha_forecast_sample.json` and for reproducible prompt
inspection (`main.py:44-50`, `main.py:64-75`).


## Environment variables

Nothing that unlocks an account is stored in a configuration file. The generator
reads these from the environment instead:

| Variable | Read by | Purpose |
|---|---|---|
| `FOREDOGS_HA_TOKEN` | `publish.py` | Long-lived Home Assistant access token for the upload and the render call. Required. |
| `FOREDOGS_API_KEY` | `providers/openai_compat.py` | API key for the `openai` provider. The variable name is configurable via `provider.api_key_env`. |
| `FOREDOGS_IMMICH_KEY` | `immich.py` | Immich API key. Optional; without it archiving is skipped. |
| `FOREDOGS_OPENAI_BASE_URL` | `custom_components/foredogs/foredogs.py` | Endpoint for the legacy in-Home-Assistant generator, when the service call does not pass `base_url`. |
| `FOREDOGS_TRIGGER_ENTITY` | `scripts/trigger_watch.sh` | Which `input_button` the poller watches. Defaults to `input_button.foredogs_generation_trigger`. |
| `FOREDOGS_STATUS_ENTITY` | `scripts/trigger_watch.sh` | Which `input_text` receives the run status. Defaults to `input_text.foredogs_generation_status`. |

The scheduled jobs load them from files rather than from a shell profile, since
`launchd` and `systemd` do not read one:

```sh
mkdir -p ~/.config/foredogs
printf '%s' 'your-long-lived-token' > ~/.config/foredogs/ha_token
chmod 600 ~/.config/foredogs/ha_token
```

`scripts/macos/*.plist` and `scripts/linux/*.service` both `cat` those files at
start. See [mac-pipeline.md](mac-pipeline.md) for the exact wiring.

## Secrets and credentials

| Secret | Where it belongs | Never in |
|---|---|---|
| Home Assistant token | `~/.config/foredogs/ha_token` | `config.json` |
| Model API key | `~/.config/foredogs/api_key`, exported as `FOREDOGS_API_KEY` | `config.json`, automations |
| Immich API key | `~/.config/foredogs/immich_key` | `config.json` |
| WiFi, OTA, API key, dashboard URL | `esphome/secrets.yaml` | `esphome/e1002-kitchen.yaml` |

`esphome/secrets.yaml.example` lists every key the firmware expects. Copy it and
fill it in:

```sh
cp esphome/secrets.yaml.example esphome/secrets.yaml
chmod 600 esphome/secrets.yaml
```

Two of those deserve a note. `api_encryption_key` must be freshly generated —
`python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"` —
and not a base64-encoded phrase, which is guessable. And `dashboard_url` should
name an IP address: the ESP32's HTTP client does not resolve other hosts over
mDNS, so a `.local` name that works from your laptop can still fail on the
device.

`.gitignore` already excludes `generator/config.json`, `generator/config/`,
`esphome/secrets.yaml` and the data directories. Verify before your first push:

```sh
git ls-files | grep -E 'secrets\.yaml$|config\.json$'   # expect no output
```

## Home Assistant side

The render service takes its entity wiring per call, not from a configuration
file, so one Home Assistant can drive several panels with different sensors.
Every field is documented in [dashboard.md](dashboard.md#configuring-the-render-call);
the ready-made script is `examples/homeassistant/render_dashboard_script.yaml`.

## Firmware side

`esphome/e1002-kitchen.yaml` puts everything adjustable in its `substitutions:`
block — device name, timezone, refresh time, retry interval. See
[display.md](display.md#substitutions).
