# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

Each release states which parts it touches: **integration** (Home Assistant),
**generator** (the macOS/Linux worker) or **firmware** (ESPHome). They update
independently, so a release rarely requires touching all three.

foredogs is a modified version of
[forecats](https://github.com/jwardbond/forecats) by jwardbond, GPL-3.0. The
fork dates from February 2026; everything below describes changes made since.

## [Unreleased]

## [2.0.0] – 2026-09-25

The first public release of the rebuilt stack: a standalone generator, a Home
Assistant renderer for a six-colour e-paper panel, ESPHome firmware for the
reTerminal E1002, and a landing page at
[foredogs.nichtlegacy.com](https://foredogs.nichtlegacy.com/). A major version,
because the layout, the package names and the configuration files all moved;
the migration notes below list every step.

### Migration

- **Generator: `config/art_styles.json` is no longer tracked.** It is one
  household's taste, and a live pool tends to accumulate named franchises.
  Existing files are left alone; new installs copy
  `examples/generator/art_styles.example.json`. If you pull into a checkout
  that had the tracked file, git removes it — keep a copy first.
- **Integration: the built-in style pool is now twelve generic entries**, not
  forty-seven naming franchises and characters. A service call that passes its
  own `art_styles` is unaffected.

- **Firmware: the render script is now a substitution.** The device asked for
  `script.e1002_render_dashboard`; every example created
  `script.foredogs_render_dashboard`. The name now lives in
  `substitutions.render_script` and defaults to the documented one. A device
  flashed before this change keeps calling the old name until it is reflashed —
  either reflash it, or keep a one-line script under the old name that calls
  the new one.
- **Repository: four ESPHome variants and three Home Assistant examples were
  removed.** They described the architecture before the render service existed.
  `esphome/e1002-kitchen.yaml` is the firmware;
  `examples/homeassistant/render_dashboard_script.yaml` is the script.

- **Integration: the repository layout changed.** The Python files moved from
  the repository root into `custom_components/foredogs/`. An install that
  cloned the whole repository into `/config/custom_components/foredogs/` is
  broken by a `git pull`. See
  [`docs/updating.md`](docs/updating.md) for the swap; Home Assistant
  keeps running on the old files until the new directory is in place.
- **Generator: the Python package was renamed** from `foredogs_macos` to
  `foredogs_generator`, and the launchd agents moved to `scripts/macos/`.
  Reinstall the scheduler unit after pulling, or edit the `exec` path in the
  copy under `~/Library/LaunchAgents/`.
- **Generator: three configuration files are no longer tracked.**
  `config.json`, `config/dogs.json` and `config/celebrations.yaml` are now
  ignored by git, because they describe one household. Existing files are left
  alone; new installs copy them from `examples/generator/`.
- **Firmware: `dashboard_url` moved into `secrets.yaml`.** It names a host on
  your network, and it should be an IP address rather than a `.local` name,
  because the ESP32's HTTP client does not resolve other hosts over mDNS.
- **Firmware: credentials are now `!secret` references.** Create
  `/config/esphome/secrets.yaml` from `esphome/secrets.yaml.example` before the
  next flash, or the build fails.

### Added

- **Repository: a landing page** at foredogs.nichtlegacy.com, in `site/`,
  deployed by `.github/workflows/pages.yml`. Its reTerminal demo shows frames
  drawn by the integration's own renderer (`tools/build_screens.py`), so the
  page cannot show something the code no longer draws.
- **Repository: a project icon.** `design/icon/foredogs.svg` is the one
  source; `tools/build_og.py` renders the favicons, the social preview's logo
  and the integration's brand icons from it.
- **Integration: brand icons in `brand/`.** Home Assistant 2026.3 and later
  shows them on the integration's card instead of a placeholder.
- **Generator: a `tone` field on style entries.** Extra direction for styles
  whose surface aesthetic is easy to imitate badly, added to both prompts. It
  replaces a hard-coded branch that applied to one named franchise.

- **Integration: the dashboard speaks more than German.** `language` on
  `foredogs.render_dashboard` selects `de` (the default, so nothing changes for
  an existing install) or `en`. Adding another is one file in
  `custom_components/foredogs/languages/` plus a line in its registry; the
  tests fail if the two drift apart. The language is part of the fingerprint,
  so switching it repaints the panel rather than waiting for the weather.
- **A test suite for the integration**, in `tests/`, covering the language
  contract, the renderer's text and a full render of every page in every
  language. Run it with `python3 -m unittest discover -s tests`.

- **Documentation: split by topic.** Fourteen pages named after what they
  contain, replacing seven numbered chapters. Four are new:
  [`architecture.md`](docs/architecture.md) with the flow and a full-day
  sequence diagram, [`dashboard.md`](docs/dashboard.md) documenting every field
  of the render call and which entity feeds which region,
  [`automation.md`](docs/automation.md) on the three schedulers, and
  [`power.md`](docs/power.md) on measured drain and expected runtime.
- **Examples: `art_styles.example.json`.** Twelve styles that name a medium
  rather than a franchise, so a starting pool can be shipped without shipping
  a household's own.
- **Integration: the remaining five render fields are documented in
  `services.yaml`** — `image_dir`, `keep_days`, `page`, `page_count` and
  `photo` existed in the schema but not in the UI.

- **Generator: a dated local archive.** Every generated original is copied to
  `generator/output/archive/YYYY-MM-DD.png`, kept indefinitely by default and
  prunable with `archive_keep_days`. The working files are overwritten each
  run, Home Assistant prunes its own copies, and an archive server can be
  unreachable for weeks — when all three coincided, sixteen days of pictures
  were lost with no copy left on the machine that made them.
- **Examples: `keep_days` raised from 5 to 30** in the render script. Home
  Assistant holds the shortest-lived copy, so it is the buffer that has to
  cover an archive outage; five days did not.
- **Generator: selectable model backend.** `provider.kind` in `config.json`
  chooses between the `codex` CLI and any OpenAI-compatible endpoint — OpenAI,
  Google, LiteLLM, OpenRouter, Ollama or a local proxy. The CLI is no longer a
  hard requirement for a fresh install. The API key is read from the
  environment variable named by `provider.api_key_env` and never stored in the
  configuration. A config without a `provider` block keeps using the older
  `generator.codex_*` keys, so existing installs need no edit.
- **Integration:** `base_url`, `text_model` and `image_model` parameters on
  `foredogs.generate_dog_picture`, so any OpenAI-compatible endpoint — a local
  proxy, LiteLLM, OpenRouter, Ollama, OpenAI itself — can be selected from the
  service call. Previously the endpoint was a constant in the source. Omitted,
  they fall back to `FOREDOGS_OPENAI_BASE_URL` and then to Google's public
  endpoint.
- **Integration:** `hacs.json`, so the repository can be added to HACS as a
  custom repository.
- **Firmware:** the ESPHome configurations are tracked for the first time. They
  were excluded by `.gitignore`, which meant the firmware actually running the
  panel was never reviewable and could not be rolled back.
- **Generator:** systemd user units in `scripts/linux/`, as an untested
  alternative to the launchd agents.
- **Examples:** `render_dashboard_script.yaml`, the render script that the
  firmware and the generator both call. It was required by both and shipped by
  neither.
- **Examples:** the `input_button` and `input_text` helpers the manual trigger
  needs. Only the `stay_awake` boolean was documented before.
- **Docs:** an end-to-end setup guide, an updating and migration guide, and the
  five chapters describing the running system, translated to English.

### Changed

- **Integration: the dashboard footer no longer shows "Seite 1/1".** The page
  marker is drawn only when there is more than one page, and the battery gauge
  is centred in the cell it used to share, using its measured ink extent rather
  than nominal constants.
- **Integration: a sun and a tilted crescent replace the up and down arrows** next to
  sunrise and sunset. An icon is understood without being read, which is what a
  panel read from across a room needs.
- **Integration: the battery gauge carries a runtime estimate** underneath it,
  in small type. It is computed from long-term statistics, counts only the
  stretch since the last charge, and is withheld entirely unless that stretch
  spans three days and three percent — a forecast nobody double-checks must not
  be invented.
- **Generator:** the shell scripts are POSIX `sh` instead of zsh, so they run
  under bash and dash as well.
- **Generator:** the trigger watcher's Home Assistant entity IDs are
  configurable via `FOREDOGS_TRIGGER_ENTITY` and `FOREDOGS_STATUS_ENTITY`
  rather than hard-coded, so renaming a helper is not a code change.
- **Generator:** the built-in calendar no longer contains one town's local
  festivals. Town events differ per town and belong in configuration;
  `examples/generator/celebrations.regional-events.example.yaml` shows how to
  add them. The calendar has 32 built-in entries, down from 35.

### Fixed

- **Integration: the manifest now requires `openai`, not `google-genai`.** The
  code has imported `openai.OpenAI` at load time for a while; an install
  without it from elsewhere failed to set up the integration at all.
- **Integration: a temperature between -0.5 and 0 °C was drawn as "-0°".**
  Every temperature on both pages now rounds through one helper that drops
  the sign of a zero.
- **Integration: the page marker no longer collides with the battery gauge.**
  On page 1 the gauge was pinned to a fixed offset that predated the runtime
  estimate and clipped it off the panel; on pages 2 and 3 the marker was drawn
  on top of the gauge. Only visible with `page_count` above 1.

- **Integration: the `clear-night` moon rendered as a bite, not a crescent,**
  on the white forecast band. The crescent is punched in the background colour,
  which was hard-coded to black for the header.
- **Integration:** `tools/preview_dashboard.py` and the repository-local font
  fallback both still pointed at the old pre-restructure paths, so the preview
  tool could not import the renderer and silently fell back to a system font.
- **Integration:** `custom_components/foredogs/services.yaml` documents
  `gemini_api_key`, which was required but undocumented.
- **Generator:** a refusal from an image model is now reported as one. The
  base64 fallback decoded any string, so prose like "I can't create that image"
  became garbage bytes that failed later as an unreadable PNG instead of
  surfacing the actual reply.

### Security

- Removed a plaintext API key, WiFi, OTA and API credentials, private network
  addresses and personal data from the examples and the firmware. All
  credentials are now `!secret` references or files outside the repository.

---

## [1.1.0] – before this changelog

Earlier releases predate this file. `1.1.0` is the version in `manifest.json`
at the point the project was prepared for a public release: the Home Assistant
integration with the direct Gemini flow, forked from
[forecats](https://github.com/jwardbond/forecats).
