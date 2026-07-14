# ---------------------------------------------------------------------------
# File:        features_wise.py
# Version:     0.1
# Date:        2026-07-13
# Author:      Scott Douglass
# Description: Cleans raw AllWISE photometry and engineers w1_w2/w2_w3.
#              Deliberately does NOT compute global_colour_span/
#              global_colour_jump (global_features.py) -- WISE candidates
#              are never scored by the shared Isolation Forest; w1_w2 only
#              reaches other sources' candidates via a crossmatch join
#              (crossmatch_candidates.py -> triage.py's wise_red_excess
#              flag). See scan_tile_wise.py for why.
# ---------------------------------------------------------------------------
import numpy as np
import pandas as pd

BASE_COLS = [
    "cntr", "ra", "dec",
    "w1mpro", "w1sigmpro",
    "w2mpro", "w2sigmpro",
]

# Only w1_w2 is required for a row to survive cleaning -- W3/W4 are
# populated far less often in AllWISE than W1/W2, so w2_w3 is left nullable
# rather than dropping every row missing it (see objects table comment in
# schema.sql).
FEATURE_COLS = ["w1_w2"]


def clean_and_engineer(df):
    df = df.copy()
    df["cntr"] = df["cntr"].astype("int64")

    df = df.dropna(subset=BASE_COLS).copy()

    good_errors = (df["w1sigmpro"] < 0.5) & (df["w2sigmpro"] < 0.5)

    # cc_flags is a 4-char string, one char per band (W1..W4), '0' = clean.
    # Only W1/W2 are mandatory here, so only their two flag characters gate
    # inclusion -- a contamination flag on the (possibly undetected) W3/W4
    # bands shouldn't drop an otherwise-clean W1/W2 measurement.
    clean_w1_w2 = df["cc_flags"].astype(str).str[:2] == "00"

    df = df[good_errors & clean_w1_w2].copy()

    df["w1_w2"] = df["w1mpro"] - df["w2mpro"]

    has_w3 = df["w3mpro"].notna()
    df["w2_w3"] = np.where(has_w3, df["w2mpro"] - df["w3mpro"], np.nan)

    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=FEATURE_COLS).copy()

    return df
