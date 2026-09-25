"""Archive generated images to an Immich album.

Home Assistant is a poor archive: its `www/` folder is on the same storage as the
database and backups, and a 1.7 MB PNG a day fills it for no benefit — the panel
only ever fetches today's picture. So Immich holds the history, the Mac keeps the
working copies, and Home Assistant keeps just enough to render.

Uploads are idempotent. Immich dedupes on a client-supplied SHA-1, so re-running
a day's publish does not create a second copy, and no local state has to be kept
to remember what was already sent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("foredogs_generator")


class ImmichError(RuntimeError):
    """Raised when Immich rejects a request."""


@dataclass(slots=True)
class ImmichTarget:
    """Where to archive, and what to call it."""

    url: str
    api_key: str
    album_id: str = ""
    # Created on demand when album_id is empty, so a fresh install needs no
    # manual setup in the Immich UI.
    album_name: str = "Foredogs Archive"
    device_id: str = "foredogs-generator"
    timeout: int = 120


def _curl(args: list[str], timeout: int) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise ImmichError(f"curl failed ({result.returncode}): {result.stderr.strip()[:300]}")
    return result.stdout


def _api(
    target: ImmichTarget,
    method: str,
    path: str,
    payload: dict | None = None,
) -> dict | list:
    """One JSON request. Uses curl rather than urllib so uploads and plain calls
    share the same transport, including TLS handling on this reverse proxy."""
    args = [
        "curl", "-s", "-m", str(target.timeout),
        "-X", method,
        "-H", f"x-api-key: {target.api_key}",
    ]
    if payload is not None:
        args += ["-H", "Content-Type: application/json", "-d", json.dumps(payload)]
    args.append(f"{target.url.rstrip('/')}{path}")

    body = _curl(args, target.timeout)
    if not body.strip():
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError as err:
        raise ImmichError(f"{path}: non-JSON reply: {body[:200]}") from err


def resolve_album(target: ImmichTarget) -> str:
    """Return the album id to upload into, creating the album if needed."""
    if target.album_id:
        return target.album_id

    albums = _api(target, "GET", "/api/albums")
    if isinstance(albums, list):
        for album in albums:
            if album.get("albumName") == target.album_name:
                logger.info("Immich album %r -> %s", target.album_name, album["id"])
                return str(album["id"])

    created = _api(target, "POST", "/api/albums", {"albumName": target.album_name})
    album_id = str(created.get("id", ""))
    if not album_id:
        raise ImmichError(f"could not create album {target.album_name!r}")
    logger.info("Created Immich album %r -> %s", target.album_name, album_id)
    return album_id


def already_uploaded(target: ImmichTarget, image: Path) -> str | None:
    """Return the existing asset id if Immich already has this exact file.

    Uses Immich's own dedupe endpoint rather than a local ledger, so the answer
    stays correct even if the Mac is restored from a backup or the state file is
    lost.
    """
    checksum = hashlib.sha1(image.read_bytes()).hexdigest()
    reply = _api(
        target,
        "POST",
        "/api/assets/bulk-upload-check",
        {"assets": [{"id": image.name, "checksum": checksum}]},
    )

    for result in reply.get("results", []) if isinstance(reply, dict) else []:
        if result.get("action") == "reject" and result.get("reason") == "duplicate":
            return str(result.get("assetId") or "") or "duplicate"
    return None


def upload(target: ImmichTarget, image: Path, taken: datetime | None = None) -> str:
    """Upload one image and return its asset id.

    `taken` sets the asset's creation date, which is what Immich sorts and groups
    by — without it every generated image would land under the upload time and
    the album would lose its day-by-day ordering.
    """
    image = Path(image)
    if not image.is_file():
        raise ImmichError(f"no such image: {image}")

    checksum = hashlib.sha1(image.read_bytes()).hexdigest()
    stamp = (taken or datetime.fromtimestamp(image.stat().st_mtime)).astimezone()

    args = [
        "curl", "-s", "-m", str(target.timeout),
        "-X", "POST",
        "-H", f"x-api-key: {target.api_key}",
        "-H", f"x-immich-checksum: {checksum}",
        "-F", f"deviceAssetId={image.name}-{int(stamp.timestamp())}",
        "-F", f"deviceId={target.device_id}",
        "-F", f"fileCreatedAt={stamp.isoformat()}",
        "-F", f"fileModifiedAt={stamp.isoformat()}",
        "-F", "isFavorite=false",
        "-F", f"assetData=@{image}",
        f"{target.url.rstrip('/')}/api/assets",
    ]

    body = _curl(args, target.timeout)
    try:
        reply = json.loads(body)
    except json.JSONDecodeError as err:
        raise ImmichError(f"upload: non-JSON reply: {body[:200]}") from err

    asset_id = str(reply.get("id", ""))
    if not asset_id:
        raise ImmichError(f"upload rejected: {body[:200]}")

    logger.info("Immich upload %s -> %s (%s)", image.name, asset_id[:12], reply.get("status"))
    return asset_id


def add_to_album(target: ImmichTarget, album_id: str, asset_id: str) -> bool:
    """Put an asset in the album. Already being a member is not an error."""
    reply = _api(
        target,
        "PUT",
        f"/api/albums/{album_id}/assets",
        {"ids": [asset_id]},
    )

    for result in reply if isinstance(reply, list) else []:
        if result.get("success"):
            return True
        if result.get("error") == "duplicate":
            logger.debug("asset %s already in album", asset_id[:12])
            return True
    return False


def archive(
    target: ImmichTarget,
    image: Path,
    taken: datetime | None = None,
) -> dict:
    """Upload the image and file it in the album. Safe to call repeatedly."""
    image = Path(image)

    existing = already_uploaded(target, image)
    if existing and existing != "duplicate":
        album_id = resolve_album(target)
        add_to_album(target, album_id, existing)
        return {"asset_id": existing, "uploaded": False, "album_id": album_id}

    asset_id = upload(target, image, taken=taken)
    album_id = resolve_album(target)
    in_album = add_to_album(target, album_id, asset_id)

    return {
        "asset_id": asset_id,
        "uploaded": True,
        "album_id": album_id,
        "in_album": in_album,
    }
