"""Photo source for dashboard page 3: an Immich album, or a local folder.

Immich has no per-album API keys — keys carry permission *scopes*
(`asset.read`, `album.read`, …) but no object scope, so a key that can read the
dog album can read every other album too. The album is pinned here instead, by
id, and that is the only album this module ever asks for.

Two ways in:

  api_key   a normal Immich API key with `album.read` + `asset.read`. The album
            id is required and nothing else is ever fetched.
  share_key the token from a shared link (`/share/<key>`). Immich binds a shared
            link to exactly one album server-side, so this is the option that is
            genuinely restricted to that album — a leaked share key cannot reach the
            rest of the library. Preferred when the account allows creating one.

The picked asset is downloaded once per render into `www/daily_foredogs/` as a
cache file and handed to the renderer as a path. Rotation is date-seeded rather
than random: every render inside the same slot picks the same photo, so the
device's fingerprint check still works and a 30-minute wake does not repaint the
panel just because the dice came up differently.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from .dashboard_render import PhotoInfo

logger = logging.getLogger("foredogs")

CACHE_NAME = "page3_photo.jpg"
# Immich's "preview" render is ~1440px on the long edge — far more than the
# 800x480 panel needs, and a fraction of the original's bytes.
IMMICH_SIZE = "preview"
TIMEOUT_S = 25
# Photos are cropped to 800x480, so anything portrait loses most of its subject.
# Landscape-ish only, unless the album has nothing else.
MIN_ASPECT = 1.1

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}


class ImmichError(RuntimeError):
    """Raised when Immich is unreachable or rejects the credentials."""


def _request(url: str, headers: dict[str, str]) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as response:
            return response.read()
    except urllib.error.HTTPError as err:
        # Immich answers permission problems with a JSON body that names the
        # missing scope, which is by far the most useful thing to surface.
        detail = ""
        try:
            detail = json.loads(err.read().decode()).get("message", "")
        except (ValueError, OSError):
            pass
        raise ImmichError(f"HTTP {err.code}{': ' + detail if detail else ''}") from err
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        raise ImmichError(str(err)) from err


def _album_assets(base_url: str, album_id: str, api_key: str) -> list[dict]:
    """Read one album's assets with a normal API key."""
    body = _request(
        f"{base_url}/api/albums/{album_id}",
        {"x-api-key": api_key, "Accept": "application/json"},
    )
    payload = json.loads(body)
    assets = payload.get("assets") or []
    if not assets and payload.get("assetCount"):
        # The album exists and reports a count, but the assets came back empty:
        # that is what an `album.read`-only key looks like, and the fix is to add
        # `asset.read` rather than anything in this code.
        raise ImmichError(
            f"album '{payload.get('albumName', album_id)}' reports "
            f"{payload['assetCount']} assets but returned none — the API key is "
            "probably missing the asset.read permission"
        )
    return assets


def _shared_link_assets(base_url: str, share_key: str) -> list[dict]:
    """Read the album behind a shared link. The link itself pins the album."""
    body = _request(
        f"{base_url}/api/shared-links/me?key={share_key}",
        {"Accept": "application/json"},
    )
    payload = json.loads(body)
    album = payload.get("album") or {}
    return album.get("assets") or payload.get("assets") or []


def _usable(asset: dict) -> bool:
    if asset.get("type") != "IMAGE":
        return False
    exif = asset.get("exifInfo") or {}
    width = exif.get("exifImageWidth")
    height = exif.get("exifImageHeight")
    if not width or not height:
        return True  # no EXIF is not a reason to skip a photo
    return (width / height) >= MIN_ASPECT


def _parse_taken(asset: dict) -> datetime | None:
    exif = asset.get("exifInfo") or {}
    raw = exif.get("dateTimeOriginal") or asset.get("fileCreatedAt") or ""
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _pick(assets: list[dict], seed: str) -> dict | None:
    """Deterministically pick one asset for this rotation slot.

    Seeded on the slot rather than random: two renders in the same slot must
    agree, otherwise the fingerprint changes on every wake and the device
    repaints for nothing.
    """
    usable = [a for a in assets if _usable(a)]
    if not usable:
        # Better a portrait photo than an empty page.
        usable = [a for a in assets if a.get("type") == "IMAGE"]
    if not usable:
        return None

    usable.sort(key=lambda a: str(a.get("id", "")))
    index = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(usable)
    return usable[index]


