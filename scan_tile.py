# ---------------------------------------------------------------------------
# File:        scan_tile.py
# Version:     0.3
# Date:        2026-07-14
# Author:      Scott Douglass
# Description: Queries SDSS for one sky tile, cleans/engineers features,
#              fits a global Isolation Forest (per-source normalized, see
#              global_features.py), and stores objects/features/candidates
#              in survey.db. score_tile() here is imported directly by
#              scan_tile_gaia.py -- not duplicated.
# ---------------------------------------------------------------------------
import argparse
import sqlite3
import pandas as pd

from astroquery.sdss import SDSS

from db import connect, init_db, start_run, finish_run
from features import clean_and_engineer, FEATURE_COLS
from global_features import GLOBAL_FEATURE_COLS, compute_source_norm_stats, apply_norm_stats

from sklearn.ensemble import IsolationForest

SOURCE = "sdss"


def sdss_query_for_tile(ra_min, ra_max, dec_min, dec_max, limit):
    return f"""
    SELECT TOP {int(limit)}
        p.objID, p.ra, p.dec,
        p.u, p.g, p.r, p.i, p.z,
        p.psfMag_u, p.psfMag_g, p.psfMag_r, p.psfMag_i, p.psfMag_z,
        p.psfMagErr_u, p.psfMagErr_g, p.psfMagErr_r, p.psfMagErr_i, p.psfMagErr_z,
        p.petroRad_r, p.petroR50_r, p.petroR90_r,
        p.extinction_r,
        p.flags, p.clean, p.type
    FROM PhotoObj AS p
    WHERE
        p.clean = 1
        AND p.type = 3
        AND p.ra >= {ra_min} AND p.ra < {ra_max}
        AND p.dec >= {dec_min} AND p.dec < {dec_max}
        AND p.u BETWEEN 10 AND 25
        AND p.g BETWEEN 10 AND 25
        AND p.r BETWEEN 10 AND 22
        AND p.i BETWEEN 10 AND 25
        AND p.z BETWEEN 10 AND 25
        AND p.petroRad_r > 0
        AND p.petroR50_r > 0
        AND p.petroR90_r > 0
    """


def ensure_tile_scans(con, source):
    con.execute(
        """
        INSERT OR IGNORE INTO tile_scans(tile_id, source, status)
        SELECT tile_id, ?, 'pending' FROM sky_tiles
        """,
        (source,),
    )
    con.commit()


def next_pending_tile(con, source):
    row = con.execute(
        """
        SELECT st.*, ts.status AS scan_status
        FROM sky_tiles st
        JOIN tile_scans ts ON ts.tile_id = st.tile_id AND ts.source = ?
        WHERE ts.status IN ('pending', 'failed')
        ORDER BY 
            ABS(st.dec_min) ASC,      -- prioritize near equator first
            st.ra_min ASC,            -- then sweep in RA
            st.dec_min ASC
        LIMIT 1
        """,
        (source,),
    ).fetchone()
    return dict(row) if row else None


