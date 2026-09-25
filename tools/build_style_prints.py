#!/usr/bin/env python3
"""Turn a folder of generated style pictures into the landing page's prints.

The style index on the page shows one picture per example style when you
point at it. Those pictures are generated once, by the real generator, with
the example style pool and a made-up morning per style; this script only
crops and compresses them.

    python3 tools/build_style_prints.py /path/to/run

The folder needs one `<key>.png` per style (the keys the page uses: watercolour,
comic, risograph, woodcut, childrens, stainedglass, collage, poster, chalk,
voxel, botanical, enamel). Output goes to site/pictures/styles/.

Check every picture by eye before committing: no lettering, no real place
names, nobody but the dog.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "pictures" / "styles"
KEYS = [
    "watercolour", "comic", "risograph", "woodcut", "childrens", "stainedglass",
    "collage", "poster", "chalk", "voxel", "botanical", "enamel",
]
# The print is 304 CSS px wide inside a 320 px card; 600 covers a 2x screen.
SIZE = (600, 338)


def cover(image: Image.Image, width: int, height: int) -> Image.Image:
    scale = max(width / image.width, height / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    source = Path(sys.argv[1])
    missing = [key for key in KEYS if not (source / f"{key}.png").exists()]
    if missing:
        raise SystemExit(f"missing in {source}: {', '.join(missing)}")

    OUT.mkdir(parents=True, exist_ok=True)
    for key in KEYS:
        image = Image.open(source / f"{key}.png").convert("RGB")
        target = OUT / f"{key}.webp"
        # A fresh image carries no metadata, so nothing about where or when it
        # was generated reaches the page.
        cover(image, *SIZE).save(target, "WEBP", quality=74, method=6)
        print(f"{target.name}: {target.stat().st_size / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
