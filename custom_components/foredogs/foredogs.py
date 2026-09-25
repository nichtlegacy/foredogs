"""Generate dog pictures based on weather forecasts using OpenAI-compatible API."""

import base64
import io
import logging
import os
import random
import re
import textwrap
from datetime import date, datetime
from pathlib import Path
from typing import TypedDict

from openai import OpenAI
from PIL import Image

try:
    from .celebrations import (
        ActiveCelebration,
        active_celebrations,
        activity_prompt_block,
    )
    from .celebrations import image_prompt_block as celebration_image_block
    from .celebrations import load_events as load_celebration_events
    from .celebrations import record_history as record_celebration_history
    from .celebrations import resolve_people as resolve_celebration_people
    from .image_processing import recolor_image, resize_image
    from .models import GenerateRequest
except ImportError:  # For local testing
    from celebrations import (
        ActiveCelebration,
        active_celebrations,
        activity_prompt_block,
    )
    from celebrations import image_prompt_block as celebration_image_block
    from celebrations import load_events as load_celebration_events
    from celebrations import record_history as record_celebration_history
    from celebrations import resolve_people as resolve_celebration_people
    from image_processing import recolor_image, resize_image
    from models import GenerateRequest

_LOGGER = logging.getLogger(__name__)

# OpenAI-compatible API endpoint. Google's public endpoint is the default, so
# an AI Studio key works with no further configuration. A self-hosted proxy or
# any other OpenAI-compatible gateway is selected per service call via
# `base_url`, or for a whole installation via the environment variable.
#
# Setting an environment variable is awkward on Home Assistant OS, so the
# service parameter is the path that is actually reachable from an automation.
DEFAULT_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_TEXT_MODEL = "gemini-2.5-flash"
DEFAULT_IMAGE_MODEL = "gemini-3-pro-image"


