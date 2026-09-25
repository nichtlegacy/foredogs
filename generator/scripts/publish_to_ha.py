#!/usr/bin/env python3
"""Upload the most recently generated image to Home Assistant.

Reads its target from `config.json` (a `publish` block) with environment
overrides, so a token never has to live in a file that is committed:

    publish.ssh_host      ssh alias or user@host, default "homeassistant"
    publish.remote_dir    default /config/www/daily_foredogs/dogs
    publish.ha_url        for the post-upload render nudge
    publish.render_script default script.foredogs_render_dashboard

    FOREDOGS_HA_TOKEN     long-lived token; overrides publish.ha_token

Uploads the *original* rather than the dithered copy: Home Assistant crops and
dithers itself, and doing it twice visibly degrades the picture.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from foredogs_generator.immich import ImmichError, ImmichTarget, archive  # noqa: E402
from foredogs_generator.publish import PublishError, PublishTarget, publish  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("publish_to_ha")


def load_immich() -> ImmichTarget | None:
    """Immich archive target, or None when not configured.

    The API key comes from the environment rather than config.json so the file
    stays safe to commit.
    """
    config = json.loads((ROOT / "config.json").read_text())
    cfg = config.get("immich", {})

    url = cfg.get("url", "")
    key = os.environ.get("FOREDOGS_IMMICH_KEY", cfg.get("api_key", ""))
    if not (url and key):
        logger.debug("Immich not configured; skipping archive")
        return None

    return ImmichTarget(
        url=url,
        api_key=key,
        album_id=cfg.get("album_id", ""),
        album_name=cfg.get("album_name", "Foredogs Archive"),
    )


def load_target() -> tuple[PublishTarget, Path]:
    config = json.loads((ROOT / "config.json").read_text())
    publish_cfg = config.get("publish", {})
    generator = config.get("generator", {})

    output_dir = Path(generator.get("output_dir", "./output"))
    if not output_dir.is_absolute():
        output_dir = (ROOT / output_dir).resolve()

    target = PublishTarget(
        ssh_host=publish_cfg.get("ssh_host", "homeassistant"),
        remote_dir=publish_cfg.get("remote_dir", "/config/www/daily_foredogs/dogs"),
        condition=publish_cfg.get("condition", ""),
        ha_url=publish_cfg.get("ha_url", ""),
        # The token is expected from the environment; keeping it out of
        # config.json means the file stays safe to commit.
        ha_token=os.environ.get("FOREDOGS_HA_TOKEN", publish_cfg.get("ha_token", "")),
        render_script=publish_cfg.get("render_script", "script.foredogs_render_dashboard"),
    )
    return target, output_dir


def main() -> int:
    target, output_dir = load_target()

    # The original, not foredogs_optimized.png: HA does its own crop and dither,
    # and dithering an already-dithered image smears it.
    image = output_dir / "foredogs_original.png"
    if not image.is_file():
        logger.error("no generated image at %s", image)
        return 1

    logger.info("publishing %s (%.1f KB)", image.name, image.stat().st_size / 1024)

    try:
        result = publish(image, target, when=date.today())
    except PublishError:
        logger.exception("publish failed")
        return 2

    logger.info("remote path: %s", result["remote_path"])
    logger.info("render triggered: %s", result["render_triggered"])

    # Archive to Immich after the display has what it needs. Deliberately last
    # and non-fatal: the panel working matters more than the archive, and a
    # failed upload can be retried tomorrow (uploads are deduped, so re-sending
    # costs nothing).
    immich = load_immich()
    if immich is not None:
        try:
            archived = archive(immich, image)
            logger.info(
                "immich: asset %s (%s)",
                archived["asset_id"][:12],
                "uploaded" if archived["uploaded"] else "already present",
            )
        except (ImmichError, OSError):
            logger.warning("immich archive failed; will retry tomorrow", exc_info=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
