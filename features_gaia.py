# ---------------------------------------------------------------------------
# File:        features_gaia.py
# Version:     0.1
# Date:        2026-07-11
# Author:      Scott Douglass
# Description: Cleans raw Gaia DR3 astrometry/photometry and engineers the
#              derived colour/astrometric feature columns used for
#              Isolation Forest scoring of Gaia candidates.
# ---------------------------------------------------------------------------
import numpy as np
import pandas as pd

from global_features import GLOBAL_FEATURE_COLS

BASE_COLS = [
    "source_id", "ra", "dec",
    "phot_g_mean_mag", "phot_bp_mean_mag", "phot_rp_mean_mag",
    "parallax", "parallax_error",
    "pmra", "pmdec",
    "ruwe", "astrometric_excess_noise",
]

FEATURE_COLS = [
    "bp_rp", "bp_g", "g_rp",
    "pm_total",
    "parallax_over_error",
    "ruwe",
    "astrometric_excess_noise",
]


def clean_and_engineer(df):
    df = df.copy()
    df["source_id"] = df["source_id"].astype("int64")

    df = df.dropna(subset=BASE_COLS).copy()

    good = (df["phot_g_mean_mag"] > 0) & (df["parallax_error"] > 0)
    df = df[good].copy()

    df["bp_rp"] = df["phot_bp_mean_mag"] - df["phot_rp_mean_mag"]
    df["bp_g"] = df["phot_bp_mean_mag"] - df["phot_g_mean_mag"]
    df["g_rp"] = df["phot_g_mean_mag"] - df["phot_rp_mean_mag"]

    df["pm_total"] = np.sqrt(df["pmra"] ** 2 + df["pmdec"] ** 2)
    df["parallax_over_error"] = df["parallax"] / df["parallax_error"]

    # Survey-agnostic colour-shape features -- see global_features.py.
    # BP is bluest, RP is reddest, matching SDSS's u(bluest)-z(reddest) sign convention.
    df["global_colour_span"] = df["phot_bp_mean_mag"] - df["phot_rp_mean_mag"]
    df["global_colour_jump"] = df[["bp_g", "g_rp"]].abs().max(axis=1)

    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=FEATURE_COLS + GLOBAL_FEATURE_COLS).copy()

    return df