def resolve_base_url(requested: str | None) -> str:
    """Pick the endpoint: service call, then environment, then the default."""
    return requested or os.environ.get("FOREDOGS_OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL

# Weather condition to mood mapping for image generation
WEATHER_MOODS = {
    "clear-night": ("peaceful, serene, starlit", "night sky with stars"),
    "cloudy": ("calm, muted, contemplative", "soft diffused lighting"),
    "exceptional": ("dramatic, intense, striking", "unusual atmospheric conditions"),
    "fog": ("mysterious, atmospheric, ethereal", "misty and dreamlike environment"),
    "hail": ("dramatic, intense, sheltered", "icy conditions"),
    "lightning": ("dramatic, electrifying, powerful", "stormy skies with lightning"),
    "lightning-rainy": ("dramatic, moody, cozy indoors", "thunder and rain outside"),
    "partlycloudy": ("pleasant, balanced, cheerful", "mix of sun and clouds"),
    "pouring": ("cozy, sheltered, rainy-day-vibes", "heavy rain outside"),
    "rainy": ("cozy, reflective, peaceful", "gentle rain"),
    "snowy": ("magical, cozy, winter-wonderland", "snow-covered landscape"),
    "snowy-rainy": ("chilly, cozy, wintry", "sleet and wintry mix"),
    "sunny": ("bright, cheerful, vibrant, happy", "warm sunlight"),
    "windy": ("dynamic, energetic, breezy", "wind-swept environment"),
    "windy-variant": ("lively, fresh, movement", "gusty conditions"),
}

# Holiday/Event definitions (month, day) -> (name, activity hints, outfit override)
HOLIDAYS = {
    (1, 1): ("New Year's Day", "celebrating new year, party, fireworks watching, champagne toast", None),
    (2, 14): ("Valentine's Day", "romantic dinner, giving flowers, heart-shaped treats", None),
    (3, 17): ("St. Patrick's Day", "wearing green, shamrock hunting, Irish celebration", "green hat and clover accessories"),
    (4, 1): ("April Fools", "playing pranks, silly jokes, mischief", None),
    (10, 3): ("German Unity Day", "flag waving, celebration, parade watching", "German flag scarf or bandana"),
    (10, 31): ("Halloween", "trick or treating, pumpkin carving, haunted house, costume party", "spooky costume"),
    (12, 6): ("Nikolaus", "putting out boots, receiving treats, meeting St. Nicholas", "red Santa hat"),
    (12, 24): ("Christmas Eve", "opening presents, decorating tree, family dinner, singing carols", "Christmas sweater and Santa hat"),
    (12, 25): ("Christmas Day", "unwrapping gifts, Christmas feast, playing with new toys", "cozy Christmas sweater"),
    (12, 26): ("Second Christmas Day", "family visit, leftover feast, relaxing", "festive winter outfit"),
    (12, 31): ("New Year's Eve", "countdown party, fireworks, champagne celebration", "party hat and bow tie"),
}

# Holidays with variable dates (calculated each year)
def get_easter_date(year: int) -> tuple[int, int]:
    """Calculate Easter Sunday using the Anonymous Gregorian algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return (month, day)


def get_variable_holidays(year: int) -> dict:
    """Get holidays with variable dates for a specific year."""
    from datetime import date, timedelta

    easter = date(year, *get_easter_date(year))

    return {
        # Carnival (Rose Monday is 48 days before Easter)
        (easter - timedelta(days=48)).month: {
            (easter - timedelta(days=48)).day: ("Rosenmontag", "carnival parade, costume party, throwing candy, celebrating", "colorful carnival costume with confetti")
        },
        # Good Friday
        (easter - timedelta(days=2)).month: {
            (easter - timedelta(days=2)).day: ("Good Friday", "quiet reflection, nature walk, peaceful activities", None)
        },
        # Easter Sunday
        easter.month: {
            easter.day: ("Easter Sunday", "Easter egg hunt, decorating eggs, Easter brunch, meeting Easter bunny", "bunny ears headband"),
            (easter + timedelta(days=1)).day: ("Easter Monday", "Easter egg hunt, spring picnic, family gathering", "spring flower accessories"),
        },
    }


def _forecast_date(date_str: str) -> date:
    """The date the forecast is about, falling back to today.

    HA hands over an ISO timestamp; celebrations are matched against a plain
    date so a Z-suffixed UTC string and a local one behave the same.
    """
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
    except (ValueError, TypeError, AttributeError):
        return date.today()


def get_holiday_info(date_str: str) -> tuple[str, str, str] | None:
    """Check if a date is a holiday and return (name, activity_hints, outfit_override)."""
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        month, day, year = dt.month, dt.day, dt.year

        # Check fixed holidays
        if (month, day) in HOLIDAYS:
            return HOLIDAYS[(month, day)]

        # Check variable holidays
        variable = get_variable_holidays(year)
        if month in variable and day in variable[month]:
            return variable[month][day]

    except (ValueError, TypeError):
        pass

    return None

# Fallback style pool, used when a service call passes no `art_styles`.
#
# Deliberately plain: each entry names a *medium* rather than a franchise, and
# says in `render_as` what the dogs themselves are made of — which is the
# difference between a watercolour scene and a photographic dog standing in
# front of one. Everything here is composed for a six-colour e-paper panel, so
# large readable shapes and strong value contrast, no smooth gradients and no
# fine repeating texture.
#
# This is a starting point, not a recommendation. Pass your own `art_styles`,
# or point the standalone generator at its own catalogue; see docs/styles.md.
ART_STYLES = [
    {"style": "watercolour painting with soft bleeding edges", "render_as": "watercolour-painted subjects with translucent washes, visible paper grain and soft edges where colours meet. Genuinely painted, never a photograph with a watercolour filter"},
    {"style": "comic book style with bold ink lines and flat colour", "render_as": "comic-book characters with confident black ink outlines, flat cel colouring and halftone dot shading, like a panel from a printed comic. Ink and print, never a photographic or 3D dog dropped onto a comic background"},
    {"style": "risograph print with limited spot colours and visible misregistration", "render_as": "subjects built from two or three overprinted spot-colour layers with slight misregistration and coarse paper texture. Flat ink on stock, no photographic depth"},
    {"style": "woodcut print with heavy carved lines", "render_as": "figures carved as bold relief shapes, black ink on cream paper, with directional gouge marks doing all the shading. Two tones only, no midtones"},
    {"style": "mid-century children's book illustration", "render_as": "simplified friendly figures in flat muted colour with slightly off-register printed texture, drawn with a soft crayon or litho edge rather than a clean vector line"},
    {"style": "stained glass window with lead came outlines", "render_as": "subjects assembled from flat panes of saturated coloured glass, every shape bounded by a thick dark lead line. Luminous and graphic, never modelled or shaded"},
    {"style": "paper cut-out collage with layered shapes", "render_as": "figures built from torn and cut paper in flat colours, layered with visible edges and small drop shadows between layers. Handmade collage, not a digital illustration imitating one"},
    {"style": "vintage travel poster with flat bold shapes", "render_as": "simplified geometric figures in four or five flat screen-printed colours, strong diagonal composition, no gradients. The look of a 1930s railway poster"},
    {"style": "chalk drawing on a dark board", "render_as": "figures drawn in coloured chalk on slate: soft dusty strokes, smudged edges, bright marks on a dark ground. Hand-drawn on a real board"},
    {"style": "blocky voxel art built from large cubes", "render_as": "creatures and scenery built from visibly chunky cubes with flat per-face shading, like a low-resolution voxel model. Cubic geometry throughout, never a smooth model with a blocky texture"},
    {"style": "botanical field guide plate with fine ink hatching", "render_as": "subjects drawn as a naturalist's plate: precise ink contours, restrained hatching for form, flat colour washes, plenty of cream paper around them"},
    {"style": "enamel pin design with thick metal borders", "props": "a plain background so the shapes read as a single object", "render_as": "figures reduced to a few bold enamel colour fields separated by raised metal outlines, as if the whole scene were one hard enamel pin. Flat colour, hard edges, no shading inside a field"},
]


def get_art_style_string(style_entry: dict) -> str:
    """Combine style and outfit into a single prompt string."""
    style = style_entry["style"]
    outfit = style_entry.get("outfit")
    if outfit:
        return f"{style} - dress the dog in {outfit}"
    return style


class SelectedPerson(TypedDict):
    """Selected companion person context for one generation run."""

    name: str
    description: str
    image_path: str


def build_person_pool(data: GenerateRequest) -> list[SelectedPerson]:
    """Build and validate optional people pool from request data."""
    names = data.person_names
    descriptions = data.person_descriptions
    image_paths = data.person_image_paths
    lengths = (len(names), len(descriptions), len(image_paths))

    if any(lengths) and len(set(lengths)) != 1:
        raise ValueError("person_names, person_descriptions, and person_image_paths must have the same length.")

    if data.person_inclusion_probability < 0.0 or data.person_inclusion_probability > 1.0:
        raise ValueError("person_inclusion_probability must be between 0.0 and 1.0.")

    return [
        {
            "name": name,
            "description": description,
            "image_path": image_path,
        }
        for name, description, image_path in zip(names, descriptions, image_paths)
    ]


def select_optional_person(
    people_pool: list[SelectedPerson],
    person_inclusion_probability: float,
) -> SelectedPerson | None:
    """Select a random companion person based on configured probability."""
    if not people_pool:
        return None

    roll = random.random()
    if roll >= person_inclusion_probability:
        _LOGGER.info(
            "No companion person selected (roll=%.3f, threshold=%.3f).",
            roll,
            person_inclusion_probability,
        )
        return None

    selected = random.choice(people_pool)
    _LOGGER.info(
        "Selected companion person '%s' (roll=%.3f, threshold=%.3f).",
        selected["name"],
        roll,
        person_inclusion_probability,
    )
    return selected


def generate_dog_pic(data: GenerateRequest, config_dir: str) -> tuple[str, str]:
    """Generate, crop, and dither a dog picture based on the current weather.

    Args:
        data: Pydantic model containing all generation parameters
        config_dir: Home Assistant configuration directory for saving data

    Returns:
        tuple[str, str]: Filenames of the saved PNG images of the generated dog pictures (original and recolored).

    """
    # Setup paths relative to HA config directory
    config_path = Path(config_dir)
    data_dir = config_path / "foredogs_data"
    static_dir = config_path / "www" / "daily_foredogs"

    data_dir.mkdir(parents=True, exist_ok=True)
    static_dir.mkdir(parents=True, exist_ok=True)

    prompt_history_filepath = data_dir / "foredogs_prompt_history.txt"
    style_history_filepath = data_dir / "foredogs_style_history.txt"

    # Load resources
    prompt_history = load_prompt_history(prompt_history_filepath)
    style_history = load_prompt_history(style_history_filepath)
    input_images = load_images_as_base64(data.input_image_paths)
    dog_reference_count = len(input_images)
    people_pool = build_person_pool(data)
    selected_person = select_optional_person(people_pool, data.person_inclusion_probability)
    if selected_person:
        person_images = load_images_as_base64([selected_person["image_path"]])
        if person_images:
            input_images.extend(person_images)
        else:
            _LOGGER.warning(
                "Selected person image not found for '%s' at path '%s'. Falling back to dog-only scene.",
                selected_person["name"],
                selected_person["image_path"],
            )
            selected_person = None

    # Check for holidays
    holiday_info = get_holiday_info(data.forecast.get("datetime", ""))

    # Private celebrations (birthdays, anniversaries) come from
    # foredogs_data/celebrations.yaml and can override who appears in the scene.
    forecast_day = _forecast_date(data.forecast.get("datetime", ""))
    celebration_events = load_celebration_events(data_dir)
    celebrations = active_celebrations(celebration_events, forecast_day, data_dir)

    if celebrations:
        _LOGGER.info(
            "Active celebrations: %s",
            ", ".join(f"{c.name} ({c.message})" for c in celebrations),
        )
        # A birthday should show the person whose birthday it is, so an explicit
        # request wins over the random draw made above.
        requested = resolve_celebration_people(celebrations, people_pool)
        if requested is not None:
            selected_person = requested[0] if requested else None
            if selected_person:
                person_images = load_images_as_base64([selected_person["image_path"]])
                if person_images:
                    # Replace any previously appended random person image.
                    input_images = input_images[:dog_reference_count]
                    input_images.extend(person_images)
                    _LOGGER.info(
                        "Celebration selected person: %s",
                        selected_person["name"],
                    )
                else:
                    _LOGGER.warning(
                        "Celebration person image missing for '%s' at '%s'",
                        selected_person["name"],
                        selected_person["image_path"],
                    )
                    selected_person = None
            else:
                input_images = input_images[:dog_reference_count]

    # Select art style - use built-in styles if none provided, otherwise use user's styles
    if data.art_styles and len(data.art_styles) > 0:
        # User provided custom styles as strings - wrap in dict format
        style_entry = {"style": random.choice(data.art_styles), "outfit": None}
    else:
        # Use built-in styles with outfits, avoiding recent styles
        available_styles = [s for s in ART_STYLES if s["style"] not in style_history]
        if not available_styles:
            # All styles used recently, reset
            available_styles = ART_STYLES
        style_entry = random.choice(available_styles)

    # Holiday can override outfit
    if holiday_info and holiday_info[2]:
        style_entry = {"style": style_entry["style"], "outfit": holiday_info[2]}

    art_style = get_art_style_string(style_entry)
    _LOGGER.info(f"Selected art style: {art_style}")

    # Update style history
    style_history.append(style_entry["style"])
    style_history = style_history[-10:]
    save_prompt_history(style_history_filepath, style_history)

    # Initialize OpenAI-compatible client
    client = OpenAI(
        base_url=resolve_base_url(data.base_url),
        api_key=data.gemini_api_key
    )

    # Generate activity description (now aware of the style/outfit and holidays)
    activity = generate_activity(
        client,
        data,
        prompt_history,
        style_entry,
        holiday_info,
        selected_person,
        celebrations,
    )

    _LOGGER.info(f"Generated activity: {activity}")

    # Update prompt history
    prompt_history.append(activity)
    prompt_history = prompt_history[-20:]
    save_prompt_history(prompt_history_filepath, prompt_history)

    # Remember what this celebration looked like, so next year's prompt can be
    # told to invent something else.
    if celebrations:
        record_celebration_history(
            data_dir,
            celebrations,
            datetime.now(),
            activity,
            style_entry["style"],
        )

    # Generate image with retry logic
    _LOGGER.info(f"Generating image for: {activity}")
    max_retries = 3
    image = None

    for attempt in range(max_retries):
        try:
            image = generate_image(
                client,
                data,
                activity,
                input_images,
                art_style,
                dog_reference_count,
                selected_person,
                style_entry.get("render_as"),
                celebrations,
                style_entry.get("tone") or "",
            )
            break
        except RuntimeError as e:
            _LOGGER.warning(f"Attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                # Try with a different style
                style_entry = random.choice(ART_STYLES)
                art_style = get_art_style_string(style_entry)
                _LOGGER.info(f"Retrying with new style: {art_style}")
            else:
                raise

    if image is None:
        raise RuntimeError("Failed to generate image after all retries")

    # Post-process image
    resized_image = resize_image(image.copy(), data.final_image_size)
    optimized_image = recolor_image(resized_image, data.display_profile)

    # Save images
    original_filepath = static_dir / "foredogs_original.png"
    optimized_filepath = static_dir / "foredogs_optimized.png"
    image.save(original_filepath)
    optimized_image.save(optimized_filepath)

    _LOGGER.info(f"Images saved to {static_dir}")

    return original_filepath, optimized_filepath


def load_prompt_history(filepath: Path) -> list[str]:
    """Load past prompts from file."""
    if filepath.exists():
        return filepath.read_text().splitlines()
    filepath.parent.mkdir(parents=True, exist_ok=True)
    return []


def save_prompt_history(filepath: Path, history: list[str]) -> None:
    """Save prompt history to file."""
    Path(filepath).write_text("\n".join(history))


def load_images_as_base64(image_paths: list[str], max_size: int = 1024) -> list[str]:
    """Load images and convert to base64 data URLs."""
    images = []

    for img_path in image_paths:
        path = Path(img_path)
        if not path.exists():
            _LOGGER.warning(f"Image not found: {img_path}")
            continue

        # Load and resize image
        img = Image.open(path)
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

        # Convert to base64
        buffer = io.BytesIO()
        img_format = "JPEG" if path.suffix.lower() in [".jpg", ".jpeg"] else "PNG"
        img.save(buffer, format=img_format)
        img_data = base64.b64encode(buffer.getvalue()).decode()

        mime_type = "image/jpeg" if img_format == "JPEG" else "image/png"
        images.append(f"data:{mime_type};base64,{img_data}")

    if not images:
        _LOGGER.warning(f"No valid images found from paths: {image_paths}")

    return images


def generate_activity(
    client: OpenAI,
    data: GenerateRequest,
    prompt_history: list[str],
    style_entry: dict,
    holiday_info: tuple[str, str, str] | None = None,
    selected_person: SelectedPerson | None = None,
    celebrations: list[ActiveCelebration] | None = None,
) -> str:
    """Describe an activity for the dogs based on the weather forecast, date, and art style."""
    # Build style context for the prompt
    style_name = style_entry["style"]
    outfit = style_entry.get("outfit")

    # Build holiday context
    if holiday_info:
        holiday_context = f"""
        🎉 SPECIAL DAY: Today is {holiday_info[0]}!
        The activity MUST be themed around this holiday!
        Suggested activities: {holiday_info[1]}
        """
    else:
        holiday_context = ""

    # Private celebrations sit alongside public holidays and carry their own
    # history, so the same birthday does not produce the same scene every year.
    celebration_context = activity_prompt_block(celebrations or [])

    if outfit:
        style_context = f"""
        IMPORTANT - Art Style Context:
        The image will be rendered in: {style_name}
        The dogs will be wearing: {outfit}

        You MUST choose an activity that FITS this themed outfit! Examples:
        - Military/tactical gear → paintball, airsoft, laser tag, survival training, stealth mission, camping
        - Samurai armor → dojo training, zen garden meditation, martial arts practice, tea ceremony
        - Wizard robes → potion brewing, magic library, enchanted forest exploration
        - Cowboy outfit → rodeo, horse riding, western saloon, gold prospecting
        - Astronaut suit → space station, moon exploration, rocket launch viewing
        - Sports gear → the matching sport activity
        - Holiday costumes → holiday-themed activities

        The activity should feel like a natural scene from the style's universe!
        Do NOT choose activities like "museum visit" or "cafe" when wearing tactical/fantasy gear.
        """
    else:
        style_context = f"""
        Art Style: {style_name}
        (No specific outfit - choose any appropriate activity for the weather and season)
        """

    # Add universe context if available
    universe = style_entry.get("universe")
    if universe:
        style_context += f"""
        IMPORTANT - Setting/Universe:
        The scene MUST take place in: {universe}
        Do NOT set the scene in {data.location} or other real-world locations!
        The activity and background should be authentic to this universe.
        """

    # A style may carry its own extra direction, for looks whose surface
    # aesthetic is easy to imitate badly — "neon everywhere" instead of a place
    # that happens to have neon in it. This used to be a hard-coded branch for
    # one style; as a field it works for any of them, and ships with none.
    tone = (style_entry.get("tone") or "").strip()
    if tone:
        style_context += f"""
        IMPORTANT - Tone for this style:
        {tone}
        """

    if selected_person:
        person_context = f"""
        Optional Person Companion:
        Include exactly one person in today's scene:
        - Name: {selected_person["name"]}
        - Description: {selected_person["description"]}
        This person should interact naturally with the dogs while the dogs remain the main focus.
        """
        participation_rule = f"- The activity should involve all {len(data.dog_names)} dogs and {selected_person['name']}."
    else:
        person_context = ""
        participation_rule = f"- The activity should involve all {len(data.dog_names)} dogs"

    activity_prompt = textwrap.dedent(
        f"""
        You are a prompt generator for static AI cartoon art generation model. Your task is to generate an activity for {len(data.dog_names)} dogs to do based on the date and weather conditions provided, which will be used to draw a single picture.

        The date is {data.forecast.get("datetime", "")}.

        The weather forecast is for {data.location}.

        The forecast is:
        {data.forecast}
        {style_context}
        {holiday_context}
        {celebration_context}
        {person_context}

        The last 20 activities you generated were:
        {prompt_history}

        ************************************************

        Follow this prompt:

        Generate a fun activity for {len(data.dog_names)} dogs to do together that fits the weather conditions, time of year, AND the art style/outfit above.

        Heuristics:
        - You can anthropomorphize the dogs to do human-like activities, or you can make them do more dog-like activities occasionally.
        - The activity can be either indoors or outdoors
        - Activities should be 50% set in locations in {data.location}, and 20% set in other specific locations with similar weather, and 30% set in generic locations.
        - The mix of indoor/outdoor should be seasonally appropriate. Summer is mostly outdoor, winter is 50/50 indoor/outdoor.
        - It can be a mundane activity (waiting for the bus, commuting, shopping, reading, etc.) or it can be exciting (playing in the snow, sports, going to a festival, playing tag, games, etc.).
        - If a themed outfit is specified, the activity MUST match that theme!

        Rules:
        - You must come up with the following:
            - Activity: A short (<5 words) description of the activity
            - Foreground: A description of what the dogs are doing. Do NOT describe clothing (the outfit is already defined by the style).
            - Background: A description of the background elements (e.g. buildings, landmarks, trees, furniture, etc.)
        - You don't have to describe the weather
        - Do not describe the general appearance of the dogs
        - Do not describe clothing or accessories in the Foreground (this is handled by the art style)
        {participation_rule}
        - The activity must not be similar to any of the last 20 activities you generated.
        - Respond in a single line, no more than 100 words
        - Do not use newlines
        - Respond with "Activity: <activity>, Foreground: <foreground>, Background: <background>"
        """,
    )

    response = client.chat.completions.create(
        model=data.text_model or DEFAULT_TEXT_MODEL,
        messages=[{"role": "user", "content": activity_prompt}]
    )

    if not response.choices[0].message.content:
        msg = "API returned no activity."
        raise RuntimeError(msg)

    return response.choices[0].message.content


def generate_image(
    client: OpenAI,
    data: GenerateRequest,
    activity: str,
    reference_images: list[str],
    art_style: str,
    dog_reference_count: int,
    selected_person: SelectedPerson | None = None,
    render_as: str | None = None,
    celebrations: list[ActiveCelebration] | None = None,
    tone: str = "",
) -> Image.Image:
    """Generate a cartoon image of dogs based on the weather forecast and activity."""
    # Celebration text is drawn *into* the artwork (a banner, cake, chalkboard)
    # rather than overlaid afterwards, so it belongs in the image prompt.
    celebration_context = celebration_image_block(celebrations or [])
    # Get weather mood
    condition = data.forecast.get("condition", "").lower().replace(" ", "").replace("-", "")
    # Try to match condition to mood, fallback to neutral
    mood_info = None
    for key, value in WEATHER_MOODS.items():
        if key.replace("-", "") in condition or condition in key.replace("-", ""):
            mood_info = value
            break

    if mood_info:
        mood_description, lighting_hint = mood_info
        mood_context = f"""
        MOOD & ATMOSPHERE:
        The overall mood should be: {mood_description}
        Lighting/Environment hint: {lighting_hint}
        Let these guide your color palette and atmosphere!
        """
    else:
        mood_context = ""

    if selected_person:
        person_reference_position = dog_reference_count + 1
        person_context = f"""
        The scene must include one companion person:
        - Name: {selected_person["name"]}
        - Description: {selected_person["description"]}
        Reference image mapping:
        - Images 1 to {dog_reference_count}: dog reference images
        - Image {person_reference_position}: {selected_person["name"]} reference image
        Use image {person_reference_position} as the identity anchor for {selected_person["name"]}.
        Keep these identity traits consistent in stylized form: face shape, glasses, hairline/hair style, and beard/mustache pattern.
        Do not invent strong facial features that are not visible in the reference (e.g. hat, mask, exaggerated facial hair changes).
        Keep the dogs as the visual focus of the scene.
        """
        person_rules = f"""
        - Include {selected_person["name"]} naturally in the same activity as the dogs.
        - Keep dogs central in composition and emphasis.
        - Prioritize identity fidelity for {selected_person["name"]}'s face over accessory/clothing variation.
        """
    else:
        person_context = ""
        person_rules = ""

    style_specific_rules = f"- {tone}" if tone else ""

    if render_as:
        embodiment_line = f"Specifically, render the dogs as {render_as}."
    else:
        embodiment_line = (
            "Render the dogs the way the game, show, or movie behind this style would natively "
            "depict a dog — using its own construction, line work, shading, palette, and resolution."
        )

    style_embodiment = f"""
        STYLE EMBODIMENT (very important):
        Do NOT paste a photo-realistic dog into a stylized scene. The dogs must look like they were
        created inside the world of "{art_style}", not photographed and dropped in.
        - Use the reference images to lock IDENTITY only: breed, body proportions, fur color and
          markings, ear and snout shape, eye color.
        - Then fully re-render those identity traits in the native medium of this style.
        {embodiment_line}
        The dogs must stay clearly recognizable as the same individual dogs, but rendered as
        true inhabitants of this style's medium.
        """

    image_generation_prompt = textwrap.dedent(
        f"""
        You are an AI artist creating daily weather illustrations featuring dogs based on a weather forecast and an activity that will be given to you. Your task is to generate a vibrant and engaging illustration that captures the essence of the weather conditions and the dogs' activity in a specific art style.

        The weather forecast is for {data.location} on {data.forecast.get("datetime", "")}:
        {data.forecast}
        {mood_context}

        You have {len(data.dog_names)} dogs to illustrate:
        {", ".join(data.dog_names)}.

        Here are their descriptions:
        {chr(10).join("- " + d for d in data.dog_descriptions)}

        The activity they are doing today is:
        {activity}

        The art style should be:
        {art_style}
        {style_embodiment}

        You will be given {len(reference_images)} input images with reference photos.
        Use these reference images to lock the dogs' identity, then re-render them fully in the art style above.
        {person_context}
        {celebration_context}

        ***********************************************

        Create a vibrant and engaging illustration in the recommended style that captures the essence of the weather conditions and the dogs' activity. Use colors and elements that reflect the forecasted weather, making the scene lively and appropriate for the time of year.

        Additionally, create a small box in the bottom left corner, in the style of the image. This box should contain TEXT IN GERMAN:
        - A < three word description of the weather conditions (IN GERMAN, e.g. "Bewölkt und Kalt")
        - The daily high temperature: "Höchst: {data.forecast.get("temperature")}°C"
        - The daily low temperature: "Tiefst: {data.forecast.get("templow")}°C"

        Heuristics:
        - Try to capture the mood of the weather and activity in the illustration (e.g., bright and sunny, cozy indoors during snow, etc.).
        - Try to capture the dogs personalities, but it is okay if they change based on the weather and activity.
        - This is for a color e-ink screen. I will handle the dithering later, but try to avoid very fine details that may be lost in dithering.

        Rules:
        - Only generate a single image.
        - The final image will be cropped in postprocessing to {data.final_image_size}, so compose the image accordingly and DON'T place anything near the edges.
        - The weather is important, so include elements that clearly indicate the weather conditions
        - Do not place the temperature box too close to the edge, or overlapping any important details.
        - Style the temperature box to fit the overall image and art style.
        - Use the input images as references for the dogs' identity (breed, fur color, markings, ear/snout shape).
        - Style the dogs to fit the activity and weather conditions.
        - THE DOG(S) MUST STAY RECOGNIZABLE AS THE REFERENCE DOGS, but rendered fully in the art style's medium (see STYLE EMBODIMENT above) — not as photo-realistic dogs.
        {person_rules}
        {style_specific_rules}
        """,
    )

    # Build message content with text and reference images
    content = [{"type": "text", "text": image_generation_prompt}]

    # Add reference images
    for img_url in reference_images:
        content.append({
            "type": "image_url",
            "image_url": {"url": img_url}
        })

    response = client.chat.completions.create(
        model=data.image_model or DEFAULT_IMAGE_MODEL,
        extra_body={"size": "1280x720"},
        messages=[{"role": "user", "content": content}]
    )

    # Parse the response to get the image
    response_content = response.choices[0].message.content

    if response_content is None:
        _LOGGER.error(f"API returned no image content for style: {art_style}")
        raise RuntimeError(f"API refused to generate image - possibly due to content filters with style: {art_style}")

    return parse_image_from_response(response_content, art_style)


def parse_image_from_response(content: str, art_style: str) -> Image.Image:
    """Parse image from API response content."""
    try:
        # Method 1: Markdown image format ![...](data:image/...;base64,...)
        if "![" in content and "data:image" in content:
            match = re.search(r'data:image/[^;]+;base64,([A-Za-z0-9+/=]+)', content)
            if match:
                base64_data = match.group(1)
                image_bytes = base64.b64decode(base64_data)
                return Image.open(io.BytesIO(image_bytes))

        # Method 2: Plain data URL
        if content.startswith("data:image"):
            base64_data = content.split(",")[1]
            image_bytes = base64.b64decode(base64_data)
            return Image.open(io.BytesIO(image_bytes))

        # Method 3: Raw base64 string
        clean_content = content.replace("\n", "").replace("\r", "").replace(" ", "")

        # Fix padding if needed
        padding = 4 - len(clean_content) % 4
        if padding != 4:
            clean_content += "=" * padding

        image_bytes = base64.b64decode(clean_content)
        return Image.open(io.BytesIO(image_bytes))

    except Exception as e:
        _LOGGER.error(f"Failed to parse image: {e}")
        _LOGGER.error(f"Response length: {len(content)} characters")
        _LOGGER.error(f"First 200 chars: {content[:200]}...")
        raise RuntimeError(f"Could not parse image from response: {e}")
