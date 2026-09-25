"""End-to-end generation pipeline."""

from __future__ import annotations

import json
import logging
import random
import shutil
import subprocess
import textwrap
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from .celebrations import (
    ActiveCelebration,
    active_celebrations,
    load_events as load_celebration_events,
    metadata as celebration_metadata,
    primary_outfit,
    record_history as record_celebration_history,
)
from .celebrations import activity_prompt_block as celebration_activity_block
from .celebrations import image_prompt_block as celebration_image_block
from .providers import ProviderError, get_provider
from .config import AppConfig
from .history import (
    StyleUse,
    format_style_history,
    load_history,
    parse_style_history,
    sanitize_prompt_history,
    sanitize_style_history,
    save_history,
    seed_history_if_missing,
)
from .image_processing import recolor_image, resize_image
from .styles import (
    WEATHER_MOODS,
    choose_style,
    get_art_style_string,
)
from .weather import fetch_daily_forecast

logger = logging.getLogger("foredogs_generator")

# Matches the image-generation retry count: the failure mode is the same
# intermittent hang, and the prompt is deterministic between attempts.
ACTIVITY_MAX_ATTEMPTS = 3
PROMPT_HISTORY_LIMIT = 20
# Style selection ranks by date rather than by position, so this is only a cap
# on file growth. It has to stay well above the pool size, otherwise a style
# would lose its last-used date and read as never used.
STYLE_HISTORY_LIMIT = 200


@dataclass(slots=True)
class GenerationResult:
    """Final output paths and prompt artifacts."""

    original_path: Path
    optimized_path: Path
    status_path: Path
    forecast: dict
    style_entry: dict
    activity: str
    activity_prompt_path: Path | None = None
    image_prompt_path: Path | None = None


@dataclass(slots=True)
class GenerationPlan:
    """Prepared prompt/state bundle ready for image rendering."""

    config: AppConfig
    reference_images: list[Path]
    original_path: Path
    optimized_path: Path
    archive_dir: Path
    status_path: Path
    forecast: dict
    style_entry: dict
    activity: str
    image_prompt: str
    celebrations: list[ActiveCelebration] = field(default_factory=list)
    activity_prompt_path: Path | None = None
    image_prompt_path: Path | None = None


