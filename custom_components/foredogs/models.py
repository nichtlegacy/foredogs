from pydantic import BaseModel


class GenerateRequest(BaseModel):
    """Request model for generating dog pictures."""

    gemini_api_key: str
    # OpenAI-compatible endpoint. None falls back to FOREDOGS_OPENAI_BASE_URL
    # and then to Google's public endpoint, so a self-hosted proxy is a service
    # call parameter rather than an edit to the source.
    base_url: str | None = None
    text_model: str | None = None
    image_model: str | None = None

    location: str
    forecast: dict
    dog_names: list[str]
    dog_descriptions: list[str]
    input_image_paths: list[str]
    art_styles: list[str] = []  # Optional - uses built-in styles if empty
    person_names: list[str] = []
    person_descriptions: list[str] = []
    person_image_paths: list[str] = []
    person_inclusion_probability: float = 0.4

    image_gen_aspect_ratio: str
    image_gen_resolution: str
    final_image_size: str

    display_profile: str | None
