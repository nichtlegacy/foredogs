# macOS scheduling (launchd agents)

The generator itself is platform-independent; only the scheduling layer differs.
The Linux equivalents live in `../linux/`.

Both plists contain an absolute path to the checkout. They assume
`~/opt/foredogs` — edit the `exec` line if yours lives elsewhere.

## Install

```sh
cp com.foredogs.daily.plist ~/Library/LaunchAgents/
cp com.foredogs.trigger.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.foredogs.daily.plist
launchctl load ~/Library/LaunchAgents/com.foredogs.trigger.plist
```

## Check

```sh
launchctl list | grep foredogs
tail -f ../../logs/daily_run.log

# Run once by hand
launchctl start com.foredogs.daily
```

## Uninstall

```sh
launchctl unload ~/Library/LaunchAgents/com.foredogs.daily.plist
launchctl unload ~/Library/LaunchAgents/com.foredogs.trigger.plist
```

## Why these timings

`com.foredogs.daily` runs at 04:30 rather than just before the display wakes at
05:45. Generation usually takes 3–7 minutes but has been measured at 19.7, and a
15-minute gap once meant the panel had already fetched and gone back to sleep
before the new picture existed. `RunAtLoad` is deliberately `false`: loading the
agent must not spend a generation.

`com.foredogs.trigger` polls every 120 seconds with `RunAtLoad` set to `true`,
so a button press made while the Mac slept is picked up right after it returns.

Both set `ExitTimeOut` to 2400 seconds, because one generation at max reasoning
effort can take several minutes.

If the Mac is asleep at the scheduled time, launchd runs the job at the next
wake, so a laptop that is only open in the evening still produces one image a
day.

The reasoning behind each of these is documented in
`../../../docs/02-mac-pipeline.md`.