def get_tile(con, source, tile_id):
    row = con.execute(
        """
        SELECT st.*, ts.status AS scan_status
        FROM sky_tiles st
        JOIN tile_scans ts ON ts.tile_id = st.tile_id AND ts.source = ?
        WHERE st.tile_id = ?
        """,
        (source, tile_id),
    ).fetchone()
    return dict(row) if row else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="survey.db")
    parser.add_argument("--tile-id")
    parser.add_argument("--limit", type=int, default=100000)
    parser.add_argument("--top-n", type=int, default=2000)
    parser.add_argument("--contamination", type=float, default=0.01)
    args = parser.parse_args()

    source = SOURCE

    init_db(args.db)
    con = connect(args.db)
    run_id = start_run(con, "scan_tile", f"{source} global Isolation Forest scan")

    try:
        ensure_tile_scans(con, source)

        if args.tile_id:
            tile = get_tile(con, source, args.tile_id)
        else:
            tile = next_pending_tile(con, source)

        if not tile:
            print(f"No pending tile found for source={source}.")
            finish_run(con, run_id, "finished", "No pending tile found.")
            return

        tile_id = tile["tile_id"]
        print(f"Scanning {tile_id} [{source}]")

        con.execute(
            "UPDATE tile_scans SET status='running', last_run_id=? WHERE tile_id=? AND source=?",
            (run_id, tile_id, source),
        )
        con.commit()

        # === ROBUST SDSS QUERY ===
        query = sdss_query_for_tile(
            tile["ra_min"], tile["ra_max"], tile["dec_min"], tile["dec_max"], args.limit
        )

        table = None
        try:
            table = SDSS.query_sql(query, data_release=17)
        except Exception as query_err:
            error_str = str(query_err)
            print(f"SDSS query failed for {tile_id}: {error_str[:250]}")
            status = 'no_coverage' if "InconsistentTableError" in error_str or "header" in error_str else 'failed'
            con.execute(
                """
                UPDATE tile_scans
                SET status=?, last_scanned_at=CURRENT_TIMESTAMP,
                    object_count=0, candidate_count=0, notes=?
                WHERE tile_id=? AND source=?
                """,
                (status, error_str[:400], tile_id, source)
            )
            con.commit()
            finish_run(con, run_id, "finished" if status == 'no_coverage' else "failed", f"Query error")
            print(f"Marked {tile_id} as {status}")
            return

        # Extra safety: make sure we actually got real data with objID
        if table is None or len(table) == 0 or 'objID' not in table.colnames:
            con.execute(
                """
                UPDATE tile_scans
                SET status='no_coverage', last_scanned_at=CURRENT_TIMESTAMP,
                    object_count=0, candidate_count=0
                WHERE tile_id=? AND source=?
                """,
                (tile_id, source),
            )
            con.commit()
            finish_run(con, run_id, "finished", f"No valid {source} data for {tile_id}")
            print(f"No valid data for {tile_id} (missing columns)")
            return

        raw = table.to_pandas()
        raw["objID"] = raw["objID"].astype("int64")
        clean = clean_and_engineer(raw)

        if len(clean) < 50:
            con.execute(
                """
                UPDATE tile_scans
                SET status='complete', last_scanned_at=CURRENT_TIMESTAMP,
                    object_count=?, candidate_count=0
                WHERE tile_id=? AND source=?
                """,
                (len(clean), tile_id, source),
            )
            con.commit()
            finish_run(con, run_id, "finished", f"Tile had only {len(clean)} clean objects.")
            return

        upsert_objects(con, clean, source, run_id, tile_id)
        upsert_features(con, clean, source)
        con.commit()

        anomalies = score_tile(con, source, clean, args.contamination, args.top_n)

        for rank, row in enumerate(anomalies.itertuples(index=False), start=1):
            con.execute(
                """
                INSERT OR IGNORE INTO candidates(
                    source, objID, run_id, tile_id, anomaly_score, rank_in_run
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source, int(row.objID), run_id, tile_id, float(row.anomaly_score), rank),
            )
            con.execute(
                "INSERT OR IGNORE INTO reviews(source, objID, status) VALUES (?, ?, 'unreviewed')",
                (source, int(row.objID)),
            )

        con.execute(
            """
            UPDATE tile_scans
            SET status='complete',
                last_scanned_at=CURRENT_TIMESTAMP,
                object_count=?,
                candidate_count=?
            WHERE tile_id=? AND source=?
            """,
            (len(clean), len(anomalies), tile_id, source),
        )
        con.commit()

        finish_run(con, run_id, "finished", f"Scanned {tile_id}; clean={len(clean)} candidates={len(anomalies)}")
        print(f"Finished {tile_id}: clean={len(clean)}, candidates={len(anomalies)}")

    except Exception as e:
        error_msg = str(e)[:500]
        con.execute(
            "UPDATE tile_scans SET status='failed', notes=? WHERE last_run_id=? AND source=?",
            (error_msg, run_id, source),
        )
        con.commit()
        finish_run(con, run_id, "failed", error_msg)
        raise
    finally:
        con.close()


def load_all_global_features(con):
    """
    Every object from every source (not just this source) -- the Isolation
    Forest fit is deliberately global across the whole survey, not siloed
    per source. Only GLOBAL_FEATURE_COLS is safe to use here since that's
    the one feature set every source populates the same way.

    This IS NOT NULL filter is also the ONLY thing excluding reference-only
    sources (currently just WISE, see features_wise.py) from the fit --
    there is no separate allowlist/registry of "which sources feed the
    forest". WISE deliberately never populates GLOBAL_FEATURE_COLS, so it
    never passes this filter. If this filter is ever loosened or replaced,
    check what sources would newly be admitted -- a source silently
    entering the pooled fit here produces no error, just a contaminated
    anomaly_score.
    """
    where_clause = " AND ".join(f"f.{c} IS NOT NULL" for c in GLOBAL_FEATURE_COLS)
    query = f"""
        SELECT f.source, f.objID, {', '.join('f.' + c for c in GLOBAL_FEATURE_COLS)}
        FROM features f
        WHERE {where_clause}
    """
    return pd.read_sql_query(query, con)


def score_tile(con, source, tile_df, contamination, top_n, id_col="objID"):
    """
    Shared by scan_tile.py and scan_tile_gaia.py (imported directly, not
    duplicated) -- id_col differs since SDSS keys on objID and Gaia on
    source_id.
    """
    all_features = load_all_global_features(con)

    if len(all_features) < 50:
        all_features = tile_df[[id_col] + GLOBAL_FEATURE_COLS].rename(columns={id_col: "objID"}).copy()
        all_features["source"] = source

    # Per-source normalization before pooling: see global_features.py.
    # Without this, a single scale fit across the pooled multi-source
    # population lets whichever source has a different typical scale or
    # sample density dominate the fit.
    norm_stats = compute_source_norm_stats(all_features, GLOBAL_FEATURE_COLS)
    normalized_all = apply_norm_stats(all_features, GLOBAL_FEATURE_COLS, norm_stats)

    model = IsolationForest(
        n_estimators=500,
        max_samples=256,
        contamination=contamination,
        random_state=42,
        n_jobs=1,
    )
    model.fit(normalized_all[GLOBAL_FEATURE_COLS])

    tile_df = tile_df.copy()
    tile_for_norm = tile_df.copy()
    tile_for_norm["source"] = source
    normalized_tile = apply_norm_stats(tile_for_norm, GLOBAL_FEATURE_COLS, norm_stats)
    tile_df["anomaly_score"] = model.score_samples(normalized_tile[GLOBAL_FEATURE_COLS])
    return tile_df.sort_values("anomaly_score").head(top_n).copy()


# === YOUR ORIGINAL UPSERT FUNCTIONS (unchanged) ===
def upsert_objects(con, df, source, run_id, tile_id):
    object_cols = [
        "objID", "ra", "dec",
        "u", "g", "r", "i", "z",
        "psfMag_u", "psfMag_g", "psfMag_r", "psfMag_i", "psfMag_z",
        "psfMagErr_u", "psfMagErr_g", "psfMagErr_r", "psfMagErr_i", "psfMagErr_z",
        "petroRad_r", "petroR50_r", "petroR90_r",
        "extinction_r", "flags", "clean", "type",
    ]

    for row in df[object_cols].itertuples(index=False):
        vals = row._asdict()
        con.execute(
            """
            INSERT INTO objects(
                source, objID, ra, dec,
                u, g, r, i, z,
                psfMag_u, psfMag_g, psfMag_r, psfMag_i, psfMag_z,
                psfMagErr_u, psfMagErr_g, psfMagErr_r, psfMagErr_i, psfMagErr_z,
                petroRad_r, petroR50_r, petroR90_r,
                extinction_r, flags, clean, type,
                first_seen_run_id, last_seen_run_id
            )
            VALUES (
                :source, :objID, :ra, :dec,
                :u, :g, :r, :i, :z,
                :psfMag_u, :psfMag_g, :psfMag_r, :psfMag_i, :psfMag_z,
                :psfMagErr_u, :psfMagErr_g, :psfMagErr_r, :psfMagErr_i, :psfMagErr_z,
                :petroRad_r, :petroR50_r, :petroR90_r,
                :extinction_r, :flags, :clean, :type,
                :run_id, :run_id
            )
            ON CONFLICT(source, objID) DO UPDATE SET
                last_seen_run_id=excluded.last_seen_run_id,
                last_seen_at=CURRENT_TIMESTAMP
            """,
            {**vals, "source": source, "run_id": run_id},
        )

        con.execute(
            """
            INSERT OR IGNORE INTO object_tiles(source, objID, tile_id, first_seen_run_id)
            VALUES (?, ?, ?, ?)
            """,
            (source, int(vals["objID"]), tile_id, run_id),
        )


def upsert_features(con, df, source):
    feature_insert_cols = [
        "objID",
        "u_g", "g_r", "r_i", "i_z",
        "psf_minus_model_r",
        "log_petroRad_r", "log_petroR50_r", "log_petroR90_r",
        "concentration_r", "mu_r",
        "r",
        "extinction_r",
    ] + GLOBAL_FEATURE_COLS

    for row in df[feature_insert_cols].itertuples(index=False):
        vals = row._asdict()
        con.execute(
            """
            INSERT INTO features(
                source, objID,
                u_g, g_r, r_i, i_z,
                psf_minus_model_r,
                log_petroRad_r, log_petroR50_r, log_petroR90_r,
                concentration_r, mu_r,
                r, extinction_r,
                global_colour_span, global_colour_jump
            )
            VALUES (
                :source, :objID,
                :u_g, :g_r, :r_i, :i_z,
                :psf_minus_model_r,
                :log_petroRad_r, :log_petroR50_r, :log_petroR90_r,
                :concentration_r, :mu_r,
                :r, :extinction_r,
                :global_colour_span, :global_colour_jump
            )
            ON CONFLICT(source, objID) DO UPDATE SET
                u_g=excluded.u_g,
                g_r=excluded.g_r,
                r_i=excluded.r_i,
                i_z=excluded.i_z,
                psf_minus_model_r=excluded.psf_minus_model_r,
                log_petroRad_r=excluded.log_petroRad_r,
                log_petroR50_r=excluded.log_petroR50_r,
                log_petroR90_r=excluded.log_petroR90_r,
                concentration_r=excluded.concentration_r,
                mu_r=excluded.mu_r,
                r=excluded.r,
                extinction_r=excluded.extinction_r,
                global_colour_span=excluded.global_colour_span,
                global_colour_jump=excluded.global_colour_jump,
                updated_at=CURRENT_TIMESTAMP
            """,
            {**vals, "source": source},
        )


if __name__ == "__main__":
    main()