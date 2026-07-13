# ---------------------------------------------------------------------------
# File:        startup/populate.py
# Version:     0.1
# Date:        2026-07-11
# Author:      Scott Douglass
# Description: One-time script that generates the full-sky tile grid and
#              bootstraps tile_scans for the sdss source.
# ---------------------------------------------------------------------------
import argparse
import sqlite3

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="survey.db")
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    
    # Count existing tiles
    tile_count = con.execute("SELECT COUNT(*) FROM sky_tiles").fetchone()[0]
    print(f"Existing sky_tiles rows: {tile_count}")

    if tile_count > 0:
        print("Tiles already exist. Skipping generation.")
        pending = con.execute("""
            SELECT COUNT(*) FROM tile_scans 
            WHERE status IN ('pending', 'failed')
        """).fetchone()[0]
        print(f"Pending scans: {pending}")
        con.close()
        return

    print("Generating ~32,400 × 1°×1° tiles...")
    tiles = []
    for dec in range(-90, 90):           # -90 to +89
        for ra in range(360):            # 0 to 359
            tile_id = f"{ra:03d}_{dec:+03d}"   # e.g. "120_+05", "005_-30"
            tiles.append((
                tile_id,
                float(ra), float(ra + 1),
                float(dec), float(dec + 1)
            ))

    con.executemany(
        """
        INSERT INTO sky_tiles 
        (tile_id, ra_min, ra_max, dec_min, dec_max)
        VALUES (?, ?, ?, ?, ?)
        """,
        tiles
    )
    con.commit()
    print(f"✅ Created {len(tiles):,} tiles.")

    # Now bootstrap tile_scans for sdss
    con.execute(
        """
        INSERT OR IGNORE INTO tile_scans (tile_id, source, status)
        SELECT tile_id, 'sdss', 'pending' FROM sky_tiles
        """
    )
    con.commit()

    pending = con.execute(
        "SELECT COUNT(*) FROM tile_scans WHERE source='sdss' AND status='pending'"
    ).fetchone()[0]
    print(f"✅ {pending:,} pending tiles ready for sdss.")

    con.close()

if __name__ == "__main__":
    main()