def _build_activity_prompt(
    *,
    config: AppConfig,
    forecast: dict,
    prompt_history: list[str],
    style_entry: dict,
    celebrations: list[ActiveCelebration],
) -> str:
    style_name = style_entry["style"]
    outfit = style_entry.get("outfit")
    props = style_entry.get("props")
    universe = style_entry.get("universe")

    # Public holidays, regional events and private dates all arrive
    # through the same block, already sorted so the most important one leads.
    holiday_context = _indent_block(celebration_activity_block(celebrations))

    if outfit:
        style_context = textwrap.dedent(
            f"""
            Art style: {style_name}
            Outfit requirement: {outfit}
            The activity must match that outfit naturally.
            """
        ).strip()
    else:
        style_context = f"Art style: {style_name}"

    if props:
        # Listed separately from the outfit so the model treats a go-kart or a
        # trailing companion as part of the scene, not as something worn.
        style_context += textwrap.dedent(
            f"""

            Required props in the scene: {props}
            These belong to the scene or are carried, not worn.
            """
        ).rstrip() + "\n"

    if universe:
        style_context += textwrap.dedent(
            f"""
            IMPORTANT - Setting/Universe:
            The scene MUST take place in: {universe}
            Do NOT set the scene in {config.location} or other real-world locations.
            The activity and background should be authentic to this universe.
            """
        )

    # A style may carry its own extra direction, for looks whose surface
    # aesthetic is easy to imitate badly — "neon everywhere" instead of a place
    # that happens to have neon in it. This used to be a hard-coded branch for
    # one style; as a field it works for any of them, and ships with none.
    tone = (style_entry.get("tone") or "").strip()
    if tone:
        style_context += textwrap.dedent(
            f"""
            IMPORTANT - Tone for this style:
            {tone}
            """
        )

    return textwrap.dedent(
        f"""
        You are a prompt generator for static AI cartoon art generation model. Your task is to generate an activity for {len(config.dog_names)} dogs to do based on the date and weather conditions provided, which will be used to draw a single picture.

        The date is {forecast.get('datetime', '')}.

        The weather forecast is for {config.location}.

        The forecast is:
        {json.dumps(forecast, ensure_ascii=False, indent=2)}
        {style_context}
        {holiday_context}

        The last 20 activities you generated were:
        {prompt_history[-PROMPT_HISTORY_LIMIT:]}

        ************************************************

        Follow this prompt:

        Generate a fun activity for {len(config.dog_names)} dogs to do together that fits the weather conditions, time of year, AND the art style/outfit above.

        Heuristics:
        - You can anthropomorphize the dogs to do human-like activities, or you can make them do more dog-like activities occasionally.
        - The activity can be either indoors or outdoors.
        - Activities should be 50% set in locations in {config.location}, 20% set in other specific locations with similar weather, and 30% set in generic locations.
        - The mix of indoor/outdoor should be seasonally appropriate. Summer is mostly outdoor, winter is 50/50 indoor/outdoor.
        - It can be a mundane activity or something more exciting.
        - If a themed outfit is specified, the activity MUST match that theme.

        Rules:
        - You must come up with the following:
            - Activity: A short (<5 words) description of the activity
            - Foreground: A description of what the dogs are doing. Do NOT describe clothing.
            - Background: A description of the background elements.
        - You don't have to describe the weather.
        - Do not describe the general appearance of the dogs.
        - Do not describe clothing or accessories in the Foreground.
        - Mention a specific local real-world place name such as {config.location} at most once in the full output line.
        - The activity should involve all {len(config.dog_names)} dogs.
        - The activity must not be similar to any of the last 20 activities you generated.
        - Respond in a single line, no more than 100 words.
        - Do not use newlines.
        - Respond with "Activity: <activity>, Foreground: <foreground>, Background: <background>"
        """
    ).strip()


