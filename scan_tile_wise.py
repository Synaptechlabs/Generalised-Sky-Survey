# ---------------------------------------------------------------------------
# File:        scan_tile_wise.py
# Version:     0.1
# Date:        2026-07-13
# Author:      Scott Douglass
# Description: Queries AllWISE (via IRSA's TAP service) for one sky tile,
#              cleans/engineers w1_w2/w2_w3, and caches objects/features in
#              survey.db under source='wise'. Unlike scan_tile.py/
#              scan_tile_gaia.py, this does NOT call score_tile() or write
#              to candidates/reviews -- WISE is ingested as a reference
#              dataset for crossmatch_candidates.py to match SDSS/Gaia
#              candidates against locally, not scored by the shared
#              Isolation Forest (see features_wise.py). Tile selection is
#              still imported directly from scan_tile.py, not duplicated.
# ---------------------------------------------------------------------------
import argparse

from astroquery.ipac.irsa import Irsa

from db import connect, init_db, start_run, finish_run
from features_wise import clean_and_engineer
from scan_tile import ensure_tile_scans, next_pending_tile, get_tile

SOURCE = "wise"


def wise_query_for_tile(ra_min, ra_max, dec_min, dec_max, limit):
    return f"""
    SELECT TOP {int(limit)}
        cntr, ra, dec,
        w1mpro, w1sigmpro,
        w2mpro, w2sigmpro,
        w3mpro, w3sigmpro,
        w4mpro, w4sigmpro,
        cc_flags
    FROM allwise_p3as_psd
    WHERE
        ra >= {ra_min} AND ra < {ra_max}
        AND dec >= {dec_min} AND dec < {dec_max}
        AND w1mpro IS NOT NULL
        AND w2mpro IS NOT NULL
    """


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="survey.db")
    parser.add_argument("--tile-id")
    parser.add_argument("--limit", type=int, default=100000)
    args = parser.parse_args()

    source = SOURCE

    init_db(args.db)
    con = connect(args.db)
    run_id = start_run(con, "scan_tile_wise", f"{source} tile ingest (reference dataset, not forest-scored)")

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

        query = wise_query_for_tile(
            tile["ra_min"], tile["ra_max"], tile["dec_min"], tile["dec_max"], args.limit
        )

        table = None
        try:
            table = Irsa.query_tap(query).to_table()
        except Exception as query_err:
            error_str = str(query_err)
            print(f"WISE query failed for {tile_id}: {error_str[:250]}")
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

        if table is None or len(table) == 0 or "cntr" not in table.colnames:
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
        raw["cntr"] = raw["cntr"].astype("int64")
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

        con.execute(
            """
            UPDATE tile_scans
            SET status='complete',
                last_scanned_at=CURRENT_TIMESTAMP,
                object_count=?,
                candidate_count=0
            WHERE tile_id=? AND source=?
            """,
            (len(clean), tile_id, source),
        )
        con.commit()

        finish_run(con, run_id, "finished", f"Scanned {tile_id}; clean={len(clean)}")
        print(f"Finished {tile_id}: clean={len(clean)}")

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


def _sql_none_if_nan(value):
    """
    NaN reaches SQLite as the literal float NaN, not NULL -- an "IS NOT
    NULL" check downstream would then wrongly treat a missing W3/W4
    measurement as present, the exact class of bug the Gaia morphology
    columns hit before morphology_available was added to triage.py.
    """
    if isinstance(value, float) and value != value:
        return None
    return value


def upsert_objects(con, df, source, run_id, tile_id):
    object_cols = [
        "cntr", "ra", "dec",
        "w1mpro", "w1sigmpro",
        "w2mpro", "w2sigmpro",
        "w3mpro", "w3sigmpro",
        "w4mpro", "w4sigmpro",
    ]

    for row in df[object_cols].itertuples(index=False):
        vals = {k: _sql_none_if_nan(v) for k, v in row._asdict().items()}
        con.execute(
            """
            INSERT INTO objects(
                source, objID, ra, dec,
                w1mpro, w1sigmpro,
                w2mpro, w2sigmpro,
                w3mpro, w3sigmpro,
                w4mpro, w4sigmpro,
                first_seen_run_id, last_seen_run_id
            )
            VALUES (
                :source, :cntr, :ra, :dec,
                :w1mpro, :w1sigmpro,
                :w2mpro, :w2sigmpro,
                :w3mpro, :w3sigmpro,
                :w4mpro, :w4sigmpro,
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
            (source, int(vals["cntr"]), tile_id, run_id),
        )


def upsert_features(con, df, source):
    feature_insert_cols = ["cntr", "w1_w2", "w2_w3"]

    for row in df[feature_insert_cols].itertuples(index=False):
        vals = {k: _sql_none_if_nan(v) for k, v in row._asdict().items()}
        con.execute(
            """
            INSERT INTO features(
                source, objID,
                w1_w2, w2_w3
            )
            VALUES (
                :source, :cntr,
                :w1_w2, :w2_w3
            )
            ON CONFLICT(source, objID) DO UPDATE SET
                w1_w2=excluded.w1_w2,
                w2_w3=excluded.w2_w3,
                updated_at=CURRENT_TIMESTAMP
            """,
            {**vals, "source": source},
        )


if __name__ == "__main__":
    main()
