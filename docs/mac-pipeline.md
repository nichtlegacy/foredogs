# The generator host: scheduling, publishing, archiving

## Responsibility

The macOS side ties three steps together:

1. daily generation,
2. handover to Home Assistant,
3. idempotent archiving in Immich.

A separate launchd agent polls a Home Assistant button for manual generations.
Home Assistant therefore never has to reach the Mac and needs no SSH access to
it.

## launchd agents

### `com.foredogs.daily.plist`

Source: `generator/scripts/macos/com.foredogs.daily.plist`.

| Key | Value | Effect |
|---|---|---|
| `Label` | `com.foredogs.daily` | launchd name. |
| `ProgramArguments` | `/bin/zsh -lc ... daily_run.sh` | Login shell; exports credentials, runs the shell script. |
| `StartCalendarInterval.Hour` | `4` | Hour 04. |
| `StartCalendarInterval.Minute` | `30` | Starts at 04:30. |
| `RunAtLoad` | `false` | Loading the agent must not start a paid generation. |
| `StandardOutPath` | `/tmp/foredogs-daily.out.log` | launchd stdout. |
| `StandardErrorPath` | `/tmp/foredogs-daily.err.log` | launchd stderr. |
| `ExitTimeOut` | `2400` | 2400 seconds; `max` reasoning and the image tool may run for several minutes. |
| `ProcessType` | `Background` | Background job. |

The 04:30 slot is not arbitrary. It is the buffer before the single daily
display wake at 05:45. The panel loads exactly whatever Home Assistant rendered
last at that moment.

> **Changed on 2026-08-02:** the job previously ran at 05:30, only 15 minutes
> ahead. Generation usually takes 3–7 minutes, but one run was measured at 19.7
> minutes. On 2026-08-02 publishing therefore only finished at 05:49 — four
> minutes after the display had already downloaded and gone back to sleep. The
> panel showed the previous day's picture all day. 04:30 gives 75 minutes of
> headroom.

If the Mac is asleep at the scheduled time, launchd runs the job at the next
wake (`com.foredogs.daily.plist:18-24`).

### `com.foredogs.trigger.plist`

Source: `generator/scripts/macos/com.foredogs.trigger.plist`.

| Key | Value | Effect |
|---|---|---|
| `Label` | `com.foredogs.trigger` | Name of the polling agent. |
| `StartInterval` | `120` | Polls every two minutes. |
| `RunAtLoad` | `true` | A press made while the Mac slept is picked up right after it returns. |
| `ExitTimeOut` | `2400` | A manual generation may run for up to 40 minutes. |
| `ProcessType` | `Background` | Background job. |
| stdout/stderr | `/tmp/foredogs-trigger.out.log`, `/tmp/foredogs-trigger.err.log` | launchd output. |

Two minutes is a compromise between perceived responsiveness and minimal HTTPS
traffic. Image generation takes several minutes anyway
(`com.foredogs.trigger.plist:3-9`).

Installation:

```zsh
cp generator/scripts/macos/com.foredogs.daily.plist ~/Library/LaunchAgents/
cp generator/scripts/macos/com.foredogs.trigger.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.foredogs.daily.plist
launchctl load ~/Library/LaunchAgents/com.foredogs.trigger.plist
```

Both plists contain the absolute path to the checkout. Adjust it if the
repository lives somewhere else.

## Loading credentials

The launchd arguments load no secret values from this documentation or from
`config.json`, but from two local files:

```zsh
export FOREDOGS_HA_TOKEN="$(cat ~/.config/foredogs/ha_token 2>/dev/null)"
export FOREDOGS_IMMICH_KEY="$(cat ~/.config/foredogs/immich_key 2>/dev/null)"
```

The exact lines are in both plists (`com.foredogs.daily.plist:31-37`,
`com.foredogs.trigger.plist:25-31`). Both files should be mode `0600`.

`publish_to_ha.py` reads:

