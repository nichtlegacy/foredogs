"""Publish a generated image to Home Assistant.

The Mac generates, Home Assistant renders the dashboard around the picture, and
the display only fetches the finished PNG. This module is the hand-off: it
copies the image into HA's web root under a dated filename and then asks HA to
re-render, so the new picture appears without waiting for the next half hour.

Transport is scp over the existing SSH access rather than an HTTP upload: HA has
no general file-upload API, and the image store (`/api/image/upload`) hands back
an opaque id rather than a stable path the renderer could pick up by date.

Naming matters. Images land as `YYYY-MM-DD.png` because the dashboard's image
picker resolves today's date first and falls back to the most recent one — so a
day when the Mac was off still shows a picture instead of a placeholder.
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

logger = logging.getLogger("foredogs_generator")

DEFAULT_REMOTE_DIR = "/config/www/daily_foredogs/dogs"


class PublishError(RuntimeError):
    """Raised when the image could not be delivered."""


@dataclass(slots=True)
class PublishTarget:
    """Where and how to deliver the image."""

    ssh_host: str = "homeassistant"
    remote_dir: str = DEFAULT_REMOTE_DIR
    # Optional weather-condition subfolder, so a stock of images can be built up
    # and the dashboard can pick one matching the actual weather on the day.
    condition: str = ""
    # Home Assistant URL and token, for triggering a re-render after upload.
    ha_url: str = ""
    ha_token: str = ""
    render_script: str = "script.foredogs_render_dashboard"
    ssh_options: tuple[str, ...] = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=10")


def _run(command: list[str], timeout: int = 120) -> str:
    logger.debug("running: %s", " ".join(command))
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        raise PublishError(f"timed out: {' '.join(command)}") from err

    if result.returncode != 0:
        raise PublishError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def publish_image(
    image_path: Path,
    target: PublishTarget,
    when: date | None = None,
    also_latest: bool = True,
) -> str:
    """Copy the image to Home Assistant and return the remote path written.

    Args:
        image_path: the dithered PNG to upload.
        target: destination and credentials.
        when: date to name the file after; defaults to today.
        also_latest: additionally write `latest.png`, handy for dashboards or
            debugging that want a fixed URL.
    """
    image_path = Path(image_path)
    if not image_path.is_file():
        raise PublishError(f"image does not exist: {image_path}")

    when = when or date.today()
    remote_dir = target.remote_dir.rstrip("/")
    if target.condition:
        remote_dir = f"{remote_dir}/{target.condition}"

    remote_name = f"{when.isoformat()}.png"
    remote_path = f"{remote_dir}/{remote_name}"

    # mkdir first: scp will not create intermediate directories, and the
    # per-condition subfolder usually will not exist yet.
    _run(
        ["ssh", *target.ssh_options, target.ssh_host, f"mkdir -p '{remote_dir}'"],
        timeout=30,
    )

    # Upload to a temp name and move into place, so a slow copy can never leave
    # a half-written file that the dashboard picks up mid-render.
    tmp_path = f"{remote_dir}/.{remote_name}.part"
    _run(["scp", *target.ssh_options, str(image_path), f"{target.ssh_host}:{tmp_path}"])
    _run(
        ["ssh", *target.ssh_options, target.ssh_host, f"mv '{tmp_path}' '{remote_path}'"],
        timeout=30,
    )
    logger.info("uploaded %s -> %s", image_path.name, remote_path)

    if also_latest:
        latest = f"{target.remote_dir.rstrip('/')}/latest.png"
        _run(
            ["ssh", *target.ssh_options, target.ssh_host, f"cp '{remote_path}' '{latest}'"],
            timeout=30,
        )

    return remote_path


def trigger_render(target: PublishTarget) -> bool:
    """Ask Home Assistant to re-render the dashboard now.

    Returns False rather than raising when HA is unreachable: the image is
    already uploaded, and the scheduled render will pick it up within half an
    hour regardless. A failed nudge is not a failed publish.
    """
    if not (target.ha_url and target.ha_token):
        logger.debug("no HA credentials configured; skipping render trigger")
        return False

    domain, _, service = target.render_script.partition(".")
    url = f"{target.ha_url.rstrip('/')}/api/services/{domain}/{service}"

    request = urllib.request.Request(
        url,
        data=json.dumps({}).encode(),
        headers={
            "Authorization": f"Bearer {target.ha_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            logger.info("render triggered (HTTP %s)", response.status)
            return True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
        logger.warning("could not trigger render: %s", err)
        return False


def publish(
    image_path: Path,
    target: PublishTarget,
    when: date | None = None,
) -> dict:
    """Upload the image and nudge Home Assistant. Returns a small summary."""
    remote_path = publish_image(image_path, target, when=when)
    rendered = trigger_render(target)
    return {
        "remote_path": remote_path,
        "render_triggered": rendered,
    }
