# Documentation

A daily AI-generated picture of your dogs, composited with live weather, waste
collection dates and sunrise times, painted onto a battery-powered e-paper panel
in the kitchen.

Three machines do the work and each can fail without taking the panel down —
that is the whole design. [architecture.md](architecture.md) explains it with
diagrams; everything else here is detail.

To see the panel before building one, [foredogs.nichtlegacy.com](https://foredogs.nichtlegacy.com/)
runs it in a browser, with frames drawn by the real renderer.

## Start here

| If you want to… | Read |
|---|---|
| understand the system before installing anything | [architecture.md](architecture.md) |
| build one | [setup.md](setup.md) |
| change what the panel shows | [dashboard.md](dashboard.md) |
| change what the pictures look like | [styles.md](styles.md) |
| use a different model or API | [ai-providers.md](ai-providers.md) |
| find a setting | [configuration.md](configuration.md) |
| fix something | [operations.md](operations.md) |

## All pages

**Understanding it**

| Page | Contents |
|---|---|
| [architecture.md](architecture.md) | The three machines, the daily flow, the timeline, where files live |
| [setup.md](setup.md) | From nothing to a running panel, with a check after every step |

**The generator**

| Page | Contents |
|---|---|
| [generator.md](generator.md) | The pipeline and the two prompts, in full |
| [styles.md](styles.md) | The style pool, rotation, holidays, birthdays, regional events |
| [ai-providers.md](ai-providers.md) | Codex CLI or any OpenAI-compatible endpoint |
| [mac-pipeline.md](mac-pipeline.md) | Scheduling, publishing, the archive, Immich, credentials |

**Home Assistant**

| Page | Contents |
|---|---|
| [home-assistant.md](home-assistant.md) | The integration, the two services, the render flow |
| [dashboard.md](dashboard.md) | Every region of the panel, and which entity feeds it |
| [automation.md](automation.md) | Helpers, scripts, automations, the manual trigger |

**The panel**

| Page | Contents |
|---|---|
| [display.md](display.md) | ESPHome firmware, hardware, wake and sleep, OTA, buttons |
| [power.md](power.md) | Measured drain, expected runtime, the calibration curve |

**Keeping it running**

| Page | Contents |
|---|---|
| [configuration.md](configuration.md) | Every key in every configuration file, and where secrets go |
| [operations.md](operations.md) | Daily checks, diagnostics, every failure diagnosed so far |
| [updating.md](updating.md) | Updating each part, migrating an old install, rollback |

## Conventions in these pages

**Entity IDs are examples.** `weather.forecast_home`,
`sensor.e1002_playground_battery_level` and the rest are placeholders for
whatever your installation calls them. Nothing in the code hard-codes an entity
ID; the render service takes them all as parameters.

**Line references go stale.** References like `styles.py:94-133` are accurate at
the time of writing and meant to point you at the right function, not the right
line. Search for the name.

**The panel draws in German by default.** Pass `language: en` to the render
call for English, or add your own — see
[dashboard.md](dashboard.md#languages). Code, configuration and these pages are
English regardless.

## Where this came from

foredogs is a fork of [forecats](https://github.com/jwardbond/forecats) by
jwardbond, GPL-3.0, and has changed substantially since February 2026 — the
generator moved onto its own host, the dashboard renderer and the e-paper
firmware are new. See [Credits](../README.md#credits) and the
[CHANGELOG](../CHANGELOG.md).

## A note on secrets

No secret value belongs in any file in this repository, including these pages.

- The Home Assistant token and the model API key live in `~/.config/foredogs/`,
  mode `0600`, and reach the generator as environment variables.
- Firmware credentials live in `esphome/secrets.yaml` and reach the YAML as
  `!secret` references.
- Keys used by Home Assistant automations live in Home Assistant's own
  `secrets.yaml`.

[configuration.md](configuration.md#secrets-and-credentials) has the full list
and a one-line check to run before your first push.
