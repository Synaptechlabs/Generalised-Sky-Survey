# ---------------------------------------------------------------------------
# File:        export_candidates.py
# Version:     0.1
# Date:        2026-07-11
# Author:      Scott Douglass
# Description: Exports a standalone CSV dump of candidates (including
#              triage scores where available) for download/sharing.
# ---------------------------------------------------------------------------
import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

DEFAULT_OUT_DIR = Path("review_pack")


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def export_dataframe(db_path: str, limit: int, min_score: float | None = None) -> pd.DataFrame:
    con = connect(db_path)
    try:
        query = """
        SELECT
            c.candidate_id,
            c.anomaly_score,
            c.run_id,
            c.tile_id,
            c.rank_in_run,
            c.created_at AS candidate_created_at,

            o.source,
            o.objID,
            o.ra, o.dec,
            o.u, o.g, o.r, o.i, o.z,
            o.psfMag_u, o.psfMag_g, o.psfMag_r, o.psfMag_i, o.psfMag_z,
            o.psfMagErr_u, o.psfMagErr_g, o.psfMagErr_r, o.psfMagErr_i, o.psfMagErr_z,
            o.petroRad_r, o.petroR50_r, o.petroR90_r,
            o.extinction_r,
            o.flags AS sdss_flags,
            o.clean,
            o.type,

            f.u_g, f.g_r, f.r_i, f.i_z,
            f.psf_minus_model_r,
            f.log_petroRad_r, f.log_petroR50_r, f.log_petroR90_r,
            f.concentration_r,
            f.mu_r,

            x.search_radius_arcsec,
            x.simbad_match, x.simbad_id, x.simbad_otype,
            x.ned_match, x.ned_name, x.ned_type,
            x.gaia_match, x.gaia_source_id, x.gaia_dist,

            rv.status,
            rv.priority AS human_priority,
            rv.human_notes,

            t.definition_version,
            t.colour_span, t.red_score, t.full_red_score,
            t.colour_curvature_gr_ri, t.colour_curvature_ri_iz,
            t.colour_smoothness, t.colour_jump_max,
            t.size_ratio_petro_r50, t.r90_r50_width,
            t.compactness_proxy, t.diffuse_proxy, t.psf_per_radius,
            t.surface_brightness_offset,
            t.weirdness_score, t.artefact_risk, t.review_score,
            t.triage_class, t.triage_flags,
            t.flag_extreme_colour, t.flag_likely_model_issue,
            t.flag_possible_lsb, t.flag_compact_red, t.flag_probable_shred,
            t.flag_gaia_matched, t.flag_catalogued
        FROM candidates c
        JOIN objects o ON o.source = c.source AND o.objID = c.objID
        JOIN features f ON f.source = c.source AND f.objID = c.objID
        LEFT JOIN crossmatches x ON x.source = c.source AND x.objID = c.objID
        LEFT JOIN reviews rv ON rv.source = c.source AND rv.objID = c.objID
        LEFT JOIN triage t ON t.candidate_id = c.candidate_id
        """

        params: list = []
        if min_score is not None:
            query += " WHERE c.anomaly_score < ?"
            params.append(min_score)

        query += " ORDER BY c.anomaly_score ASC LIMIT ?"
        params.append(limit)
        return pd.read_sql_query(query, con, params=params)
    finally:
        con.close()


def export_candidates(
    db_path: str,
    out_dir: Path = DEFAULT_OUT_DIR,
    limit: int = 300,
    min_score: float | None = None,
) -> tuple[Path, pd.DataFrame]:
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    df = export_dataframe(db_path, limit=limit, min_score=min_score)
    print(f"Exported {len(df)} candidates from database.")

    csv_path = out_dir / f"candidates_{timestamp}.csv"
    df.to_csv(csv_path, index=False)

    print("\n=== EXPORT DONE ===")
    print(f"CSV: {csv_path}")
    print("This is a standalone data dump for download/sharing only.")
    print("It is not read by any other GSS script -- run score_candidates.py")
    print("then build_review.py to build the review pack; both read survey.db directly.")
    return csv_path, df


def main():
    parser = argparse.ArgumentParser(
        description="Export candidate data (including triage scores, where computed) to a "
                    "standalone CSV for download/sharing. Not part of the review pipeline -- "
                    "score_candidates.py and build_review.py read survey.db directly."
    )
    parser.add_argument("--db", default="survey.db")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--min-score", type=float, default=None)
    args = parser.parse_args()

    export_candidates(
        db_path=args.db,
        out_dir=args.out_dir,
        limit=args.limit,
        min_score=args.min_score,
    )


if __name__ == "__main__":
    main()
