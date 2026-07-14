# ---------------------------------------------------------------------------
# File:        wise_cutouts.py
# Version:     0.1
# Date:        2026-07-14
# Author:      Scott Douglass
# Description: WISE W1/W2 FITS cutout fetch + PNG rasterization for
#              build_review.py, cached alongside SDSS thumbnails
#              (thumbnails.py). Only used for candidates with a WISE
#              crossmatch (crossmatch_candidates.py) that has a coadd_id
#              (scan_tile_wise.py) to fetch a cutout for.
# ---------------------------------------------------------------------------
"""
Fetches W1/W2 FITS postage stamps for a matched WISE object from IRSA's IBE
service, rasterizes each to a grayscale PNG (ZScaleInterval + AsinhStretch,
standard practice for displaying astronomical images with a large dynamic
range), and builds a W1/W2 false-colour composite: W1->green, W2->red, blue
as the average of the two (a literal 2-band RGB would leave blue flat
black, which reads as missing data rather than a deliberate 2-band image).

Cached the same way as thumbnails.py: same thumb_dir, skip refetch if
already cached.
"""
import gzip
import time
from io import BytesIO
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests
from astropy.io import fits
from astropy.visualization import AsinhStretch, ImageNormalize, ZScaleInterval

CUTOUT_SIZE_PIX = 100
REQUEST_TIMEOUT = 20
REQUEST_SLEEP = 0.4

IBE_BASE = "https://irsa.ipac.caltech.edu/ibe/data/wise/allwise/p3am_cdd"


def wise_cutout_fits_url(coadd_id, band, ra, dec, size=CUTOUT_SIZE_PIX):
    coaddgrp = coadd_id[:2]
    coadd_ra = coadd_id[:4]
    return (
        f"{IBE_BASE}/{coaddgrp}/{coadd_ra}/{coadd_id}/"
        f"{coadd_id}-w{band}-int-3.fits"
        f"?center={ra},{dec}&size={size}pix"
    )


def wise_viewer_url(ra, dec):
    return (
        "https://irsa.ipac.caltech.edu/applications/wise/"
        f"?api=searchPos&ra={ra}&dec={dec}&imageset=allsky-4band"
    )


def _cutout_path(thumb_dir: Path, wise_objid, kind: str) -> Path:
    return thumb_dir / f"wise_{int(wise_objid)}_{kind}.png"


def _valid_cached_image(path: Path) -> bool:
    """Same threshold logic as thumbnails.valid_cached_image."""
    try:
        return path.exists() and path.is_file() and path.stat().st_size > 500
    except OSError:
        return False


def _fetch_band_data(coadd_id, band, ra, dec):
    url = wise_cutout_fits_url(coadd_id, band, ra, dec)
    r = requests.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    # IBE serves these gzip-compressed under Content-Type: application/gzip
    # (not Content-Encoding: gzip), so requests won't auto-decompress it.
    raw = gzip.decompress(r.content)
    with fits.open(BytesIO(raw)) as hdul:
        data = hdul[0].data.astype(float)
    time.sleep(REQUEST_SLEEP)
    return data


def _normalize(data):
    norm = ImageNormalize(data, interval=ZScaleInterval(), stretch=AsinhStretch())
    return np.clip(norm(data), 0.0, 1.0)


def get_wise_cutouts(thumb_dir: Path, wise_objid, coadd_id, ra, dec, download_missing=True):
    """
    Returns (w1_path, w2_path, composite_path); any entry is "" if that
    image isn't available (not cached and either download_missing=False or
    the fetch/render failed) -- same missing-value contract as
    thumbnails.get_thumbnail().
    """
    thumb_dir.mkdir(parents=True, exist_ok=True)
    w1_path = _cutout_path(thumb_dir, wise_objid, "w1")
    w2_path = _cutout_path(thumb_dir, wise_objid, "w2")
    composite_path = _cutout_path(thumb_dir, wise_objid, "composite")
    paths = (w1_path, w2_path, composite_path)

    if all(_valid_cached_image(p) for p in paths):
        return tuple(str(p) for p in paths)

    if not download_missing or not coadd_id:
        return tuple(str(p) if _valid_cached_image(p) else "" for p in paths)

    try:
        w1_data = _fetch_band_data(coadd_id, 1, ra, dec)
        w2_data = _fetch_band_data(coadd_id, 2, ra, dec)
        w1_norm = _normalize(w1_data)
        w2_norm = _normalize(w2_data)

        plt.imsave(w1_path, w1_norm, cmap="gray", vmin=0, vmax=1)
        plt.imsave(w2_path, w2_norm, cmap="gray", vmin=0, vmax=1)

        rgb = np.dstack([w2_norm, w1_norm, (w1_norm + w2_norm) / 2.0])
        plt.imsave(composite_path, rgb)
    except Exception as e:
        print(f"WISE cutout failed for objID={wise_objid} (coadd_id={coadd_id}): {e}")

    return tuple(str(p) if _valid_cached_image(p) else "" for p in paths)
