# Model backends

The generator has no opinion about which model answers. It needs two things — a
line of activity text, and an image file built from the reference photos — and a
provider supplies them. Two ship.

## The two backends

| `provider.kind` | What it does | Needs |
|---|---|---|
| `codex` (default) | Runs the `codex` CLI as a subprocess | the CLI installed and logged in |
| `openai` | Talks to any OpenAI-compatible endpoint | `pip install openai` and an API key |

Aliases exist so a configuration saying `openai-compatible`, `gemini`,
`litellm`, `openrouter` or `ollama` does the obvious thing rather than failing
on a spelling. They all select the same adapter.

### `codex`

```json
"provider": {
  "kind": "codex",
  "text_model": "gpt-5.6-luna",
  "reasoning_effort": "max",
  "timeout_seconds": 1800
}
```

No endpoint and no API key live in this project. Whichever backend the CLI is
logged into answers, configured in `~/.codex/config.toml`. Changing provider
therefore needs no change here at all — but a fresh install needs the CLI
working first, which is the higher barrier of the two.

`reasoning_effort` is passed as `-c model_reasoning_effort=...` rather than a
flag, because `codex exec` has none.

### `openai`

```json
"provider": {
  "kind": "openai",
  "text_model": "gemini-2.5-flash",
  "image_model": "gemini-3-pro-image",
  "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
  "api_key_env": "FOREDOGS_API_KEY",
  "image_size": "1280x720",
  "reference_image_max_px": 1024
}
```

```sh
python3 -m pip install openai
export FOREDOGS_API_KEY="$(cat ~/.config/foredogs/api_key)"
```

`api_key_env` names the environment variable to read the key from. The key
itself never goes into `config.json`, which is why that file can be shared when
asking for help.

`image_model` defaults to `text_model` when omitted. That is right for `codex`,
where one agent orchestrates its own image tool, and usually wrong for `openai`,
where the two are different models.

### Why images go through chat completions

The picture is requested through `chat.completions` rather than
`/images/generations`, because the reference photos are not optional: they are
what keeps the dogs recognisable across seventy-five art styles.
`/images/generations` accepts a prompt and nothing else, so it cannot do this
job at all. Endpoints that return a picture from a chat call put it in the
message content as a data URL.

Providers disagree about exactly where, so three shapes are handled: a
structured `images` attribute, a content list carrying an `image_url` part, and
plain text holding a data URL or raw base64. A response that contains no image —
a refusal, most often — is reported with the first 200 characters of what came
back, rather than being decoded into garbage bytes that fail later as an
unreadable PNG.

### Reference photos are downscaled

Four untouched phone photos become several megabytes of base64 in the request
body. Some gateways reject that outright and all of them are slower for it. The
references only need to carry identity — breed, fur colour, markings, ear and
snout shape — and that survives `reference_image_max_px`, 1024 by default.

The `codex` provider passes file paths instead, so this does not apply there.

### Compatibility with older configuration

A `config.json` written before providers existed keeps working untouched. With
no `provider` block, `generator.codex_model`, `generator.codex_reasoning_effort`
and `generator.generation_timeout_seconds` select codex with the same model and
the same effort as before. Nobody has to edit a working installation.

---

## Codex call and model parameters

`codex_cli.py` builds the process invocation (`codex_cli.py:28-57`):

```text
codex -a never exec --skip-git-repo-check --ephemeral --color never
  -C <temporary-workdir>
  -m gpt-5.6-luna
  -o <last-message-file>
  -c model_reasoning_effort="max"
  -i <reference-image> ...
  -
```

With reference images the prompt goes in over stdin. Without images it would be
passed as the last argument. This matters because `codex exec -i ...` works
reliably with stdin in this flow (`codex_cli.py:18-70`).

For the image, `generate_image_file()` wraps an orchestration prompt around the
actual image prompt (`codex_cli.py:124-150`):

```text
Use the built-in image generation tool to create exactly one image.
Use all attached reference images to preserve the dog's identity.
After generation, copy the selected generated image into this absolute path:
{output_path}

Final response rules:
- Print only the absolute path to the copied file.
- Do not print commentary.

Image request:
{prompt}
```

`codex exec` has to return that absolute output path exactly. Any other path, or
a missing file, raises `CodexCliError` (`codex_cli.py:152-157`).


## The Home Assistant integration has its own

`foredogs.generate_dog_picture` is the older path that generates inside Home
Assistant, without the Mac. It takes the same idea as three service fields:

| Field | Default |
|---|---|
| `base_url` | `FOREDOGS_OPENAI_BASE_URL`, else `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `text_model` | `gemini-2.5-flash` |
| `image_model` | `gemini-3-pro-image` |
| `gemini_api_key` | required; pass it as `!secret`, never inline |

`resolve_base_url()` implements that order (`foredogs.py:58`). The name
`gemini_api_key` is historical — it is the key for whatever endpoint `base_url`
points at.

So a local Ollama, a LiteLLM gateway or OpenAI itself all work from Home
Assistant too:

```yaml
action: foredogs.generate_dog_picture
data:
  base_url: http://10.0.0.5:11434/v1
  text_model: llama3.2
  image_model: llama3.2-vision
  gemini_api_key: !secret local_gateway_key
```

If you run the Mac generator, you do not need this service at all. See
[generator.md](generator.md#legacy-generator-in-the-home-assistant-integration).

## Which one to pick

**Use `codex`** if you already pay for a Codex-capable subscription. There is no
API key to manage and no per-image billing; the cost is a daily slice of a quota
you already have. The catch is that it is a CLI subprocess, so a quota refusal
arrives as a non-zero exit at 04:30 and the day has no new picture until the
quota resets. `daily_run.sh` exits non-zero in that case rather than publishing
something stale.

**Use `openai`** if you want a plain metered API, a local model, or anything
with an OpenAI-compatible endpoint. One picture is one text call plus one image
call per day.

Both are configured in the same place and can be swapped by editing
`provider.kind` — nothing else in the pipeline changes.
