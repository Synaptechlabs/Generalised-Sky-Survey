# ---------------------------------------------------------------------------
# File:        rank_tracking.py
# Version:     0.1
# Date:        2026-07-14
# Author:      Scott Douglass
# Description: Records/compares top-N-by-review_score snapshots
#              (rank_history table, schema.sql) across scan cycles.
# ---------------------------------------------------------------------------
"""
Exists because the Isolation Forest refits from scratch every tile scan
against a growing population (scan_tile.py), so review_score/rank for a
given candidate is not a stable quantity over time -- a candidate's rank
can move purely from population growth, not from anything about the object
itself. "Newly entered the top N" is the useful, stable-enough signal;
raw rank isn't.

score_candidates.py calls record_rank_history() once per scoring run (its
own runs.run_id is the scan_cycle -- there's no separate cycle counter).
build_review.py calls new_entrants() at build time to badge cards.
"""
TOP_N = 50


def record_rank_history(con, scan_cycle, top_n=TOP_N):
    """
    Appends a snapshot of the current top `top_n` candidates by
    review_score under scan_cycle. Append-only -- never updates or deletes
    prior cycles' rows.
    """
    rows = con.execute(
        """
        SELECT candidate_id, review_score
        FROM triage
        ORDER BY review_score DESC
        LIMIT ?
        """,
        (top_n,),
    ).fetchall()

    for rank_in_cycle, row in enumerate(rows, start=1):
        con.execute(
            """
            INSERT INTO rank_history(candidate_id, scan_cycle, rank_in_cycle, review_score)
            VALUES (?, ?, ?, ?)
            """,
            (row["candidate_id"], scan_cycle, rank_in_cycle, row["review_score"]),
        )


def _top_n_candidate_ids(con, scan_cycle, top_n):
    return {
        row["candidate_id"]
        for row in con.execute(
            "SELECT candidate_id FROM rank_history WHERE scan_cycle = ? AND rank_in_cycle <= ?",
            (scan_cycle, top_n),
        )
    }


def latest_cycle(con):
    row = con.execute("SELECT MAX(scan_cycle) AS c FROM rank_history").fetchone()
    return row["c"] if row and row["c"] is not None else None


def previous_cycle(con, scan_cycle):
    """
    The most recent recorded cycle strictly before scan_cycle. Not simply
    scan_cycle - 1: scan_cycle is a runs.run_id, and runs are shared across
    every script type (scans, crossmatch, export, ...), not just scoring,
    so consecutive recorded cycles are not consecutive integers.
    """
    row = con.execute(
        "SELECT MAX(scan_cycle) AS c FROM rank_history WHERE scan_cycle < ?",
        (scan_cycle,),
    ).fetchone()
    return row["c"] if row and row["c"] is not None else None


def new_entrants(con, scan_cycle, top_n=TOP_N):
    """
    Candidates in scan_cycle's top `top_n` that were not in the previous
    recorded cycle's top `top_n`. Returns {candidate_id: is_new_candidate}.

    is_new_candidate distinguishes a genuinely first-ever appearance in
    rank_history (a brand-new candidate) from one that existed in an
    earlier cycle outside the top N and has now climbed in as the
    population/ranking shifted -- these are different and worth keeping
    distinct (see the module docstring on why raw rank movement alone
    isn't a reliable signal).

    If scan_cycle is the first cycle ever recorded, every member of its
    top `top_n` counts as a new entrant, all with is_new_candidate=True --
    there is no earlier cycle for any of them to have climbed from.
    """
    current_top = _top_n_candidate_ids(con, scan_cycle, top_n)
    if not current_top:
        return {}

    prev = previous_cycle(con, scan_cycle)
    previous_top = _top_n_candidate_ids(con, prev, top_n) if prev is not None else set()

    entrants = current_top - previous_top
    if not entrants:
        return {}

    placeholders = ", ".join("?" * len(entrants))
    ever_seen_before = {
        row["candidate_id"]
        for row in con.execute(
            f"""
            SELECT DISTINCT candidate_id FROM rank_history
            WHERE scan_cycle < ? AND candidate_id IN ({placeholders})
            """,
            (scan_cycle, *entrants),
        )
    }

    return {cid: (cid not in ever_seen_before) for cid in entrants}
