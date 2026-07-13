# ---------------------------------------------------------------------------
# File:        thumbnails.py
# Version:     0.1
# Date:        2026-07-11
# Author:      Scott Douglass
# Description: Shared SDSS thumbnail cache helpers used by build_review.py.
# ---------------------------------------------------------------------------
"""
SDSS thumbnail cache helpers, shared by build_review.py (the only script
that currently needs cached images -- CSV export is data-only now).
"""
import time
from pathlib import Path

import requests

IMAGE_WIDTH = 512
IMAGE_HEIGHT = 512
IMAGE_SCALE = 0.25
REQUEST_SLEEP = 0.4


def sdss_stamp_url(ra, dec, width=IMAGE_WIDTH, height=IMAGE_HEIGHT):
    return (
        "https://skyserver.sdss.org/dr17/SkyServerWS/ImgCutout/getjpeg"
        f"?ra={ra}&dec={dec}&width={width}&height={height}"
        f"&scale={IMAGE_SCALE}&opt=G"
    )


def skyserver_url(objid):
    return f"https://skyserver.sdss.org/dr17/VisualTools/explore/summary?id={int(objid)}"


def valid_cached_image(path: Path) -> bool:
    """Avoid re-downloading existing thumbnails unless they are clearly empty/broken."""
    try:
        return path.exists() and path.is_file() and path.stat().st_size > 1000
    except OSError:
        return False


def thumbnail_path_for(thumb_dir: Path, source, objid) -> Path:
    return thumb_dir / f"{source}_{int(objid)}.jpg"


def get_thumbnail(thumb_dir: Path, source, objid, ra, dec, download_missing=True) -> str:
    thumb_dir.mkdir(parents=True, exist_ok=True)
    path = thumbnail_path_for(thumb_dir, source, objid)

    if valid_cached_image(path):
        return str(path)

    if not download_missing:
        return ""

    try:
        r = requests.get(sdss_stamp_url(ra, dec), timeout=15)
        r.raise_for_status()
        path.write_bytes(r.content)
        time.sleep(REQUEST_SLEEP)
        if valid_cached_image(path):
            return str(path)
        return ""
    except Exception as e:
        print(f"Thumbnail failed for {source}:{objid}: {e}")
        return ""
