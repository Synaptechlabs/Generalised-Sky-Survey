# ---------------------------------------------------------------------------
# File:        score_candidates.py
# Version:     0.2
# Date:        2026-07-13
# Author:      Scott Douglass
# Description: Computes triage scores for candidates missing them (or
#              scored under an older definition version) and stores
#              results in survey.db's triage table.
# ---------------------------------------------------------------------------
import argparse

from db import connect, init_db, start_run, finish_run
from triage import add_candidate_triage, DEFINITIONS_VERSION

TRIAGE_COLS = [
    "colour_span", "red_score", "full_red_score",
    "colour_curvature_gr_ri", "colour_curvature_ri_iz",
    "colour_smoothness", "colour_jump_max",
    "size_ratio_petro_r50", "r90_r50_width",
    "compactness_proxy", "diffuse_proxy", "psf_per_radius",
    "surface_brightness_offset",
    "weirdness_score", "artefact_risk", "review_score",
    "triage_class", "triage_flags",
    "flag_extreme_colour", "flag_likely_model_issue",
    "flag_possible_lsb", "flag_compact_red", "flag_probable_shred",
    "flag_gaia_matched", "flag_catalogued", "flag_wise_red_excess",
]


def pending_candidates(con, limit):
    return con.execute(
        """
        SELECT
            c.candidate_id, c.anomaly_score,
            o.u, o.g, o.r, o.i, o.z,
            o.petroRad_r, o.petroR50_r, o.petroR90_r,
            f.u_g, f.g_r, f.r_i, f.i_z,
            f.psf_minus_model_r, f.concentration_r, f.mu_r,
            f.global_colour_span, f.global_colour_jump,
            x.simbad_id, x.ned_name, x.gaia_match, x.gaia_source_id,
            x.wise_match, x.wise_w1_w2
        FROM candidates c
        JOIN objects o ON o.source = c.source AND o.objID = c.objID
        JOIN features f ON f.source = c.source AND f.objID = c.objID
        LEFT JOIN crossmatches x ON x.source = c.source AND x.objID = c.objID
        LEFT JOIN triage t ON t.candidate_id = c.candidate_id
        WHERE t.candidate_id IS NULL OR t.definition_version != ?
        ORDER BY c.anomaly_score ASC
        LIMIT ?
        """,
        (DEFINITIONS_VERSION, limit),
    ).fetchall()


def upsert_triage(con, candidate_id, triage):
    values = {**triage, "candidate_id": candidate_id, "definition_version": DEFINITIONS_VERSION}
    cols = ["candidate_id", "definition_version"] + TRIAGE_COLS
    placeholders = ", ".join(f":{c}" for c in cols)
    update_clause = ", ".join(f"{c}=excluded.{c}" for c in ["definition_version"] + TRIAGE_COLS)
    con.execute(
        f"""
        INSERT INTO triage ({', '.join(cols)})
        VALUES ({placeholders})
        ON CONFLICT(candidate_id) DO UPDATE SET
            {update_clause},
            computed_at = CURRENT_TIMESTAMP
        """,
        values,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Compute triage scores/flags for candidates that don't have them yet "
                    "(or were scored under an older definition version), storing results "
                    "directly in survey.db's triage table."
    )
    parser.add_argument("--db", default="survey.db")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    init_db(args.db)
    con = connect(args.db)
    run_id = start_run(con, "score_candidates", f"Triage scoring, definition v{DEFINITIONS_VERSION}")

    try:
        rows = pending_candidates(con, args.limit)
        print(f"Scoring {len(rows)} candidates (definition v{DEFINITIONS_VERSION})...")

        for row in rows:
            data = dict(row)
            triage = add_candidate_triage(data)
            upsert_triage(con, data["candidate_id"], triage)

        con.commit()
        finish_run(con, run_id, "finished", f"Scored {len(rows)} candidates.")
        print(f"Done. Scored {len(rows)} candidates.")

    except Exception as e:
        finish_run(con, run_id, "failed", str(e))
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
