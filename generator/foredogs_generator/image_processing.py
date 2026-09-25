"""Image processing utilities for foredogs."""

from __future__ import annotations

import logging

from PIL import Image, ImageOps

logger = logging.getLogger("foredogs_generator")


def resize_image(image: Image.Image, final_size: str) -> Image.Image:
    """Crop then resize image to specified width and height."""
    if "x" not in final_size:
        logger.warning("Could not parse image size %s, returning original image", final_size)
        return image

    width, height = map(int, final_size.split("x"))
    return ImageOps.fit(
        image,
        size=(width, height),
        method=Image.Resampling.LANCZOS,
        centering=(0.0, 0.5),
    )


def recolor_image(image: Image.Image, profile: str | None) -> Image.Image:
    """Recolor image based on display profile."""
    if not profile or profile not in DISPLAY_PROFILES:
        logger.warning("Display profile %s not found, returning original image", profile)
        return image

    if image.mode != "RGB":
        image = image.convert("RGB")

    color_map = DISPLAY_PROFILES[profile]["color_map"]
    device_palette: list[int] = []
    true_palette: list[int] = []

    for color, mapped_color in color_map.items():
        true_palette.extend(_hex_to_rgb(mapped_color))
        device_palette.extend(_hex_to_rgb(color))

    true_palette.extend([0] * (256 * 3 - len(true_palette)))
    device_palette.extend([0] * (256 * 3 - len(device_palette)))

    palette_image = Image.new("P", (1, 1))
    palette_image.putpalette(true_palette)
    quantized = image.quantize(palette=palette_image, dither=Image.Dither.FLOYDSTEINBERG)
    quantized.putpalette(device_palette)
    return quantized.convert("RGB")


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    step = len(hex_color) // 3
    return tuple(int(hex_color[index : index + step], 16) for index in range(0, len(hex_color), step))


DISPLAY_PROFILES = {
    "spectra6": {
        "color_map": {
            "#000000": "#252A2D",
            "#FFFFFF": "#F5F5F5",
            "#0000FF": "#3068C5",
            "#00FF00": "#1A7A2A",
            "#FF0000": "#C52025",
            "#FFFF00": "#F5E855",
        },
    },
}

