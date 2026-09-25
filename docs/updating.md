# Updating

## Migrating from the pre-restructure layout

Early installs cloned the whole repository directly into
`/config/custom_components/foredogs/`, because the integration's Python files
lived at the repository root. That is no longer true: they now live in
`custom_components/foredogs/`, alongside the generator and the firmware.

**A `git pull` in an old install removes the integration.** The files it loads
are no longer at the path it cloned them to, and Home Assistant will fail to
import the component on the next restart.

Check whether you are affected:

```sh
ls /config/custom_components/foredogs/
```

If you see `LICENSE`, `README.md` or `config_examples/` next to the `.py` files,
you have the old layout.

### The migration

Home Assistant keeps running on the old files throughout; nothing is removed
until the new directory is in place.

```sh
# 1. Back up, including any local edits you forgot about.
cd /config/custom_components
tar -czf ~/foredogs-integration-backup.tar.gz foredogs

# 2. Confirm you have no local changes worth keeping. Anything listed as
#    modified here exists only on this machine.
cd foredogs && git status --short && cd ..

# 3. Clone the repository somewhere that is not a component directory.
git clone https://github.com/nichtlegacy/foredogs.git /config/foredogs-src

# 4. Swap the component directory for just the component.
rm -rf /config/custom_components/foredogs
cp -r /config/foredogs-src/custom_components/foredogs /config/custom_components/

# 5. Restart Home Assistant, then check the log for "foredogs".
```

Updating afterwards:

```sh
cd /config/foredogs-src && git pull
cp -r custom_components/foredogs /config/custom_components/
# restart Home Assistant
```

A symlink instead of a copy works on most installs and makes `git pull` the
only step, but it is not supported everywhere and it hides what is loaded.
The copy is two commands and always behaves.

Better still: once the repository is registered in HACS, drop the source clone
and let HACS handle it.

### Keep the data directories

The migration touches only `/config/custom_components/foredogs/`. These are
separate and must stay:

```text
/config/foredogs_data/          fonts, reference photos, celebrations.yaml
/config/www/daily_foredogs/     the published pictures and dashboard.png
```

---

## How updates work from here

The three parts update independently. That is deliberate — a firmware flash and
a dashboard change should never have to happen on the same day.

| Part | Mechanism | Needs |
|---|---|---|
| Home Assistant integration | HACS, or copy from a source clone | restart |
| Generator | `git pull` in the checkout | nothing; the next run uses it |
| Firmware | ESPHome OTA | `stay_awake` on first |

### Integration

Through HACS once the repository is registered. HACS reads GitHub releases, so
it offers a version rather than whatever is on `master`.

### Generator

```sh
cd ~/opt/foredogs && git pull
cd generator && ./scripts/run_tests.sh
./scripts/daily_run.sh --dry-run
```

Your configuration is untracked, so a pull never touches it:

```text
generator/config.json
generator/config/dogs.json
generator/config/celebrations.yaml
generator/assets/  state/  output/  logs/
```

If a release adds a configuration key, the CHANGELOG says so and the matching
file in `examples/generator/` shows it. Missing keys fall back to their
defaults, so a pull never breaks a run because of a new option.

### Firmware

```sh
# 1. Home Assistant: turn on input_boolean.e1002_stay_awake
# 2. Flash
ESPHOME_BUILD_PATH=/tmp/esphome-build esphome run esphome/e1002-kitchen.yaml
# 3. Watch it boot and stay up for at least 20 seconds
# 4. Turn stay_awake back off
```

Step 4 is not optional. Left on, the panel skips deep sleep and loses about a
third of its battery in one night.

An OTA that reports success but leaves the old firmware running is case 2 in
[`operations.md`](operations.md).

---

## Versioning

[Semantic versioning](https://semver.org/), with the integration's
`manifest.json` version matching the release tag.

| Change | Bump |
|---|---|
| A service parameter is removed or renamed; a configuration key changes meaning; the firmware needs a new Home Assistant helper | **major** |
| A new service parameter, art style, page or configuration key, all with defaults | **minor** |
| A fix that needs no configuration change | **patch** |

Anything requiring a manual step after updating goes in the CHANGELOG under
`### Migration`, not only in the commit message.

Each part carries its own compatibility surface:

- **Integration ↔ generator:** the `render_script` name in `config.json` and the
  published image path. Changing either is a major bump.
- **Integration ↔ firmware:** the dashboard PNG path and the render script name.
- **Generator ↔ Home Assistant:** the trigger and status entity IDs, which are
  overridable precisely so renaming them is not a breaking change.

## Rollback

```sh
# Integration
cd /config/foredogs-src && git checkout v1.2.3
cp -r custom_components/foredogs /config/custom_components/

# Generator
cd ~/opt/foredogs && git checkout v1.2.3

# Firmware: flash the older YAML. ESPHome keeps no image history, and after a
# successful boot the previous image is gone, so keep the YAML rather than
# relying on the device.
```

Rolling the generator back is always safe: its state files are append-only
histories that an older version still reads.
