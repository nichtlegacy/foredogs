# Art styles and the calendar

Two things decide what today's picture looks like beyond the weather: the art
style, drawn from a rotating pool, and whatever the calendar says today is.

Both live in files you own. Neither ships with the repository, because a style
pool is a matter of taste and a calendar is a matter of privacy — see
[What is deliberately not shipped](#what-is-deliberately-not-shipped).

## The style pool

`paths.art_styles_file` points at a JSON file shaped like this:

```json
{
  "art_styles": [
    {
      "style": "watercolour painting with soft edges",
      "outfit": null,
      "universe": null,
      "render_as": "watercolour-painted subjects with soft bleeding edges, translucent washes and visible paper texture"
    }
  ]
}
```

| Key | Required | What it does |
|---|---|---|
| `style` | yes | The visible style description. Also the identity used by the rotation history, so keep it stable once used. |
| `render_as` | recommended | The concrete rendering language for the dogs. This is what stops "watercolour" from producing a photographic dog on a watercolour background. |
| `outfit` | no | Themed clothing, phrased into the prompt as `dress the dog in ...`. |
| `props` | no | Scene objects, phrased as `stage the scene with ...`. Kept separate from `outfit` because "dress the dog in sitting in a go-kart" is not a sentence. |
| `universe` | no | A mandatory setting or narrative space. |
| `tone` | no | Extra direction for this style, added to both prompts. For looks whose surface aesthetic is easy to imitate badly — "neon everywhere" instead of a place that happens to have neon in it. |
| `identity` | no | Free-text note carried with the entry; not used by the prompt builder. |

`get_art_style_string()` joins `style`, `outfit` and `props` with ` - `
(`styles.py:59-77`).

### How a style gets picked

`choose_style()` ranks the pool by the date each style was last used, then picks
at random from the stalest quarter — at least `MIN_LRU_CANDIDATES` entries
(`styles.py:94-133`).

Ranking by *date* rather than by position in the history matters: the earlier
rule blocked whatever appeared in the last N entries, which counted runs, not
days. Five test runs in one afternoon aged the list by five slots and a style
could return a fortnight later. With dates, spacing is independent of how often
the generator runs, and reserving the pick for the stalest quarter guarantees a
gap of roughly three quarters of the pool — about 56 days with a pool of 75.

Styles never used sort first, so a newly added style appears within days.

### Style plus holiday

A celebration can contribute its own outfit. `merge_outfits()` combines the two
rather than replacing (`styles.py:80-92`): replacing lost the costume a style was
built around, leaving a superhero style as a dog in nothing but a Santa hat.


## Special days

Public holidays, regional events, seasons and private dates all run through
`generator/foredogs_generator/celebrations.py`.

> **Changed:** the calendar used to be a flat `HOLIDAYS` dict in `styles.py`,
> while the more capable `celebrations.py` hung off the dead Home Assistant path
> and never ran. The module was ported into the macOS pipeline, and `HOLIDAYS`,
> `get_holiday_info()` and `get_variable_holidays()` were removed from
> `styles.py`. Only `get_easter_date()` remains there, because the
> Easter-relative rules build on it.

### Date rules

| Form | Fields | Example |
|---|---|---|
| Fixed date | `month` + `day` | Christmas Eve, 24 December |
| Date with year | `date: '15.06.2021'` | A birthday, counts years |
| Nth weekday | `rule: nth_weekday`, `month`, `weekday`, `nth` | Mother's Day = second Sunday in May |
| Easter-relative | `rule: easter_offset`, `offset` | Ascension = Easter + 39 |
| Advent | `rule: advent`, `nth` | First to fourth Advent |
| Every N years | `every_n_years`, `anchor_year` | A triennial festival |
| Multi-day | `duration_days` | A town festival, a Christmas market |

`nth` may be negative and then counts from the end (`-1` = last).

### Built-in calendar

`DEFAULT_EVENTS` in `celebrations.py` holds 32 entries and works without any
configuration file at all: 23 German public holidays and culturally large days
without holiday status (Christmas Eve, New Year's Eve, St Nicholas, Mother's
Day), 5 dog and animal days, and the four season starts.

`priority` decides collisions. On 31 October, Halloween (75) therefore beats
Reformation Day (40) — deliberately, because it makes the better picture. The
winner also supplies the `outfit` for style selection.

Town festivals, markets and fairs are intentionally *not* built in, because they
differ per town. `examples/generator/celebrations.regional-events.example.yaml`
shows how to add them, and each of its three entries demonstrates a different
scheduling rule.

### Private dates

`config/celebrations.yaml` extends the built-in calendar and is untracked, so
real names and dates stay out of git. Start from
`examples/generator/celebrations.example.yaml`.

An entry reusing an existing `id` **replaces** the default, so the same file can
also retune or disable one (`enabled: false`).

Birthdays count the years themselves when the date carries a year:

```yaml
events:
  - id: dog_birthday
    name: Rex
    kind: birthday
    date: '15.06.2021'
    priority: 95
    outfit: party hat
    prompt: Rex's birthday: cake with candles, presents, balloons.
```

Result in 2026: `Alles Gute zum 5. Geburtstag, Rex`.

### Checking the calendar

```bash
python3 -m foredogs_generator.main --list-celebrations --year 2027
```

This walks a whole year day by day through the same function the generator uses,
so whatever it prints really does fire.

### History

`state/celebrations_history.json` remembers, per event, what was generated in
the last five years and hands that to the prompt. Without it the same birthday
produces the same cake scene every year.


## Writing your own styles

Start from `examples/generator/art_styles.example.json`, which holds a small
generic pool, and grow it. A few things learned the hard way on a six-colour
panel:

- **Name the medium, not just the mood.** "Cosy" produces a photograph with warm
  lighting. "Gouache on textured paper" produces gouache.
- **Say what the dogs are made of.** Without `render_as`, a style describes the
  background and leaves a realistic dog pasted on top. The phrasing that works is
  a positive plus a negative: *"...flat cel colouring and halftone shading, like
  a printed comic panel. Ink and print, never a photographic dog on a comic
  background."*
- **Avoid smooth gradients and fine repeating texture.** Both break into speckle
  when dithered to six colours. Large readable shapes with a strong value
  difference between dog and background survive the panel.
- **Keep `style` stable.** It is the key the rotation history stores. Editing the
  text makes the entry look brand new and it can repeat immediately.

Check what the pool produces without spending anything. `--dry-run` runs the
whole weather, style and prompt pipeline and stops before the image call, so you
see the picked style and the finished prompt for free:

```sh
cd generator
python3 -m foredogs_generator.main --dry-run
cat output/latest_image_prompt.txt
```

## What is deliberately not shipped

`config/art_styles.json`, `config/dogs.json` and `config/celebrations.yaml` are
untracked, and the repository carries examples instead:

| Your file | Example to copy | Why it is not shipped |
|---|---|---|
| `config/dogs.json` | `examples/generator/dogs.example.json` | Names your animals and points at photographs of them. |
| `config/celebrations.yaml` | `examples/generator/celebrations.example.yaml` | Birthdays and anniversaries of real people. |
| `config/art_styles.json` | `examples/generator/art_styles.example.json` | A live pool tends to accumulate named franchises and characters. What you generate privately for a kitchen panel is your business; a list of them in a public repository is a different thing, both for you and for whoever owns those franchises. |

The examples are deliberately plain. They are meant to be replaced, not used.

`examples/generator/celebrations.regional-events.example.yaml` shows the same
file used for local festivals rather than personal dates — a reasonable starting
point if you want the panel to know about your town without it knowing about
your family.
