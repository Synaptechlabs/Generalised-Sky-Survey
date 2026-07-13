# ---------------------------------------------------------------------------
# File:        global_features.py
# Version:     0.2
# Date:        2026-07-12
# Author:      Scott Douglass
# Description: Single source of truth for the survey-agnostic colour-shape
#              feature columns that every source's features.py-equivalent
#              computes into the same shared `features` table columns, and
#              that the ONE cross-survey Isolation Forest fits on. Imported
#              by every per-source feature module and every scanner, so the
#              column list can never drift out of sync between sources.
#              Also holds the per-source normalization applied before that
#              shared fit (see compute_source_norm_stats).
# ---------------------------------------------------------------------------
import pandas as pd

GLOBAL_FEATURE_COLS = ["global_colour_span", "global_colour_jump"]


def compute_source_norm_stats(df, feature_cols):
    """
    Per-source median/MAD for each feature column, computed from df's own
    values. Without this, a single scaler fit across the pooled multi-
    source population lets whichever source has a different typical scale
    or sample density dominate the fit, so an object can end up flagged
    "unusual" mainly because of which catalogue it came from rather than
    genuine rarity within that catalogue's own population.

    MAD (median absolute deviation) is used rather than mean/std because
    the values being normalized include exactly the tail cases the model
    is meant to detect -- a mean/std scale lets those outliers distort the
    scale being used to measure them. Falls back to std when MAD is ~0
    (e.g. too few rows, or a degenerate distribution).
    """
    stats = {}
    for src, group in df.groupby("source"):
        stats[src] = {}
        for col in feature_cols:
            values = group[col]
            median = values.median()
            mad = (values - median).abs().median()
            scale = mad if mad > 1e-6 else (values.std() or 1.0)
            stats[src][col] = (median, scale)
    return stats


def apply_norm_stats(df, feature_cols, stats):
    """
    Normalize df's feature columns (returns a copy) using per-source stats
    from compute_source_norm_stats. A source missing from stats (e.g. the
    very first tile ever scanned for a brand-new source) is left unscaled
    (median 0, scale 1) rather than raising.
    """
    df = df.copy()
    for col in feature_cols:
        normed = pd.Series(index=df.index, dtype=float)
        for src, idx in df.groupby("source").groups.items():
            median, scale = stats.get(src, {}).get(col, (0.0, 1.0))
            normed.loc[idx] = (df.loc[idx, col] - median) / scale
        df[col] = normed
    return df
