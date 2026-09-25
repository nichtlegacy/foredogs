#!/usr/bin/env python3
"""Draw the landing page's social preview and raster icons.

    python3 tools/build_og.py

Writes site/og.jpg (1200x630), site/icon-32.png, site/apple-touch-icon.png,
site/favicon.ico, the web manifest's icons and the integration's brand icons. The preview shows the real
summer frame from site/screens/, so run tools/build_screens.py first. The type
is Geist, the page's own font, unpacked from the woff2 the page serves; that
needs fontTools and brotli, which only this script uses.

Every icon is rendered from design/icon/foredogs.svg, the one source of the
mark, with rsvg-convert (brew install librsvg, apt install librsvg2-bin).
Copy that file to site/favicon.svg after changing it.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
MARK = ROOT / "design" / "icon" / "foredogs.svg"
BRAND = ROOT / "custom_components" / "foredogs" / "brand"

BG = (10, 10, 12)
INK = (244, 244, 246)
INK_2 = (180, 180, 190)
INK_4 = (88, 88, 98)
SUN = (255, 230, 0)
FRAME = (239, 239, 236)
REST = (195, 203, 202)


def geist(size: int, weight: int, mono: bool = False) -> ImageFont.FreeTypeFont:
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        sys.exit("build_og.py needs fontTools and brotli: python3 -m pip install fonttools brotli")
    name = "GeistMono-Variable.woff2" if mono else "Geist-Variable.woff2"
    font = TTFont(SITE / "fonts" / name)
    font.flavor = None
    buffer = io.BytesIO()
    font.save(buffer)
    buffer.seek(0)
    face = ImageFont.truetype(buffer, size)
    face.set_variation_by_axes([weight])
    return face


def device(width: int, screen: Image.Image) -> Image.Image:
    """The reTerminal front at the enclosure's proportions, 176 x 120 mm."""
    mm = width / 176
    height = round(120 * mm)
    scale = 3  # draw large, then shrink, for clean edges
    big_mm = mm * scale
    body = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(body)
    draw.rounded_rectangle(
        [0, 0, body.width - 1, body.height - 1], radius=round(1.1 * big_mm), fill=FRAME,
        outline=(170, 170, 168), width=scale,
    )
    # Light from above, fading down the frame.
    shade = Image.new("L", body.size, 0)
    ImageDraw.Draw(shade).rectangle([0, body.height // 2, body.width, body.height], fill=18)
    shade = shade.filter(ImageFilter.GaussianBlur(body.height // 3))
    body.paste((0, 0, 0, 255), mask=shade.point(lambda v: v // 2))
    draw = ImageDraw.Draw(body)

    wx, wy = round(6.75 * big_mm), round(6 * big_mm)
    ww, wh = round(162.5 * big_mm), round(98 * big_mm)
    draw.rectangle([wx - scale, wy - scale, wx + ww + scale, wy + wh + scale], fill=(154, 154, 158))
    draw.rectangle([wx, wy, wx + ww, wy + wh], fill=REST)
    px, py = wx + round(1.25 * big_mm), wy + round(1 * big_mm)
    pw, ph = round(160 * big_mm), round(96 * big_mm)
    body.paste(screen.convert("RGB").resize((pw, ph), Image.Resampling.LANCZOS), (px, py))

    # The buttons on the top edge are hidden at this angle; the front is the
    # recognisable part.
    return body.resize((width, height), Image.Resampling.LANCZOS)


def og() -> None:
    canvas = Image.new("RGB", (1200, 630), BG)

    glow = Image.new("RGB", canvas.size, BG)
    gd = ImageDraw.Draw(glow)
    gd.ellipse([700, -120, 1260, 420], fill=(64, 58, 6))
    gd.ellipse([-220, -260, 360, 200], fill=(18, 24, 60))
    canvas = Image.blend(canvas, glow.filter(ImageFilter.GaussianBlur(150)), 1.0)

    screen = Image.open(SITE / "screens" / "summer-en-p1.png")
    panel = device(600, screen)
    shadow = Image.new("RGBA", (panel.width + 160, panel.height + 160), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [80, 110, 80 + panel.width, 80 + panel.height + 10], radius=12, fill=(0, 0, 0, 210)
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(34))
    x, y = 560, (630 - panel.height) // 2 + 6
    canvas.paste(shadow, (x - 80, y - 80), shadow)
    canvas.paste(panel, (x, y), panel)

    draw = ImageDraw.Draw(canvas)
    left = 72
    logo = icon(40)
    canvas.paste(logo, (left, 146), logo)
    draw.text((left + 54, 150), "foredogs", font=geist(28, 620), fill=INK_2)
    headline = geist(66, 620)
    draw.text((left, 208), "Your dog,", font=headline, fill=INK)
    draw.text((left, 282), "drawn every", font=headline, fill=SUN)
    draw.text((left, 356), "morning.", font=headline, fill=SUN)
    body = geist(21, 430)
    draw.text((left, 452), "In today's weather, a new style each day,", font=body, fill=INK_2)
    draw.text((left, 480), "on a colour e-paper panel.", font=body, fill=INK_2)
    draw.line([(left, 530), (left + 390, 530)], fill=(40, 40, 46), width=1)
    draw.text((left, 546), "HOME ASSISTANT · ESPHOME · OPEN SOURCE", font=geist(13, 500, mono=True), fill=INK_4)

    # JPEG, not PNG: WhatsApp drops a preview image over about 300 KB.
    canvas.save(SITE / "og.jpg", "JPEG", quality=90, optimize=True, progressive=True, subsampling=0)


def icon(size: int) -> Image.Image:
    """The mark at size x size px, transparent outside the rounded tile."""
    if not shutil.which("rsvg-convert"):
        sys.exit("build_og.py needs rsvg-convert: brew install librsvg")
    png = subprocess.run(
        ["rsvg-convert", "-w", str(size), "-h", str(size), str(MARK)],
        check=True, capture_output=True,
    ).stdout
    return Image.open(io.BytesIO(png)).convert("RGBA")


def icons() -> None:
    icon(32).save(SITE / "icon-32.png", "PNG", optimize=True)
    # iOS rounds the touch icon itself and shows transparency as black, so
    # fill the corners with the tile colour: its mask then cuts only dark.
    touch = Image.new("RGBA", (180, 180), BG + (255,))
    touch.alpha_composite(icon(180))
    touch.convert("RGB").save(SITE / "apple-touch-icon.png", "PNG", optimize=True)
    # Each size rendered on its own, not scaled down from the largest.
    icon(48).save(SITE / "favicon.ico", sizes=[(48, 48), (32, 32), (16, 16)],
                  append_images=[icon(32), icon(16)])
    # site.webmanifest: the tile as is, plus a maskable one for Android,
    # whose mask may cut down to a circle of 80% of the width. Shrunk so the
    # frame stays inside that circle, on the tile's own colour.
    icon(192).save(SITE / "icon-192.png", "PNG", optimize=True)
    icon(512).save(SITE / "icon-512.png", "PNG", optimize=True)
    maskable = Image.new("RGBA", (512, 512), BG + (255,))
    inner = icon(392)
    maskable.alpha_composite(inner, (60, 60))
    maskable.convert("RGB").save(SITE / "icon-maskable-512.png", "PNG", optimize=True)
    # Home Assistant 2026.3+ reads these from the integration itself.
    BRAND.mkdir(exist_ok=True)
    icon(256).save(BRAND / "icon.png", "PNG", optimize=True)
    icon(512).save(BRAND / "icon@2x.png", "PNG", optimize=True)


if __name__ == "__main__":
    og()
    icons()
    print("wrote og.jpg, favicons, manifest icons, brand/icon*.png")