def _build_image_prompt(
    *,
    config: AppConfig,
    forecast: dict,
    activity: str,
    art_style: str,
    render_as: str | None = None,
    simplified_identity: bool = False,
    celebrations: list[ActiveCelebration] | None = None,
    tone: str = "",
) -> str:
    condition = forecast.get("condition", "").lower().replace(" ", "").replace("-", "")
    mood_context = ""
    for key, value in WEATHER_MOODS.items():
        normalized = key.replace("-", "")
        if normalized in condition or condition in normalized:
            mood_context = f"Mood: {value[0]}. Lighting hint: {value[1]}."
            break

    if render_as:
        embodiment_line = f"Specifically, render the dogs as {render_as}."
    else:
        embodiment_line = (
            "Render the dogs the way the game, show, or movie behind this style would natively "
            "depict a dog — using its own construction, line work, shading, palette, and resolution."
        )

    # Some styles only exist through radical simplification, and for those the
    # usual identity wording is the thing that breaks them: asked for fur colour
    # and markings while holding four reference photos, the model keeps painting
    # individual hairs and the style never lands. Styles that need it opt in.
    if simplified_identity:
        identity_rule = (
            "- Carry the dogs' IDENTITY across ONLY via: overall silhouette, ear and snout shape, tail\n"
            "  carriage, and the coarse placement of their dark patches rendered as FLAT SHAPES. Do not\n"
            "  reproduce fur colour or markings in detail, and do not draw individual hairs, strands or\n"
            "  fur texture anywhere in the image. Style fidelity outranks likeness here: if keeping a\n"
            "  marking would force rendered fur, drop the marking."
        )
    else:
        identity_rule = (
            "- Carry the dogs' IDENTITY across only via: breed / silhouette cue, fur color and markings,\n"
            "  and personality. Use the reference images for those cues only."
        )

    style_embodiment = textwrap.dedent(
        f"""
        STYLE EMBODIMENT (very important):
        Do NOT paste a photo-realistic dog into a stylized scene. The dogs must be fully REDRAWN as
        characters created inside the world of "{art_style}", using that style's own character-design
        language — its linework, shapes, PROPORTIONS, anatomy, face and eye design, shading, palette,
        and resolution. A realistic or default-3D dog is wrong unless this style is itself photoreal.
        {identity_rule}
        - Do NOT keep realistic proportions or realistic fur when the style is stylized. Let the
          style's drawing rules reshape the dog (flat 2D cartoon shapes, anime cel-shading, blocky
          voxels, paper cutout, comic ink, etc.).
        {embodiment_line}
        If this style is a 2D cartoon, anime, comic, or illustration, the dogs must be hand-DRAWN as
        characters in that exact look — same proportions, line weight, and face/eye style as that
        show/film/comic draws its own characters — never a realistic dog standing in a stylized set.
        The dogs should still read as the same individual dogs, but as native characters of this style.
        """
    ).strip()

    if simplified_identity:
        reference_rule = (
            "- Use the input images only for silhouette, ear and snout shape, and the rough placement "
            "of dark patches. Do NOT copy fur detail or texture from them."
        )
        likeness_rule = (
            "- The dogs should read as the same individual dogs at a glance, from shape alone, while "
            "being fully absorbed into the art style's medium. Where likeness and style conflict, the "
            "style wins."
        )
    else:
        reference_rule = (
            "- Use the input images as references for the dogs' identity (breed, fur color, markings, ear/snout shape)."
        )
        likeness_rule = (
            "- THE DOG(S) MUST STAY RECOGNIZABLE AS THE REFERENCE DOGS, but rendered fully in the art "
            "style's medium (see STYLE EMBODIMENT above) — not as photo-realistic dogs."
        )

    style_specific_rules = f"- {tone}" if tone else ""

    celebrations = celebrations or []
    celebration_block = _indent_block(celebration_image_block(celebrations))
    # Seasons only tint the scene; a greeting is what needs written words.
    celebration_text_wanted = any(c.kind != "season" for c in celebrations)

    # The painted-in weather caption is optional. The e-ink dashboard renders
    # weather itself, in flat palette colours that survive dithering, so leaving
    # it out of the artwork avoids showing the same numbers twice — once crisp,
    # once smudged. Kept configurable for anyone using the raw image on its own.
    if config.include_weather_box:
        weather_box_block = textwrap.dedent(
            f"""
            Additionally, create a small box in the bottom left corner, in the style of the image. This box should contain TEXT IN GERMAN:
            - A < three word description of the weather conditions
            - The daily high temperature: "Höchst: {forecast.get('temperature')}°C"
            - The daily low temperature: "Tiefst: {forecast.get('templow')}°C"
            """
        ).strip()
        weather_box_rule = (
            "- Do not place the temperature box too close to the edge, "
            "or overlapping any important details."
        )
        weather_box_style_rule = "- Style the temperature box to fit the overall image and art style."
    else:
        weather_box_block = ""
        # Stated as an explicit prohibition: without it the model tends to add a
        # caption anyway, because "daily weather illustration" implies one.
        weather_box_rule = (
            "- Do NOT draw any text, caption box, temperature readout, date, or "
            "weather label anywhere in the image. The scene must communicate the "
            "weather purely through visuals — the surrounding dashboard supplies "
            "the numbers. Incidental text that belongs to the world (a shop sign, "
            "a book cover) is fine."
        )
        # A celebration greeting is the one exception. Without carving it out
        # here the two instructions contradict each other, and the flat ban wins
        # — which would silently drop the birthday message the whole feature
        # exists to show.
        if celebration_text_wanted:
            weather_box_rule += (
                " EXCEPTION: the celebration text named under CELEBRATION below "
                "is required, and is the only text allowed."
            )
        weather_box_style_rule = ""

    return textwrap.dedent(
        f"""
        You are an AI artist creating daily weather illustrations featuring dogs based on a weather forecast and an activity that will be given to you. Your task is to generate a vibrant and engaging illustration that captures the essence of the weather conditions and the dogs' activity in a specific art style.

        The weather forecast is for {config.location} on {forecast.get('datetime', '')}:
        {json.dumps(forecast, ensure_ascii=False, indent=2)}
        {mood_context or 'Mood: weather-appropriate natural atmosphere.'}

        You have {len(config.dog_names)} dogs to illustrate:
        {", ".join(config.dog_names)}.

        Here are their descriptions:
        {chr(10).join("- " + description for description in config.dog_descriptions)}

        The activity they are doing today is:
        {activity}

        The art style should be:
        {art_style}

        {style_embodiment}

        You will be given reference photos.
        Use these reference images to lock the dogs' identity, then re-render them fully in the art style above.

        ***********************************************

        Create a vibrant and engaging illustration in the recommended style that captures the essence of the weather conditions and the dogs' activity. Use colors and elements that reflect the forecasted weather, making the scene lively and appropriate for the time of year.
        {weather_box_block}
        {celebration_block}
        Heuristics:
        - Try to capture the mood of the weather and activity in the illustration.
        - Try to capture the dogs personalities, but it is okay if they change based on the weather and activity.
        - This is for a color e-ink screen. Avoid very fine details that may be lost in dithering.

        Rules:
        - Only generate a single image.
        - The final image will be cropped in postprocessing to {config.final_image_size}, so compose the image accordingly and DON'T place anything near the edges.
        - Use a cinematic wide composition in {config.image_gen_aspect_ratio}.
        - Target generation resolution: {config.image_gen_resolution}.
        - The weather is important, so include elements that clearly indicate the weather conditions.
        {weather_box_rule}
        {weather_box_style_rule}
        {reference_rule}
        - Style the dogs to fit the activity and weather conditions.
        {likeness_rule}
        {style_specific_rules}
        """
    ).strip()