- `FOREDOGS_HA_TOKEN` for the Home Assistant bearer token
  (`publish_to_ha.py:69-77`),
- `FOREDOGS_IMMICH_KEY` for the `x-api-key` header
  (`publish_to_ha.py:37-57`).

`daily_run.sh` loads the Immich key again in case a manual invocation did not
inherit the environment variable (`daily_run.sh:85-89`). `trigger_watch.sh`
falls back to `~/.config/foredogs/ha_token` when the environment variable is
empty (`trigger_watch.sh:36-43`).

### SSH

`publish.py` uses a host alias and these options:

```python
ssh_options: tuple[str, ...] = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=10")
```

Source: `generator/foredogs_generator/publish.py:37-51`.

Set the alias up in `~/.ssh/config` with its own identity file. `publish.py`
knows no password and passes no password arguments; key-based authentication is
required. Never write credentials into `config.json` or into the scripts.

### Secrets in this repository

- `esphome/*.yaml` reference all credentials as `!secret`. The real values
  belong in the ESPHome `secrets.yaml`; see `esphome/secrets.yaml.example`.
- An API key used by a Home Assistant automation belongs in Home Assistant's own
  `secrets.yaml` and is referenced as `!secret`.

## `scripts/daily_run.sh`

Source: `generator/scripts/daily_run.sh`.

### Arguments

| Argument | Behaviour |
|---|---|
| none | Generate, then publish. |
| `--dry-run` | Produce weather, style, prompts and status only — no image, no upload. |
| `--publish-only` | Re-publish the existing `output/foredogs_original.png`. |
| unknown | Log entry, exit `1`. |

### PATH handling

launchd starts with a minimal PATH, so the script appends the standard paths
(`daily_run.sh:35-39`):

```zsh
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
```

Important: **append**, do not prepend. Putting Homebrew ahead of the existing
PATH shadowed the `python3` that has Pillow installed with a different Python.
The script then explicitly looks for an interpreter where `import PIL` works
(`daily_run.sh:41-55`):

1. `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3`,
2. `command -v python3`,
3. `/opt/homebrew/bin/python3`,
4. `/usr/bin/python3`.

With no suitable interpreter it reports:

```text
no python3 with Pillow found; run: python3 -m pip install -r requirements.txt
```

It then checks that `codex` is in PATH (`daily_run.sh:63`).

### Exit code phases

```text
0  published successfully
1  generation or preparation failed
2  upload failed
```

`daily_run.sh` deliberately uses `rc` as the return-value variable
(`daily_run.sh:72-76`, `91-97`). In zsh, `status` is read-only; using it made
the script abort immediately before publishing.

### Generation and publishing

The normal path:

```zsh
"$PYTHON" -m foredogs_generator.main --config config.json --json
"$PYTHON" scripts/publish_to_ha.py
```

On a successful generation the script writes `generation ok`, then
`=== publish start ===`, and finally `published`. Failures are logged with a
timestamp in `logs/daily_run.log` (`daily_run.sh:23`, `65-100`).

## Manual button pipeline: `trigger_watch.sh`

### Why poll instead of having Home Assistant push?

`trigger_watch.sh` explains the architectural decision
(`trigger_watch.sh:3-8`):

- Home Assistant has no SSH key for the Mac.
- macOS stealth mode drops inbound pings.
- Outbound polling needs no open port and no new credential.
- A press made while the Mac slept survives, because the button keeps its last
  timestamp as its state.

### State and idempotency

The button defaults to:

```text
input_button.foredogs_generation_trigger
```

Override it with `FOREDOGS_TRIGGER_ENTITY` if your helper is named differently.
Its state is the timestamp of the last press. The script stores the value it
last acted on in:

```text
~/.local/state/foredogs/last_trigger
```

The sequence (`trigger_watch.sh:45-83`):

