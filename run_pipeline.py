# ---------------------------------------------------------------------------
# File:        run_pipeline.py
# Version:     0.3
# Date:        2026-07-13
# Author:      Scott Douglass
# Description: Long-running/bounded orchestrator that loops the per-source
#              tile scanners (scan_tile.py, scan_tile_gaia.py, ...) and
#              periodically triggers score_candidates.py,
#              crossmatch_candidates.py, and export_candidates.py.
# ---------------------------------------------------------------------------
import argparse
import logging
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

from db import connect
from scan_tile import ensure_tile_scans

# Registry of scannable sources -> their scanner script. Add an entry here
# when a new source's scan_tile_<source>.py is built; --sources controls
# which of these actually run in a given invocation.
# Note: unlike sdss/gaia, wise is ingested as a reference dataset for
# crossmatch_candidates.py, not scored by the shared Isolation Forest --
# see scan_tile_wise.py -- but it's still scanned every round the same way.
SOURCE_SCANNERS = {
    "sdss": "scan_tile.py",
    "gaia": "scan_tile_gaia.py",
    "wise": "scan_tile_wise.py",
}

def setup_logging(log_file="runner.log"):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)

def run_step(logger, name, cmd, timeout=None):
    """Run a subprocess step. Returns True on success, False on failure."""
    logger.info(f"=== {name} ===")
    logger.info("Command: " + " ".join(cmd))
    
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.stdout.strip():
            logger.info(result.stdout.strip())
        if result.stderr.strip():
            logger.warning(result.stderr.strip())
        logger.info(f"{name} completed successfully.")
        return True
    except subprocess.TimeoutExpired:
        logger.error(f"{name} timed out after {timeout} seconds.")
        return False
    except subprocess.CalledProcessError as e:
        logger.error(f"{name} FAILED with exit code {e.returncode}")
        if e.stdout:
            logger.error("STDOUT:\n" + e.stdout)
        if e.stderr:
            logger.error("STDERR:\n" + e.stderr)
        return False
    except Exception as e:
        logger.exception(f"Unexpected error running {name}: {e}")
        return False

def count_pending_tiles(db_path, source):
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            """
            SELECT COUNT(*)
            FROM tile_scans
            WHERE source = ?
              AND status IN ('pending', 'failed')
            """,
            (source,),
        )
        return int(cur.fetchone()[0])
    finally:
        con.close()

