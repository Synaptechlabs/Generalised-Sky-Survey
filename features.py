# ---------------------------------------------------------------------------
# File:        features.py
# Version:     0.1
# Date:        2026-07-11
# Author:      Scott Douglass
# Description: Cleans raw SDSS photometry and engineers the derived
#              colour/morphology feature columns used for Isolation Forest
#              scoring.
# ---------------------------------------------------------------------------
import numpy as np
import pandas as pd

from global_features import GLOBAL_FEATURE_COLS

MAG_COLS = ["u", "g", "r", "i", "z"]
ERR_COLS = ["psfMagErr_u", "psfMagErr_g", "psfMagErr_r", "psfMagErr_i", "psfMagErr_z"]

BASE_COLS = [
    "objID", "ra", "dec",
    "u", "g", "r", "i", "z",
    "psfMag_u", "psfMag_g", "psfMag_r", "psfMag_i", "psfMag_z",
    "petroRad_r", "petroR50_r", "petroR90_r",
    "extinction_r"
]

FEATURE_COLS = [
    "u_g", "g_r", "r_i", "i_z",
    "r",
    "psf_minus_model_r",
    "log_petroRad_r",
    "log_petroR50_r",
    "log_petroR90_r",
    "concentration_r",
    "mu_r",
    "extinction_r",
]

def clean_and_engineer(df):
    df = df.copy()
    df["objID"] = df["objID"].astype("int64")

    df = df.dropna(subset=BASE_COLS + ERR_COLS).copy()

    good_errors = (df[ERR_COLS] < 0.5).all(axis=1)

    good_radii = (
        (df["petroRad_r"] > 0.2) &
        (df["petroR50_r"] > 0.2) &
        (df["petroR90_r"] > df["petroR50_r"])
    )

    df = df[good_errors & good_radii].copy()

    df["u_g"] = df["u"] - df["g"]
    df["g_r"] = df["g"] - df["r"]
    df["r_i"] = df["r"] - df["i"]
    df["i_z"] = df["i"] - df["z"]

    df["psf_minus_model_r"] = df["psfMag_r"] - df["r"]

    df["log_petroRad_r"] = np.log10(df["petroRad_r"].clip(lower=0.2))
    df["log_petroR50_r"] = np.log10(df["petroR50_r"].clip(lower=0.2))
    df["log_petroR90_r"] = np.log10(df["petroR90_r"].clip(lower=0.2))

    df["concentration_r"] = df["petroR90_r"] / df["petroR50_r"]

    df["mu_r"] = (
        df["r"] + 2.5 * np.log10(2 * np.pi * df["petroR50_r"] ** 2)
    )

    # Survey-agnostic colour-shape features -- see global_features.py.
    df["global_colour_span"] = df["u"] - df["z"]
    df["global_colour_jump"] = df[["u_g", "g_r", "r_i", "i_z"]].abs().max(axis=1)

    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=FEATURE_COLS + GLOBAL_FEATURE_COLS).copy()

    return df
