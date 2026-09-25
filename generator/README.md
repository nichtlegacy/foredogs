# foredogs generator

The standalone worker for the foredogs image pipeline. It runs independently of
the Home Assistant custom component: it fetches weather, builds two prompts,
generates one picture a day, and publishes the result to Home Assistant.

Developed and running on macOS. The Python package itself is
platform-independent; see [Scheduling](#scheduling) for Linux.

1. fetches live weather from Open-Meteo,
2. picks an art style that has not been used recently,
3. generates the day's activity description,
4. generates the final image from your reference photos,
5. post-processes it for the display,
6. publishes it to Home Assistant and archives it in Immich.

See [`../docs/01-image-generation.md`](../docs/01-image-generation.md) for the
prompt logic in detail, and
[`../docs/02-mac-pipeline.md`](../docs/02-mac-pipeline.md) for launchd,
publishing and credentials.

## Requirements

- macOS or Linux
- `python3` with Pillow
- network access for Open-Meteo
- a model backend, either:
  - the `codex` CLI, installed and logged in (default), or
  - `pip install openai` plus an API key, for any OpenAI-compatible endpoint

```bash
python3 -m pip install -r requirements.txt
```

See [Model backend](#model-backend) for choosing between them.

## Setup

Three configuration files are deliberately untracked, because they describe one
specific household. Copy the examples and edit them:

```bash
cp ../examples/generator/config.example.json config.json
cp ../examples/generator/dogs.example.json config/dogs.json
cp ../examples/generator/celebrations.example.yaml config/celebrations.yaml
```

Then put your own reference photos in `assets/input_images/` and point
`config/dogs.json` at them. Four photos per dog from different angles work
well.

| File | What it holds |
|---|---|
| `config.json` | location, weather provider, output paths, image size, display profile, Codex model, publish and Immich targets |
| `config/dogs.json` | dog names, descriptions and reference image paths |
| `config/celebrations.yaml` | your own birthdays and anniversaries (optional) |
| `config/art_styles.json` | the shipped pool of 75 art styles, tracked in git |

Credentials never live in a configuration file. The scripts read them from:

```text
~/.config/foredogs/ha_token      Home Assistant long-lived access token
~/.config/foredogs/immich_key    Immich API key (optional)
```

Both should be mode `0600`.

## Model backend

`provider.kind` in `config.json` picks how the models are reached.

```json
"provider": { "kind": "codex", "text_model": "gpt-5.6-luna", "reasoning_effort": "max" }
```

```json
"provider": {
  "kind": "openai",
  "text_model": "gemini-2.5-flash",
  "image_model": "gemini-3-pro-image",
  "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
  "api_key_env": "FOREDOGS_API_KEY"
}
```

`codex` holds no endpoint and no key: whatever the CLI is logged into answers.
`openai` reaches OpenAI, Google, LiteLLM, OpenRouter, Ollama or a local proxy,
and reads the key from the environment variable named by `api_key_env` — never
from `config.json`.

A configuration without a `provider` block keeps using the older
`generator.codex_*` keys, so an existing install needs no edit.

Full reference: [`../docs/01-image-generation.md`](../docs/01-image-generation.md#choosing-a-model-backend).

## Commands

Dry run — prompts and status only, no image and no upload:

```bash
python3 -m foredogs_generator.main --config config.json --json --dry-run
```

Dry run against a saved forecast, for reproducible prompt inspection:

```bash
python3 -m foredogs_generator.main --config config.json \
  --forecast-file samples/ha_forecast_sample.json --json --dry-run
```

Full live run:

```bash
python3 -m foredogs_generator.main --config config.json --json
```

Generate and publish in one step, the way launchd does it:

```bash
./scripts/daily_run.sh
./scripts/daily_run.sh --dry-run
./scripts/daily_run.sh --publish-only
```

Pre-generate ten weather and style variants:

```bash
python3 -m foredogs_generator.batch --config config.json --count 10
```

Print the resolved calendar for a year, to check your dates landed where you
expect:

```bash
python3 -m foredogs_generator.main --config config.json --list-celebrations --year 2027
```

Run the tests:

```bash
./scripts/run_tests.sh
```

## Scheduling

The generator itself is platform-independent — the Python package contains no
macOS-specific code. Only the scheduling layer differs, and both variants ship:

| Platform | Units | Status |
|---|---|---|
| macOS | [`scripts/macos/`](scripts/macos/) launchd agents | in production |
| Linux | [`scripts/linux/`](scripts/linux/) systemd user units | untested, please report |

Both contain an absolute path to this checkout — edit it if the repository lives
somewhere other than `~/opt/foredogs`.

The shell scripts are POSIX `sh`, so they run under bash, dash and zsh alike.

```bash
cp scripts/macos/com.foredogs.daily.plist ~/Library/LaunchAgents/
cp scripts/macos/com.foredogs.trigger.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.foredogs.daily.plist
launchctl load ~/Library/LaunchAgents/com.foredogs.trigger.plist
```

The trigger watcher defaults to `input_button.foredogs_generation_trigger` and
`input_text.foredogs_generation_status`. Override them with
`FOREDOGS_TRIGGER_ENTITY` and `FOREDOGS_STATUS_ENTITY` if your helpers are
named differently.

## Outputs

A dry run writes:

```text
output/foredogs_generation_status.json
output/latest_activity_prompt.txt
output/latest_image_prompt.txt
```

A full run additionally writes:

```text
output/foredogs_original.png     uploaded to Home Assistant
output/foredogs_optimized.png    local preview at the display size
```

The original is what gets published. The Home Assistant renderer does its own
cropping and dithering, so uploading the already-optimized copy would apply
image processing twice.

## Migrating from the Home Assistant integration

If the custom component already ran and you want to keep its history, pull the
reference photos and the prompt and style history across:

```bash
HA_SSH_HOST=homeassistant ./scripts/import_from_home_assistant.sh
```

## Notes

- `codex_model` is the host/agent model for both the prompt and the image tool
  call. In the current Codex CLI flow it is not a guaranteed direct selector for
  the underlying raster image model.
- The Codex CLI bridge sends prompts over stdin when reference images are
  attached. That detail is required for `codex exec -i ...` to work reliably.