def main():
    parser = argparse.ArgumentParser(
        description="Long-running tile scanner for astronomical survey anomaly detection"
    )
    parser.add_argument("--db", default="survey.db", help="Path to SQLite database")
    parser.add_argument("--sources", default=None,
                        help="Comma-separated sources to scan each round. "
                             "Defaults to every registered scanner in SOURCE_SCANNERS "
                             f"(currently: {', '.join(SOURCE_SCANNERS)}) -- no source is preferred "
                             "over another; pass this to scan a subset instead.")
    parser.add_argument("--tiles", type=int, default=None,
                        help="Max rounds (one tile per source each) to process this run (ignored with --forever)")
    parser.add_argument("--forever", action="store_true",
                        help="Keep running until no more pending tiles")
    parser.add_argument("--score-every", type=int, default=5,
                        help="Run triage scoring every N tiles (0 to disable)")
    parser.add_argument("--score-limit", type=int, default=500)
    parser.add_argument("--crossmatch-every", type=int, default=10,
                        help="Run crossmatch every N tiles (0 to disable)")
    parser.add_argument("--crossmatch-limit", type=int, default=50)
    parser.add_argument("--export-every", type=int, default=10,
                        help="Run export every N tiles (0 to disable)")
    parser.add_argument("--export-limit", type=int, default=500)
    parser.add_argument("--sleep", type=float, default=5.0,
                        help="Seconds to sleep between tiles (good for old hardware)")
    parser.add_argument("--log-file", default="runner.log", help="Log file path")
    parser.add_argument("--max-consecutive-failures", type=int, default=5,
                        help="Stop after this many consecutive scan failures (0 = unlimited)")

    args = parser.parse_args()
    logger = setup_logging(args.log_file)
    py = sys.executable

    if args.sources is None:
        sources = list(SOURCE_SCANNERS)
    else:
        sources = [s.strip() for s in args.sources.split(",") if s.strip()]
        unknown = [s for s in sources if s not in SOURCE_SCANNERS]
        if unknown:
            logger.error(f"Unknown source(s) {unknown} -- known scanners: {list(SOURCE_SCANNERS)}")
            return

    if not args.forever and args.tiles is None:
        args.tiles = 1

    # Seed tile_scans for every source up front. Without this, a source with
    # zero tile_scans rows (brand new, never scanned) and a source that has
    # genuinely finished every tile both read as "0 pending" to
    # count_pending_tiles below -- the round loop would then skip a new
    # source forever, before it ever got the chance to seed its own rows
    # (that seeding normally happens inside the scanner script itself, which
    # never gets invoked if it looks skippable on round 1).
    con = connect(args.db)
    for source in sources:
        ensure_tile_scans(con, source)
    con.close()

    logger.info(f"Starting runner | db={args.db} sources={sources} forever={args.forever} rounds={args.tiles}")
    logger.info(f"Score every {args.score_every}, Crossmatch every {args.crossmatch_every}, "
                f"Export every {args.export_every}, Sleep={args.sleep}s")

    scanned = 0
    consecutive_failures = 0

    try:
        while True:
            if not args.forever and scanned >= args.tiles:
                logger.info("Reached round limit. Stopping.")
                break

            pending_by_source = {s: count_pending_tiles(args.db, s) for s in sources}
            if all(p == 0 for p in pending_by_source.values()):
                logger.info(f"No pending/failed tiles for any of {sources}. All done!")
                break

            logger.info(f"Pending tiles: {pending_by_source} | Rounds scanned so far: {scanned}")

            # Run one tile per source this round. A round counts as
            # successful if at least one source actually scanned something
            # (a source with 0 pending tiles is legitimately skipped, not a
            # failure).
            round_success = False
            for source in sources:
                if pending_by_source[source] == 0:
                    continue
                scan_cmd = [py, SOURCE_SCANNERS[source], "--db", args.db]
                ok = run_step(logger, f"scan_tile_{source}_{scanned + 1}", scan_cmd)
                round_success = round_success or ok

            if round_success:
                consecutive_failures = 0
                scanned += 1
            else:
                consecutive_failures += 1
                logger.warning(f"Round failed (no source scanned). Consecutive failures: {consecutive_failures}")
                if args.max_consecutive_failures > 0 and consecutive_failures >= args.max_consecutive_failures:
                    logger.error("Too many consecutive failures. Stopping runner.")
                    break

            # Periodic post-processing only on rounds where something scanned
            if round_success:
                if args.score_every > 0 and scanned % args.score_every == 0:
                    score_cmd = [
                        py, "score_candidates.py",
                        "--db", args.db,
                        "--limit", str(args.score_limit)
                    ]
                    run_step(logger, "score_candidates", score_cmd)

                if args.crossmatch_every > 0 and scanned % args.crossmatch_every == 0:
                    cross_cmd = [
                        py, "crossmatch_candidates.py",
                        "--db", args.db,
                        "--limit", str(args.crossmatch_limit)
                    ]
                    run_step(logger, "crossmatch", cross_cmd)

                if args.export_every > 0 and scanned % args.export_every == 0:
                    export_cmd = [
                        py, "export_candidates.py",
                        "--db", args.db,
                        "--limit", str(args.export_limit)
                    ]
                    run_step(logger, "export", export_cmd)

            if args.sleep > 0:
                time.sleep(args.sleep)

    except KeyboardInterrupt:
        logger.info("Interrupted by user (Ctrl+C). Shutting down gracefully...")
    except Exception as e:
        logger.exception(f"Unexpected error in runner main loop: {e}")
    finally:
        logger.info(f"Runner finished. Total tiles attempted this run: {scanned}")
        logger.info(f"Log saved to: {args.log_file}")

if __name__ == "__main__":
    main()