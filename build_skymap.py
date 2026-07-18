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

# Per-source colour for "complete" tiles, used on the composite map and on
# each source's own map, so the two stay visually consistent. "no_coverage"
# and "failed" stay shared/neutral -- only completed coverage is source-coded.
SOURCE_COLOURS = {
    "sdss": "#33b3ff",
    "gaia": "#ffd447",
    "wise": "#39ff14",
}


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
    colours = {**STATUS_COLOURS, "complete": SOURCE_COLOURS.get(source, STATUS_COLOURS["complete"])}
    by_status = {}
    for ra_min, dec_min, status in rows:
        if status not in colours:
            continue
        by_status.setdefault(status, []).append((ra_min + 0.5, dec_min + 0.5))

    fig = plt.figure(figsize=(10, 5.5), facecolor=BG)
    ax = fig.add_subplot(111, projection="mollweide", facecolor=PANEL)

    for status, points in by_status.items():
        ra = np.array([p[0] for p in points])
        dec = np.array([p[1] for p in points])
        lon = np.radians(((180 - ra) % 360) - 180)
        lat = np.radians(dec)
        ax.scatter(lon, lat, s=4, marker="s", color=colours[status], label=status, linewidths=0)

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


def blend_colours(hex_colours) -> str:
    rgbs = [tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in hex_colours]
    r = sum(c[0] for c in rgbs) // len(rgbs)
    g = sum(c[1] for c in rgbs) // len(rgbs)
    b = sum(c[2] for c in rgbs) // len(rgbs)
    return f"#{r:02x}{g:02x}{b:02x}"


def render_composite_skymap(rows_by_source, out_path: Path):
    # Each tile belongs to exactly one combination of sources that have
    # completed it (e.g. {sdss}, {sdss, wise}, {sdss, gaia, wise}), so
    # combinations partition the tiles rather than overlapping -- colour
    # each combination as the blend of its member sources' colours instead
    # of layering per-source scatters, which would just hide whichever
    # source was drawn first wherever coverage overlaps. A tile that any
    # source failed on is flagged red regardless of what else completed it
    # -- an error tile shouldn't get diluted into a blend and hidden.
    tile_combo = {}
    failed_tiles = set()
    for source, rows in rows_by_source.items():
        for ra_min, dec_min, status in rows:
            if status == "failed":
                failed_tiles.add((ra_min, dec_min))
            elif status == "complete":
                tile_combo.setdefault((ra_min, dec_min), set()).add(source)

    by_combo = {}
    for (ra_min, dec_min), combo_sources in tile_combo.items():
        if (ra_min, dec_min) in failed_tiles:
            continue
        combo = tuple(sorted(combo_sources))
        by_combo.setdefault(combo, []).append((ra_min + 0.5, dec_min + 0.5))
    if failed_tiles:
        by_combo[("failed",)] = [(ra_min + 0.5, dec_min + 0.5) for ra_min, dec_min in failed_tiles]

    fig = plt.figure(figsize=(10, 5.5), facecolor=BG)
    ax = fig.add_subplot(111, projection="mollweide", facecolor=PANEL)

    for combo in sorted(by_combo):
        points = by_combo[combo]
        ra = np.array([p[0] for p in points])
        dec = np.array([p[1] for p in points])
        lon = np.radians(((180 - ra) % 360) - 180)
        lat = np.radians(dec)
        if combo == ("failed",):
            colour = STATUS_COLOURS["failed"]
        else:
            colour = blend_colours([SOURCE_COLOURS.get(s, STATUS_COLOURS["complete"]) for s in combo])
        ax.scatter(lon, lat, s=4, marker="s", color=colour, label="+".join(combo), linewidths=0)

    ax.set_xticklabels([])
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.grid(color=LINE, linewidth=0.6, alpha=0.7)
    for spine in ax.spines.values():
        spine.set_color(LINE)
    ax.set_title("composite tile coverage", color=TEXT, fontsize=13, pad=14)

    legend = ax.legend(
        loc="lower center", bbox_to_anchor=(0.5, -0.15), ncol=min(len(by_combo), 4),
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
        rows_by_source = {}
        for source in srcs:
            rows = tile_status_rows(con, source)
            rows_by_source[source] = rows
            out_path = out_dir / f"skymap_{source}.png"
            render_skymap(rows, source, out_path)
            written.append(out_path)

        composite_path = out_dir / "skymap_composite.png"
        render_composite_skymap(rows_by_source, composite_path)
        written.append(composite_path)
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