1. `GET /api/states/<trigger entity>`.
2. `unknown` and `unavailable` are treated as empty.
3. An empty response means Home Assistant is unreachable or the button has
   never been pressed; the script exits without log spam.
4. A state equal to `last_trigger` means it has already been handled.
5. On the very first run the current value is only recorded — installing the
   agent does not immediately produce an image.
6. On a new timestamp the value is stored **before** generation starts. A crash
   therefore does not cause an endless retry on every two-minute tick.

### Home Assistant status field

Before starting, `notify()` sets:

```text
input_text.foredogs_generation_status = running since HH:MM
```

After success:

```text
done HH:MM
```

After failure:

```text
failed HH:MM
```

The status entity is overridable with `FOREDOGS_STATUS_ENTITY`. The service call
is `input_text/set_value` (`trigger_watch.sh:84-101`).

### Why always `exit 0`?

The wrapper catches generation and publish failures, logs the exit code and
writes the Home Assistant status. After that it always exits successfully
(`trigger_watch.sh:104-108`). A non-zero exit would make launchd throttle the
agent and, after repeated failures, stop it altogether. The button would then
look broken until someone reloaded the agent by hand. That has already
happened once.

## Publishing to Home Assistant

### Target object

`PublishTarget` (`publish.py:37-51`):

| Field | Default / configured value |
|---|---|
| `ssh_host` | SSH alias, default `homeassistant` |
| `remote_dir` | `/config/www/daily_foredogs/dogs` |
| `condition` | empty; optional weather subfolder |
| `ha_url` | from `config.json` |
| `ha_token` | `FOREDOGS_HA_TOKEN` |
| `render_script` | from `config.json`, e.g. `script.foredogs_render_dashboard` |
| `ssh_options` | BatchMode plus a 10-second ConnectTimeout |

### Dated image and `latest.png`

`publish_image()` (`publish.py:74-125`) does the following:

1. The local file must exist.
2. The date defaults to `date.today()`.
3. The remote subfolder is created with `mkdir -p`.
4. The target file name is built as `YYYY-MM-DD.png`.
5. `scp` writes to `.<date>.png.part`.
6. A remote `mv` moves `.part` atomically to `YYYY-MM-DD.png`.
7. Optionally it is also copied to
   `/config/www/daily_foredogs/dogs/latest.png`.

Why `.part` plus `mv`? A slow `scp` must never leave behind a PNG that the
renderer or the camera entity might open mid-write. `mv` within the same
filesystem is the handover point.

Why the date? `image_source.py` looks for today's picture first, then weather
pools, then the most recent dated picture. A missed generation day therefore
still shows a sensible earlier picture (`image_source.py:9-16`, `117-146`).

### Render POST

`trigger_render()` turns the configured render script into:

```text
POST http://homeassistant.local:8123/api/services/script/foredogs_render_dashboard
Authorization: Bearer <FOREDOGS_HA_TOKEN>
Content-Type: application/json
Body: {}
```

Source: `publish.py:128-157`.

An unreachable Home Assistant does not produce a publish failure. The picture is
already copied, and the next scheduled render picks it up. That decision keeps a
temporary API outage from marking a successful local generation as a complete
failure.

### Upload order

`publish_to_ha.py:82-119` loads `output/foredogs_original.png`, calls
`publish()`, and only archives afterwards:

```text
original exists
  -> HA file + render trigger
  -> Immich archive
```

The renderer does its own cropping and dithering. Uploading the Mac-optimized
copy would apply image processing twice.

## The local archive

Every generated original is also copied to `generator/output/archive/` under its
date. `archive_keep_days` defaults to `0`, which keeps all of them; at roughly
2.5 MB a day that is under a gigabyte a year.

This exists because the obvious copies are all short-lived, and they failed
together once:

| Copy | Lifetime | What went wrong |
|---|---|---|
| `output/foredogs_original.png` | one day | overwritten by the next run |
| Home Assistant `dogs/YYYY-MM-DD.png` | `keep_days` | was set to 5 |
| Immich album | indefinite | the server was down for three weeks |

