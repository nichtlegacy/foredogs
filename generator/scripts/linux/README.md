# Linux scheduling (systemd user units)

The generator itself is platform-independent; only the scheduling layer differs.
These are the systemd equivalents of the launchd agents in `../macos/`.

**Untested.** The project runs in production on macOS. These units are written
from the launchd behaviour they mirror, and the reasoning behind each timing is
documented in `../../../docs/02-mac-pipeline.md`. Please report what breaks.

## Install

The units assume the checkout lives at `~/opt/foredogs`. If it does not, edit
the `WorkingDirectory` and `ExecStart` lines.

```sh
mkdir -p ~/.config/systemd/user
cp foredogs-*.service foredogs-*.timer ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now foredogs-daily.timer
systemctl --user enable --now foredogs-trigger.timer

# Survive logout, so the timers fire without an active session.
sudo loginctl enable-linger "$USER"
```

## Check

```sh
systemctl --user list-timers 'foredogs-*'
systemctl --user status foredogs-daily.service
journalctl --user -u foredogs-daily.service -n 50

# Run once by hand
systemctl --user start foredogs-daily.service
```

## Credentials

Same as on macOS: the scripts read them from files, not from the unit.

```sh
install -m 700 -d ~/.config/foredogs
install -m 600 /dev/null ~/.config/foredogs/ha_token
install -m 600 /dev/null ~/.config/foredogs/immich_key
```

If your Home Assistant is not reachable as `homeassistant.local`, set `HA_URL`
in `foredogs-trigger.service`, and set `publish.ha_url` in `config.json`.
