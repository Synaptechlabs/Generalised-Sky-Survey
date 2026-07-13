# ---------------------------------------------------------------------------
# File:        crossmatch_candidates.py
# Version:     0.1
# Date:        2026-07-11
# Author:      Scott Douglass
# Description: Cross-matches candidate objects against SIMBAD, NED, and
#              Gaia and caches the results in survey.db.
# ---------------------------------------------------------------------------
import argparse
import time

from astroquery.simbad import Simbad
from astroquery.ipac.ned import Ned
from astroquery.gaia import Gaia
from astropy.coordinates import SkyCoord
import astropy.units as u

from db import connect, init_db, start_run, finish_run

Simbad.TIMEOUT = 30
Ned.TIMEOUT = 30
Gaia.TIMEOUT = 30

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

            # Build status string
            parts = []
            if simbad_match: parts.append("SIMBAD")
            if ned_match: parts.append("NED")
            if gaia_match: parts.append("Gaia")
            status = "+".join(parts) if parts else "UNCATALOGUED"

            print(f"{source}:{objid} -> {status}")

            # Save to DB
            con.execute(
                """
                INSERT INTO crossmatches (
                    source, objID, search_radius_arcsec,
                    simbad_checked_at, simbad_match, simbad_id, simbad_otype,
                    ned_checked_at, ned_match, ned_name, ned_type,
                    gaia_checked_at, gaia_match, gaia_source_id, gaia_dist
                )
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?,
                        CURRENT_TIMESTAMP, ?, ?, ?)
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
                    updated_at = CURRENT_TIMESTAMP
                """,
                (source, objid, 30.0,
                 simbad_match, simbad_id, simbad_otype,
                 ned_match, ned_name, ned_type,
                 gaia_match, gaia_source_id, gaia_dist)
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