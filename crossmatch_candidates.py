# ---------------------------------------------------------------------------
# File:        crossmatch_candidates.py
# Version:     0.2
# Date:        2026-07-13
# Author:      Scott Douglass
# Description: Cross-matches candidate objects against SIMBAD, NED, Gaia,
#              and (locally) WISE, and caches the results in survey.db.
# ---------------------------------------------------------------------------
import argparse
import math
import time

import numpy as np
from astroquery.simbad import Simbad
from astroquery.ipac.ned import Ned
from astroquery.gaia import Gaia
from astropy.coordinates import SkyCoord
import astropy.units as u

from db import connect, init_db, start_run, finish_run

Simbad.TIMEOUT = 30
Ned.TIMEOUT = 30
Gaia.TIMEOUT = 30

WISE_MATCH_RADIUS_ARCSEC = 6.0  # AllWISE's own W1/W2 angular resolution


def wise_match_for_coord(con, ra, dec, radius_arcsec=WISE_MATCH_RADIUS_ARCSEC):
    """
    Local match against already-ingested WISE objects/features
    (source='wise'), unlike the live SIMBAD/NED/Gaia queries below -- WISE
    is cached via scan_tile_wise.py's own tile scanning, so a candidate
    only picks up a match once that patch of sky has actually been
    WISE-scanned. 6" matches AllWISE's angular resolution, tighter than the
    30" used for the sparser SIMBAD/NED/Gaia lookups (those catalogues are
    far less dense, so a wider radius doesn't risk confusing neighbours the
    way it would against WISE's much higher source density).
    """
    dec_delta = radius_arcsec / 3600.0
    ra_delta = dec_delta / max(math.cos(math.radians(dec)), 1e-6)

    rows = con.execute(
        """
        SELECT o.objID, o.ra, o.dec, f.w1_w2
        FROM objects o
        JOIN features f ON f.source = o.source AND f.objID = o.objID
        WHERE o.source = 'wise'
          AND o.ra BETWEEN ? AND ?
          AND o.dec BETWEEN ? AND ?
        """,
        (ra - ra_delta, ra + ra_delta, dec - dec_delta, dec + dec_delta),
    ).fetchall()

    if not rows:
        return None

    coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)
    catalog = SkyCoord(ra=np.array([r["ra"] for r in rows]) * u.deg,
                        dec=np.array([r["dec"] for r in rows]) * u.deg)
    seps = coord.separation(catalog).arcsec

    best = int(seps.argmin())
    if seps[best] > radius_arcsec:
        return None

    best_row = rows[best]
    return int(best_row["objID"]), float(seps[best]), best_row["w1_w2"]

def pending_candidates(con, limit):
    return con.execute(
        """
        SELECT DISTINCT o.source, o.objID, o.ra, o.dec
        FROM candidates c
        JOIN objects o ON o.source = c.source AND o.objID = c.objID
        LEFT JOIN crossmatches x ON x.source = o.source AND x.objID = o.objID
        WHERE x.objID IS NULL
        ORDER BY c.anomaly_score ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="survey.db")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--sleep", type=float, default=1.0)
    args = parser.parse_args()

    init_db(args.db)
    con = connect(args.db)
    run_id = start_run(con, "crossmatch_candidates", "SIMBAD + NED + Gaia crossmatch")

    try:
        rows = pending_candidates(con, args.limit)
        print(f"Crossmatching {len(rows)} candidates...")

        for row in rows:
            source = row["source"]
            objid = int(row["objID"])
            coord = SkyCoord(ra=row["ra"] * u.deg, dec=row["dec"] * u.deg)
            radius = 10.0 * u.arcsec

            # SIMBAD
            simbad_match = simbad_id = simbad_otype = ""
            try:
                s = Simbad.query_region(coord, radius=30*u.arcsec)
                if s is not None and len(s) > 0:
                    simbad_match = 1
                    simbad_id = str(s[0].get("main_id", s[0][0]))
                    simbad_otype = str(s[0].get("otype", ""))
            except Exception:
                pass

            # NED
            ned_match = ned_name = ned_type = ""
            try:
                n = Ned.query_region(coord, radius=30*u.arcsec)
                if n is not None and len(n) > 0:
                    ned_match = 1
                    ned_name = str(n[0].get("Object Name", ""))
                    ned_type = str(n[0].get("Type", ""))
            except Exception:
                pass

            # Gaia
            gaia_match = gaia_source_id = gaia_dist = ""
            try:
                j = Gaia.cone_search_async(coord, radius=radius)
                g = j.get_results()
                if len(g) > 0:
                    gaia_match = 1
                    gaia_source_id = str(g[0]["source_id"])
                    gaia_dist = float(g[0]["dist"])
            except Exception:
                pass

            # WISE (local match against cached objects/features -- see
            # wise_match_for_coord above)
            wise_match = wise_objid = wise_dist = wise_w1_w2 = ""
            try:
                match = wise_match_for_coord(con, row["ra"], row["dec"])
                if match is not None:
                    wise_match = 1
                    wise_objid, wise_dist, wise_w1_w2 = match
            except Exception:
                pass

            # Build status string
            parts = []
            if simbad_match: parts.append("SIMBAD")
            if ned_match: parts.append("NED")
            if gaia_match: parts.append("Gaia")
            if wise_match: parts.append("WISE")
            status = "+".join(parts) if parts else "UNCATALOGUED"

            print(f"{source}:{objid} -> {status}")

            # Save to DB
            con.execute(
                """
                INSERT INTO crossmatches (
                    source, objID, search_radius_arcsec,
                    simbad_checked_at, simbad_match, simbad_id, simbad_otype,
                    ned_checked_at, ned_match, ned_name, ned_type,
                    gaia_checked_at, gaia_match, gaia_source_id, gaia_dist,
                    wise_checked_at, wise_match, wise_objID, wise_dist, wise_w1_w2
                )
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?,
                        CURRENT_TIMESTAMP, ?, ?, ?,
                        CURRENT_TIMESTAMP, ?, ?, ?, ?)
                ON CONFLICT(source, objID) DO UPDATE SET
                    search_radius_arcsec = excluded.search_radius_arcsec,
                    simbad_checked_at = excluded.simbad_checked_at,
                    simbad_match = excluded.simbad_match,
                    simbad_id = excluded.simbad_id,
                    simbad_otype = excluded.simbad_otype,
                    ned_checked_at = excluded.ned_checked_at,
                    ned_match = excluded.ned_match,
                    ned_name = excluded.ned_name,
                    ned_type = excluded.ned_type,
                    gaia_checked_at = excluded.gaia_checked_at,
                    gaia_match = excluded.gaia_match,
                    gaia_source_id = excluded.gaia_source_id,
                    gaia_dist = excluded.gaia_dist,
                    wise_checked_at = excluded.wise_checked_at,
                    wise_match = excluded.wise_match,
                    wise_objID = excluded.wise_objID,
                    wise_dist = excluded.wise_dist,
                    wise_w1_w2 = excluded.wise_w1_w2,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (source, objid, 30.0,
                 simbad_match, simbad_id, simbad_otype,
                 ned_match, ned_name, ned_type,
                 gaia_match, gaia_source_id, gaia_dist,
                 wise_match, wise_objid, wise_dist, wise_w1_w2)
            )
            con.commit()

            time.sleep(args.sleep)

        finish_run(con, run_id, "finished", f"Crossmatched {len(rows)} candidates.")

    except Exception as e:
        finish_run(con, run_id, "failed", str(e))
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()