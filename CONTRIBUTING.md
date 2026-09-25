# Contributing to Foredogs

## How to contribute

1. **Fork the repository** and create your branch from `master`.
2. **Make your changes** with descriptive commit messages.
3. **Test your changes** to make sure nothing is broken.
4. **Submit a pull request** describing what you changed. If you are fixing an
   issue, add `closes #<issue number>` to the description.

## Never commit personal data

Three files are untracked on purpose, because they describe one specific
household. Do not add them to a commit, and do not replace the examples with
your own values:

```text
generator/config.json
generator/config/dogs.json
generator/config/celebrations.yaml
```

The same goes for reference photos, generated pictures, ESPHome
`secrets.yaml`, and anything under `generator/state/`, `generator/output/` or
`generator/logs/`. Before opening a pull request:

```bash
git diff --stat origin/master...HEAD
git log -p origin/master...HEAD | grep -iE 'password|token|api[_-]?key|sk-'
```

If a change needs a new configuration value, add it to the matching file in
`examples/` with a placeholder.

## Changing what the panel draws

The landing page at [foredogs.nichtlegacy.com](https://foredogs.nichtlegacy.com/)
shows frames rendered by `custom_components/foredogs/dashboard_render.py`. They
are committed under `site/screens/`, so a pull request that changes the layout
should regenerate them in the same commit:

```bash
python3 tools/build_screens.py
python3 tools/build_screens.py --check   # passes once the frames are current
```

The README's hero, `.github/images/dashboard.png`, is a photograph of the
page's own reTerminal, taken by `tools/capture_readme.cjs` from a running
`tools/build_site.sh --serve`. It needs Node and `playwright-core`; the header
of the script has the command.

## Testing

The generator has a unit test suite:

```bash
cd generator
./scripts/run_tests.sh
```

It runs without network access, without the `codex` CLI and without a real
configuration. Tests use a placeholder location, so do not introduce a real
place name into a fixture.

There is also a cost-free end-to-end check of the prompt pipeline:

```bash
cd generator
./scripts/verify_dry_run.sh
```

The Home Assistant integration has no automated tests. Please try changes on a
real Home Assistant instance before submitting.

## Guidelines

- If you change the service schema, reflect it in `custom_components/foredogs/services.yaml`
  and in `examples/homeassistant/automation_fragment.yaml`.
- Keep the dashboard renderer inside the six Spectra 6 device colours. Anything
  else is invisible on the panel.
- ESPHome credentials are `!secret` references. Never put a literal value in a
  device YAML.
- `docs/` describes the code as it actually is, including known divergences
  between example configuration and a live installation. If you change
  behaviour, update the matching chapter.

## Need help?

Open an issue or start a discussion.
