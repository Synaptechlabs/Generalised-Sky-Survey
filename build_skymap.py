# ---------------------------------------------------------------------------
# File:        build_skymap.py
# Version:     0.1
# Date:        2026-07-12
# Author:      Scott Douglass
# Description: Renders a Mollweide-projection all-sky coverage map per
#              source, colouring each 1x1 degree tile by its tile_scans
#              status. Standard astronomical convention for showing survey
#              footprint/coverage, as opposed to a literal 3D globe render.
# ---------------------------------------------------------------------------
import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import sqlite3

DEFAULT_DB = "survey.db"
DEFAULT_OUT_DIR = Path("figures")

BG = "#0f1117"
PANEL = "#171a22"
LINE = "#343b4d"
TEXT = "#e7eaf0"
MUTED = "#a9b0c2"

STATUS_COLOURS = {
    "complete": "#33b3ff",
    "no_coverage": "#3a4257",
    "failed": "#ff6b6b",
}
# 'pending'/'running' tiles are left unplotted (background shows through) so
# the figure reads as "what has been touched so far", not a grid of every
# tile that could ever exist.


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def sources(con):
    return [r[0] for r in con.execute("SELECT DISTINCT source FROM tile_scans ORDER BY source")]


def tile_status_rows(con, source):
    return con.execute(
        """
        SELECT st.ra_min, st.dec_min, ts.status
        FROM sky_tiles st
        JOIN tile_scans ts ON ts.tile_id = st.tile_id
        WHERE ts.source = ?
        """,
        (source,),
    ).fetchall()


def render_skymap(rows, source, out_path: Path):
    by_status = {}
    for ra_min, dec_min, status in rows:
        if status not in STATUS_COLOURS:
            continue
        by_status.setdefault(status, []).append((ra_min + 0.5, dec_min + 0.5))

    fig = plt.figure(figsize=(10, 5.5), facecolor=BG)
    ax = fig.add_subplot(111, projection="mollweide", facecolor=PANEL)

    for status, points in by_status.items():
        ra = np.array([p[0] for p in points])
        dec = np.array([p[1] for p in points])
        lon = np.radians(((180 - ra) % 360) - 180)
        lat = np.radians(dec)
        ax.scatter(lon, lat, s=4, marker="s", color=STATUS_COLOURS[status], label=status, linewidths=0)

    ax.set_xticklabels([])
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.grid(color=LINE, linewidth=0.6, alpha=0.7)
    for spine in ax.spines.values():
        spine.set_color(LINE)
    ax.set_title(f"{source} tile coverage", color=TEXT, fontsize=13, pad=14)

    legend = ax.legend(
        loc="lower center", bbox_to_anchor=(0.5, -0.15), ncol=len(by_status),
        frameon=False, fontsize=9, labelcolor=MUTED,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor=BG, dpi=150, bbox_inches="tight")
    plt.close(fig)


def build_skymaps(db_path: str, out_dir: Path):
    con = connect(db_path)
    try:
        srcs = sources(con)
        written = []
        for source in srcs:
            rows = tile_status_rows(con, source)
            out_path = out_dir / f"skymap_{source}.png"
            render_skymap(rows, source, out_path)
            written.append(out_path)
        return written
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(
        description="Render a Mollweide-projection tile coverage map per source."
    )
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    written = build_skymaps(args.db, args.out_dir)
    for p in written:
        print(f"Wrote {p}")


if __name__ == "__main__":
    main()