def _indent_block(text: str, spaces: int = 8) -> str:
    """Align an injected block with the f-string it is dropped into.

    The prompt templates are indented source, dedented at the end. A block whose
    lines start at column zero destroys that common prefix, so dedent silently
    does nothing and the entire prompt keeps its source indentation. Only lines
    after the first need padding: the placeholder itself already sits at the
    right column.
    """
    lines = text.split("\n")
    return lines[0] + "".join("\n" + (" " * spaces + line if line else "") for line in lines[1:])


def _forecast_date(forecast: dict) -> date:
    """Date the picture is for, which is not always today.

    The generation runs at 05:30 for the current day, but a manual run with a
    forecast override can target another one. Falls back to today rather than
    raising: a malformed timestamp should cost the celebration, not the image.
    """
    raw = str(forecast.get("datetime", ""))
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        logger.warning("Unparsable forecast datetime %r; using today", raw)
        return date.today()


def _write_status(status_path: Path, payload: dict) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def prepare_generation(
    config: AppConfig,
    project_dir: Path,
    forecast_override: dict | None = None,
) -> GenerationPlan:
    """Build prompts, update history, and return a render-ready plan."""
    random.seed()

    state_dir = config.resolve_path(project_dir, config.state_dir)
    output_dir = config.resolve_path(project_dir, config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_history_path = state_dir / "foredogs_prompt_history.txt"
    style_history_path = state_dir / "foredogs_style_history.txt"
    status_path = output_dir / "foredogs_generation_status.json"
    activity_prompt_path = output_dir / "latest_activity_prompt.txt"
    image_prompt_path = output_dir / "latest_image_prompt.txt"

    seed_history_if_missing(prompt_history_path, config.resolve_path(project_dir, config.seed_prompt_history_path), sanitize_prompt_history)
    seed_history_if_missing(style_history_path, config.resolve_path(project_dir, config.seed_style_history_path), sanitize_style_history)

    prompt_history = sanitize_prompt_history(load_history(prompt_history_path))
    style_history = parse_style_history(load_history(style_history_path))

    forecast = forecast_override or fetch_daily_forecast(config.location, config.weather)

    # Public holidays, regional events and private dates. The history lives in
    # state_dir so a recurring event can see what it did in previous years.
    celebrations = active_celebrations(
        load_celebration_events(project_dir / "config"),
        _forecast_date(forecast),
        state_dir,
    )
    if celebrations:
        logger.info(
            "Celebration(s) today: %s",
            ", ".join(f"{c.name} (prio {c.priority})" for c in celebrations),
        )

    style_entry = choose_style(
        style_history,
        style_pool=config.art_style_entries,
        holiday_outfit=primary_outfit(celebrations),
    )
    art_style = get_art_style_string(style_entry)
    logger.info("Selected art style: %s", art_style)

    _write_status(
        status_path,
        {
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "forecast": forecast,
            "style": style_entry,
            "celebrations": celebration_metadata(celebrations),
        },
    )

    activity_prompt = _build_activity_prompt(
        config=config,
        forecast=forecast,
        prompt_history=prompt_history,
        style_entry=style_entry,
        celebrations=celebrations,
    )
    activity_prompt_path.write_text(activity_prompt + "\n")
    activity = _generate_activity_with_retry(config, activity_prompt)
    logger.info("Generated activity: %s", activity)

    prompt_history.append(activity)
    save_history(prompt_history_path, prompt_history[-PROMPT_HISTORY_LIMIT:])
    # Dated with the forecast day, not with today, so a manual run for another
    # date ages the style for the day it actually illustrates.
    style_history.append(StyleUse(style=style_entry["style"], used_on=_forecast_date(forecast)))
    save_history(style_history_path, format_style_history(style_history[-STYLE_HISTORY_LIMIT:]))

    # Recorded once the activity exists, so next year's prompt can be told what
    # this birthday already did and pick something else.
    record_celebration_history(
        state_dir,
        celebrations,
        datetime.combine(_forecast_date(forecast), datetime.min.time()),
        activity,
        style_entry["style"],
    )

    image_prompt = _build_image_prompt(
        config=config,
        forecast=forecast,
        activity=activity,
        art_style=art_style,
        render_as=style_entry.get("render_as"),
        tone=style_entry.get("tone") or "",
        simplified_identity=style_entry.get("identity") == "simplified",
        celebrations=celebrations,
    )
    image_prompt_path.write_text(image_prompt + "\n")

    reference_images = config.resolved_image_paths(project_dir)
    if not reference_images:
        raise RuntimeError("No dog reference images configured.")
    for image_path in reference_images:
        if not image_path.exists():
            raise FileNotFoundError(f"Missing configured image: {image_path}")

    original_path = output_dir / "foredogs_original.png"
    optimized_path = output_dir / "foredogs_optimized.png"
    archive_dir = config.resolve_path(project_dir, config.archive_dir) or (output_dir / "archive")

    return GenerationPlan(
        config=config,
        reference_images=reference_images,
        original_path=original_path,
        optimized_path=optimized_path,
        archive_dir=archive_dir,
        status_path=status_path,
        forecast=forecast,
        style_entry=style_entry,
        activity=activity,
        image_prompt=image_prompt,
        celebrations=celebrations,
        activity_prompt_path=activity_prompt_path,
        image_prompt_path=image_prompt_path,
    )


def _generate_activity_with_retry(config: AppConfig, activity_prompt: str) -> str:
    """Ask Codex for the activity line, retrying the way image generation does.

    The text call has hung twice with no output and no error, burning the whole
    timeout before raising. Since planning happens before anything else, a
    single hang took the entire run with it — including, in a batch, every other
    item that had not started yet. The prompt is unchanged between attempts:
    these are transport hangs, not bad requests.
    """
    last_error: Exception | None = None

    for attempt in range(1, ACTIVITY_MAX_ATTEMPTS + 1):
        try:
            return get_provider(config.provider).generate_text(activity_prompt).strip()
        except (ProviderError, subprocess.TimeoutExpired) as error:
            last_error = error
            logger.warning(
                "Activity generation attempt %s/%s failed: %s",
                attempt,
                ACTIVITY_MAX_ATTEMPTS,
                error,
            )

    raise RuntimeError(f"Activity generation failed after {ACTIVITY_MAX_ATTEMPTS} attempts.") from last_error


def archive_original(
    original_path: Path,
    archive_dir: Path,
    keep_days: int = 0,
    when: date | None = None,
) -> Path | None:
    """Keep a dated copy of the generated original, and prune old ones.

    The working files are overwritten on every run, Home Assistant prunes its
    own copies after a few days, and Immich can be unreachable for weeks. When
    all three coincide the pictures are simply gone — sixteen days were lost
    exactly that way. This is the copy held by the machine that made them.

    Returns the archived path, or None when there was nothing to copy.
    """
    if not original_path.exists():
        logger.warning("Nothing to archive: %s does not exist", original_path)
        return None

    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = (when or date.today()).isoformat()
    target = archive_dir / f"{stamp}{original_path.suffix}"
    shutil.copy2(original_path, target)
    logger.info("Archived %s", target)

    if keep_days > 0:
        cutoff = (when or date.today()) - timedelta(days=keep_days)
        for candidate in sorted(archive_dir.glob(f"*{original_path.suffix}")):
            try:
                taken = date.fromisoformat(candidate.stem)
            except ValueError:
                # Not one of ours. Leave anything hand-placed alone.
                continue
            if taken < cutoff:
                candidate.unlink()
                logger.info("Pruned %s", candidate.name)

    return target


def render_generation_plan(plan: GenerationPlan, dry_run: bool = False) -> GenerationResult:
    """Render or finalize a prepared generation plan."""
    if dry_run:
        _write_status(
            plan.status_path,
            {
                "status": "dry_run",
                "celebrations": celebration_metadata(plan.celebrations),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "forecast": plan.forecast,
                "style": plan.style_entry,
                "activity": plan.activity,
                "activity_prompt_path": str(plan.activity_prompt_path) if plan.activity_prompt_path else None,
                "image_prompt_path": str(plan.image_prompt_path) if plan.image_prompt_path else None,
            },
        )
        return GenerationResult(
            original_path=plan.original_path,
            optimized_path=plan.optimized_path,
            status_path=plan.status_path,
            forecast=plan.forecast,
            style_entry=plan.style_entry,
            activity=plan.activity,
            activity_prompt_path=plan.activity_prompt_path,
            image_prompt_path=plan.image_prompt_path,
        )

    attempts: list[str] = []
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            get_provider(plan.config.provider).generate_image(
                prompt=plan.image_prompt,
                reference_images=plan.reference_images,
                output_path=plan.original_path,
            )
            break
        except (ProviderError, subprocess.TimeoutExpired) as error:
            last_error = error
            attempts.append(str(error))
            logger.warning("Image generation attempt %s failed: %s", attempt, error)
    else:
        _write_status(
            plan.status_path,
            {
                "status": "failed",
                "celebrations": celebration_metadata(plan.celebrations),
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "forecast": plan.forecast,
                "style": plan.style_entry,
                "activity": plan.activity,
                "errors": attempts,
            },
        )
        raise RuntimeError("Image generation failed after 3 attempts.") from last_error

    image = Image.open(plan.original_path)
    resized_image = resize_image(image.copy(), plan.config.final_image_size)
    optimized_image = recolor_image(resized_image, plan.config.display_profile)
    optimized_image.save(plan.optimized_path)

    # Archive before publishing: a failed upload should not also cost the only
    # local copy.
    archived_path = archive_original(
        plan.original_path,
        plan.archive_dir,
        plan.config.archive_keep_days,
    )

    _write_status(
        plan.status_path,
        {
            "status": "ok",
            "celebrations": celebration_metadata(plan.celebrations),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "forecast": plan.forecast,
            "style": plan.style_entry,
            "activity": plan.activity,
            "original_path": str(plan.original_path),
            "optimized_path": str(plan.optimized_path),
            "archived_path": str(archived_path) if archived_path else None,
        },
    )

    return GenerationResult(
        original_path=plan.original_path,
        optimized_path=plan.optimized_path,
        status_path=plan.status_path,
        forecast=plan.forecast,
        style_entry=plan.style_entry,
        activity=plan.activity,
        activity_prompt_path=plan.activity_prompt_path,
        image_prompt_path=plan.image_prompt_path,
    )


def run_generation(
    config: AppConfig,
    project_dir: Path,
    dry_run: bool = False,
    forecast_override: dict | None = None,
) -> GenerationResult:
    """Run full local generation pipeline."""
    plan = prepare_generation(
        config,
        project_dir,
        forecast_override=forecast_override,
    )
    return render_generation_plan(plan, dry_run=dry_run)
