import logging

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)

from .dashboard_service import render_kitchen_dashboard
from .foredogs import generate_dog_pic
from .languages import DEFAULT_LANGUAGE, available_languages
from .models import GenerateRequest

DOMAIN = "foredogs"
_LOGGER = logging.getLogger(__name__)

# Each waste entry is {entity_id, label, kind}; kind picks the block colour
# (rest/bio/papier/gelb) in dashboard_render.BIN_COLORS.
WASTE_SENSOR_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("label"): cv.string,
        vol.Optional("kind", default="rest"): cv.string,
    },
)

# For bins that only exist as calendar events; `match` filters by summary text.
WASTE_CALENDAR_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("label"): cv.string,
        vol.Optional("kind", default="bio"): cv.string,
        vol.Optional("match", default=""): cv.string,
    },
)

# Page 3's photo source. Immich has no per-album API keys — a key carries
# permission scopes but no object scope — so the album is pinned by id here.
# `share_key` is the genuinely album-restricted alternative: Immich binds a
# shared link to exactly one album server-side.
PHOTO_SCHEMA = vol.Schema(
    {
        vol.Optional("url", default=""): cv.string,
        vol.Optional("api_key", default=""): cv.string,
        vol.Optional("share_key", default=""): cv.string,
        vol.Optional("album_id", default=""): cv.string,
        vol.Optional("album_name", default=""): cv.string,
        # Used when Immich is unreachable, or on its own without any Immich
        # config at all.
        vol.Optional("fallback_folder", default=""): cv.string,
        vol.Optional("rotate_hours", default=3): vol.All(vol.Coerce(int), vol.Range(min=1)),
    },
)

DASHBOARD_SCHEMA = vol.Schema(
    {
        vol.Required("weather_entity"): cv.entity_id,
        vol.Optional("inside_temp_entity"): cv.entity_id,
        vol.Optional("inside_humidity_entity"): cv.entity_id,
        vol.Optional("battery_entity"): cv.entity_id,
        vol.Optional("waste_sensors", default=[]): [WASTE_SENSOR_SCHEMA],
        vol.Optional("waste_calendars", default=[]): [WASTE_CALENDAR_SCHEMA],
        vol.Optional("source_image", default="foredogs_original.png"): cv.string,
        # Folder of generated images, relative to www/daily_foredogs. Preferred
        # over source_image: the picker resolves today's date first and falls
        # back through weather pools to the newest image, so a day when the
        # generator did not run still shows a picture.
        vol.Optional("image_dir"): cv.string,
        # Delete dated images older than this. 0 keeps everything.
        vol.Optional("keep_days", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("dither_photo", default=True): cv.boolean,
        # Multi-page: 1 = home, 2 = weather detail, 3 = photo. The device cycles
        # them with its buttons (left back, right forward) and every second wake;
        # the middle button sleeps immediately.
        vol.Optional("page", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=3)),
        vol.Optional("page_count", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=3)),
        vol.Optional("photo", default={}): PHOTO_SCHEMA,
        # Language of the drawn labels. An unknown code logs a warning and
        # falls back to the default rather than failing the render.
        vol.Optional("language", default=DEFAULT_LANGUAGE): vol.In(available_languages()),
    },
)

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("gemini_api_key"): cv.string,
        # Any OpenAI-compatible endpoint: a local proxy, LiteLLM, OpenRouter,
        # Ollama, or OpenAI itself. Omitted, it falls back to
        # FOREDOGS_OPENAI_BASE_URL and then to Google's public endpoint.
        vol.Optional("base_url"): cv.string,
        vol.Optional("text_model"): cv.string,
        vol.Optional("image_model"): cv.string,
        vol.Required("location"): cv.string,
        vol.Required("forecast"): dict,
        vol.Required("dog_names"): [cv.string],
        vol.Required("dog_descriptions"): [cv.string],
        vol.Required("input_image_paths"): [cv.string],
        vol.Optional("art_styles", default=[]): [cv.string],
        vol.Optional("person_names", default=[]): [cv.string],
        vol.Optional("person_descriptions", default=[]): [cv.string],
        vol.Optional("person_image_paths", default=[]): [cv.string],
        vol.Optional(
            "person_inclusion_probability",
            default=0.4,
        ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
        vol.Required("image_gen_aspect_ratio"): cv.string,
        vol.Required("image_gen_resolution"): cv.string,
        vol.Required("final_image_size"): cv.string,
        vol.Optional("display_profile"): cv.string,
    },
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the dog generator integration."""

    async def handle_generate(call: ServiceCall) -> None:
        data = GenerateRequest(
            **call.data,
        )  # look I know I validate twice but I cant be effed to refactor
        _LOGGER.info(f"Received generate_dog_picture service call with data: {data}")
        try:
            # Run in executor thread, pass HA config directory
            original_path, optimized_path = await hass.async_add_executor_job(
                generate_dog_pic,
                data,
                hass.config.path(),
            )

            _LOGGER.info(f"Generated dog pictures: {original_path}, {optimized_path}")

        except Exception:
            _LOGGER.exception("Failed to generate dog picture")

    hass.services.async_register(DOMAIN, "generate_dog_picture", handle_generate, SERVICE_SCHEMA)

    async def handle_render_dashboard(call: ServiceCall) -> ServiceResponse:
        """Render the requested page into dashboard.png.

        Free to call — no Gemini request — so it can run every 30 minutes while
        the daily image itself is only regenerated once a day. Page 3 does fetch
        one photo from Immich per rotation slot, which is cheap and cached.
        """
        try:
            _, current, changed = await render_kitchen_dashboard(
                hass,
                weather_entity=call.data["weather_entity"],
                inside_temp_entity=call.data.get("inside_temp_entity"),
                inside_humidity_entity=call.data.get("inside_humidity_entity"),
                battery_entity=call.data.get("battery_entity"),
                waste_sensors=call.data.get("waste_sensors", []),
                waste_calendars=call.data.get("waste_calendars", []),
                source_image=call.data.get("source_image"),
                image_dir=call.data.get("image_dir"),
                keep_days=call.data.get("keep_days", 0),
                dither_photo=call.data.get("dither_photo", True),
                page=call.data.get("page", 1),
                page_count=call.data.get("page_count", 1),
                photo=call.data.get("photo") or {},
                language=call.data.get("language"),
            )
        except Exception:
            _LOGGER.exception("Failed to render dashboard")
            return {"changed": False, "fingerprint": "", "error": True}

        # The device reads `changed` to decide whether a 20-second panel
        # refresh is worth the battery.
        return {"changed": changed, "fingerprint": current, "error": False}

    hass.services.async_register(
        DOMAIN,
        "render_dashboard",
        handle_render_dashboard,
        DASHBOARD_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    return True