By the time the outage was noticed, Home Assistant had already pruned
everything older than five days. Sixteen days of pictures were gone, and no
copy existed on the machine that had made them.

The archive is written *before* publishing, so a failed upload cannot also cost
the local copy. Re-running a day overwrites that day's file rather than
accumulating duplicates, and pruning only ever touches files whose name is a
date — anything hand-placed is left alone.

## Immich archive

### Purpose

Home Assistant keeps only the files it needs for rendering and for current
links. Immich keeps the history in an album. The code explains why: daily PNGs
would fill up the Home Assistant filesystem even though the panel only ever
requests the current picture (`immich.py:1-11`).

### Album resolution

`resolve_album()` (`immich.py:77-94`):

1. use an existing `album_id` directly,
2. otherwise `GET /api/albums`,
3. look for a matching `albumName`,
4. create it with `POST /api/albums` if it does not exist.

### SHA-1 deduplication

`already_uploaded()` reads the file, computes SHA-1 and sends:

```text
POST /api/assets/bulk-upload-check
x-api-key: <FOREDOGS_IMMICH_KEY>
{
  "assets": [
    {"id": "<image.name>", "checksum": "<sha1>"}
  ]
}
```

Source: `immich.py:97-115`.

For `action == reject` with `reason == duplicate`, Immich returns the existing
asset ID. That is more robust than a local ledger file: a restore, a lost state
directory or a repeated publish all give the same result.

### Upload and album membership

When no duplicate is found, `upload()` sends (`immich.py:118-157`):

- `x-api-key`,
- `x-immich-checksum: <sha1>`,
- `deviceAssetId=<filename>-<timestamp>`,
- `deviceId=foredogs-macos`,
- `fileCreatedAt` and `fileModifiedAt` from `taken` or the filesystem time,
- `assetData=@<image>`.

`add_to_album()` then adds the asset via
`PUT /api/albums/<album_id>/assets`. A `duplicate` in the album response counts
as success (`immich.py:160-175`).

### Idempotent `archive()`

`archive()` (`immich.py:178-201`):

- existing asset: do not upload again, but make sure album membership is set,
- new asset: upload, then set the album,
- the return value carries `asset_id`, `uploaded`, `album_id` and optionally
  `in_album`.

The same day's run can therefore be repeated safely.

## Active and historical image paths

| Path | Status |
|---|---|
| `/config/www/daily_foredogs/dogs/YYYY-MM-DD.png` | Active dated sink for generator pictures. |
| `/config/www/daily_foredogs/dogs/latest.png` | Active stable link; the camera entity points at it. |
| `/config/www/daily_foredogs/dashboard.png` | Final product built by the Home Assistant renderer for the E1002. |
| `/config/www/daily_foredogs/foredogs_original.png` | Legacy single-file path; still supported as `source_image`, but the current pipeline writes into `dogs/`. |
| `/config/www/daily_foredogs/foredogs_optimized.png` | Legacy output of the old Home Assistant generator pipeline; do not use it as the E1002 download. |

## Operating commands

```zsh
# Check only the prompt and weather pipeline
cd generator
scripts/daily_run.sh --dry-run

# Re-upload the last original
scripts/daily_run.sh --publish-only

# Run the generator directly against a saved forecast
python3 -m foredogs_generator.main \
  --config config.json \
  --forecast-file samples/ha_forecast_sample.json \
  --json \
  --dry-run

# Logs
less generator/logs/daily_run.log
less generator/logs/trigger_watch.log
```

Before a manual live run, check:

- `codex` is in PATH,
- a `python3` where `import PIL` works,
- the SSH alias resolves and authenticates by key,
- `~/.config/foredogs/ha_token` and `~/.config/foredogs/immich_key` are readable
  (`0600`),
- the configured render script exists in Home Assistant.