def _download_asset(
    base_url: str,
    asset_id: str,
    headers: dict[str, str],
    share_key: str,
    destination: Path,
) -> None:
    url = f"{base_url}/api/assets/{asset_id}/thumbnail?size={IMMICH_SIZE}"
    if share_key:
        url = f"{url}&key={share_key}"
    data = _request(url, headers)
    # Temp file plus rename: the previous cached photo stays valid until the new
    # one is complete, so a failed download cannot leave a truncated JPEG behind.
    tmp = destination.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(destination)


def from_immich(
    base_url: str,
    album_id: str,
    cache_dir: Path,
    api_key: str = "",
    share_key: str = "",
    album_name: str = "",
    slot: str = "",
) -> PhotoInfo:
    """Pick and cache one photo from the pinned Immich album.

    Args:
        base_url: e.g. https://immich.example.com
        album_id: the album to read; ignored when share_key is given, because a
            shared link already identifies exactly one album
        cache_dir: where to write the downloaded JPEG
        api_key: Immich API key with album.read + asset.read
        share_key: shared-link token, the album-restricted alternative
        album_name: shown in the caption; falls back to what Immich reports
        slot: rotation key, e.g. "2026-07-26-14". Same slot, same photo.

    Raises:
        ImmichError: unreachable, rejected, or the album has no usable image.
    """
    base_url = base_url.rstrip("/")
    if not (api_key or share_key):
        raise ImmichError("neither api_key nor share_key configured")

    if share_key:
        assets = _shared_link_assets(base_url, share_key)
        headers = {"Accept": "*/*"}
    else:
        if not album_id:
            raise ImmichError("album_id is required when using an api_key")
        assets = _album_assets(base_url, album_id, api_key)
        headers = {"x-api-key": api_key, "Accept": "*/*"}

    asset = _pick(assets, slot or datetime.now().strftime("%Y-%m-%d-%H"))
    if asset is None:
        raise ImmichError("album contains no usable image")

    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / CACHE_NAME
    _download_asset(base_url, str(asset["id"]), headers, share_key, destination)

    return PhotoInfo(
        path=destination,
        taken=_parse_taken(asset),
        source_kind="immich",
        source_name=album_name,
        asset_id=str(asset.get("id", "")),
    )


def from_folder(folder: Path, slot: str = "") -> PhotoInfo:
    """Pick one image from a local folder. The fallback when Immich is out.

    Same date-seeded rotation as Immich, so the fingerprint behaves identically.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"{folder} is not a directory")

    images = sorted(p for p in folder.iterdir() if p.suffix.lower() in _IMAGE_SUFFIXES)
    if not images:
        raise FileNotFoundError(f"no images in {folder}")

    seed = slot or datetime.now().strftime("%Y-%m-%d-%H")
    index = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(images)
    chosen = images[index]

    return PhotoInfo(
        path=chosen,
        # File mtime is a poor proxy for when a photo was taken, but it beats no
        # date at all and the caption degrades gracefully without one.
        taken=datetime.fromtimestamp(chosen.stat().st_mtime),
        # The kind is a key; page 3 renders it in the active language. The
        # name is the user's own folder and is passed through untouched.
        source_kind="folder",
        source_name=folder.name,
        asset_id=chosen.name,
    )


def pick_photo(
    config: dict,
    cache_dir: Path,
    slot: str = "",
) -> tuple[PhotoInfo | None, str]:
    """Resolve the configured photo source, falling back as needed.

    Returns `(photo, error)`. On success `error` is empty; on failure `photo`
    is None and `error` is a label *key*, which page 3 resolves through the
    active language. Returning a key rather than a sentence is what keeps this
    module out of the translation business: it knows about Immich and
    filesystems, not about what the panel reads like.
    """
    if not config:
        return None, ""

    fallback_dir = config.get("fallback_folder") or ""

    base_url = config.get("url") or ""
    if base_url and (config.get("api_key") or config.get("share_key")):
        try:
            return (
                from_immich(
                    base_url,
                    album_id=config.get("album_id", ""),
                    cache_dir=cache_dir,
                    api_key=config.get("api_key", ""),
                    share_key=config.get("share_key", ""),
                    album_name=config.get("album_name", ""),
                    slot=slot,
                ),
                "",
            )
        except (ImmichError, KeyError, ValueError) as err:
            logger.warning("Immich photo unavailable: %s", err)
            if not fallback_dir:
                return None, "PHOTO_IMMICH_UNREACHABLE"

    if fallback_dir:
        try:
            return from_folder(Path(fallback_dir), slot=slot), ""
        except (FileNotFoundError, OSError) as err:
            logger.warning("Photo folder unavailable: %s", err)
            return None, "PHOTO_NOT_FOUND"

    return None, "PHOTO_NO_SOURCE"
