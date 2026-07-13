# ---------------------------------------------------------------------------
# File:        scan_tile_gaia.py
# Version:     0.2
# Date:        2026-07-12
# Author:      Scott Douglass
# Description: Queries Gaia DR3 for one sky tile, cleans/engineers
#              astrometric features, fits a global Isolation Forest, and
#              stores objects/features/candidates in survey.db under
#              source='gaia'. Gaia-specific counterpart to scan_tile.py --
#              tile selection AND scoring are both imported directly, not
#              duplicated.
# ---------------------------------------------------------------------------
import argparse

from astroquery.gaia import Gaia

from db import connect, init_db, start_run, finish_run
from features_gaia import clean_and_engineer
from global_features import GLOBAL_FEATURE_COLS
from scan_tile import ensure_tile_scans, next_pending_tile, get_tile, score_tile

SOURCE = "gaia"

Gaia.TIMEOUT = 60


def gaia_query_for_tile(ra_min, ra_max, dec_min, dec_max, limit):
    return f"""
    SELECT TOP {int(limit)}
        source_id, ra, dec,
        phot_g_mean_mag, phot_bp_mean_mag, phot_rp_mean_mag,
        parallax, parallax_error,
        pmra, pmdec,
        ruwe, astrometric_excess_noise
    FROM gaiadr3.gaia_source
    WHERE
        ra >= {ra_min} AND ra < {ra_max}
        AND dec >= {dec_min} AND dec < {dec_max}
        AND phot_g_mean_mag IS NOT NULL
        AND phot_bp_mean_mag IS NOT NULL
        AND phot_rp_mean_mag IS NOT NULL
    """


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
    run_id = start_run(con, "scan_tile_gaia", f"{source} global Isolation Forest scan")

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

        query = gaia_query_for_tile(
            tile["ra_min"], tile["ra_max"], tile["dec_min"], tile["dec_max"], args.limit
        )

        table = None
        try:
            job = Gaia.launch_job(query)
            table = job.get_results()
        except Exception as query_err:
            error_str = str(query_err)
            print(f"Gaia query failed for {tile_id}: {error_str[:250]}")
            con.execute(
                """
                UPDATE tile_scans
                SET status='failed', last_scanned_at=CURRENT_TIMESTAMP,
                    object_count=0, candidate_count=0, notes=?
                WHERE tile_id=? AND source=?
                """,
                (error_str[:400], tile_id, source),
            )
            con.commit()
            finish_run(con, run_id, "failed", "Query error")
            print(f"Marked {tile_id} as failed")
            return

        if table is None or len(table) == 0 or "source_id" not in table.colnames:
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
        raw["source_id"] = raw["source_id"].astype("int64")
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

        anomalies = score_tile(con, source, clean, args.contamination, args.top_n, id_col="source_id")

        for rank, row in enumerate(anomalies.itertuples(index=False), start=1):
            con.execute(
                """
                INSERT OR IGNORE INTO candidates(
                    source, objID, run_id, tile_id, anomaly_score, rank_in_run
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source, int(row.source_id), run_id, tile_id, float(row.anomaly_score), rank),
            )
            con.execute(
                "INSERT OR IGNORE INTO reviews(source, objID, status) VALUES (?, ?, 'unreviewed')",
                (source, int(row.source_id)),
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


def upsert_objects(con, df, source, run_id, tile_id):
    object_cols = [
        "source_id", "ra", "dec",
        "phot_g_mean_mag", "phot_bp_mean_mag", "phot_rp_mean_mag",
        "parallax", "parallax_error",
        "pmra", "pmdec",
        "ruwe", "astrometric_excess_noise",
    ]

    for row in df[object_cols].itertuples(index=False):
        vals = row._asdict()
        con.execute(
            """
            INSERT INTO objects(
                source, objID, ra, dec,
                phot_g_mean_mag, phot_bp_mean_mag, phot_rp_mean_mag,
                parallax, parallax_error,
                pmra, pmdec,
                ruwe, astrometric_excess_noise,
                first_seen_run_id, last_seen_run_id
            )
            VALUES (
                :source, :source_id, :ra, :dec,
                :phot_g_mean_mag, :phot_bp_mean_mag, :phot_rp_mean_mag,
                :parallax, :parallax_error,
                :pmra, :pmdec,
                :ruwe, :astrometric_excess_noise,
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
            (source, int(vals["source_id"]), tile_id, run_id),
        )


def upsert_features(con, df, source):
    feature_insert_cols = [
        "source_id",
        "bp_rp", "bp_g", "g_rp",
        "pm_total", "parallax_over_error",
        "ruwe", "astrometric_excess_noise",
    ] + GLOBAL_FEATURE_COLS

    for row in df[feature_insert_cols].itertuples(index=False):
        vals = row._asdict()
        con.execute(
            """
            INSERT INTO features(
                source, objID,
                bp_rp, bp_g, g_rp,
                pm_total, parallax_over_error,
                ruwe, astrometric_excess_noise,
                global_colour_span, global_colour_jump
            )
            VALUES (
                :source, :source_id,
                :bp_rp, :bp_g, :g_rp,
                :pm_total, :parallax_over_error,
                :ruwe, :astrometric_excess_noise,
                :global_colour_span, :global_colour_jump
            )
            ON CONFLICT(source, objID) DO UPDATE SET
                bp_rp=excluded.bp_rp,
                bp_g=excluded.bp_g,
                g_rp=excluded.g_rp,
                pm_total=excluded.pm_total,
                parallax_over_error=excluded.parallax_over_error,
                ruwe=excluded.ruwe,
                astrometric_excess_noise=excluded.astrometric_excess_noise,
                global_colour_span=excluded.global_colour_span,
                global_colour_jump=excluded.global_colour_jump,
                updated_at=CURRENT_TIMESTAMP
            """,
            {**vals, "source": source},
        )


if __name__ == "__main__":
    main()
