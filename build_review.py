# ---------------------------------------------------------------------------
# File:        build_review.py
# Version:     0.1
# Date:        2026-07-11
# Author:      Scott Douglass
# Description: Builds the review_pack/review.html candidate review page
#              directly from survey.db, including per-card metric
#              definitions.
# ---------------------------------------------------------------------------
import argparse
import html
import math
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from thumbnails import get_thumbnail, skyserver_url, sdss_stamp_url
from triage import METRIC_DEFINITIONS, DEFINITIONS_VERSION, DEFINITIONS_UPDATED

DEFAULT_DB = "survey.db"
DEFAULT_REVIEW_DIR = Path("review_pack")


def esc(x) -> str:
    if x is None or pd.isna(x):
        return ""
    return html.escape(str(x))


def safe_float(x, default=None):
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def fmt(x, digits=3):
    x = safe_float(x)
    if x is None or not math.isfinite(x):
        return "—"
    return f"{x:.{digits}f}"


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def load_candidates(db_path: str, limit: int, min_review_score: float | None = None) -> pd.DataFrame:
    con = connect(db_path)
    try:
        query = """
        SELECT
            c.candidate_id, c.anomaly_score, c.run_id, c.tile_id, c.rank_in_run,

            o.source, o.objID, o.ra, o.dec,
            o.u, o.g, o.r, o.i, o.z,
            o.petroRad_r, o.petroR50_r, o.petroR90_r,

            f.u_g, f.g_r, f.r_i, f.i_z, f.mu_r, f.concentration_r,

            x.simbad_match, x.simbad_id, x.simbad_otype,
            x.ned_match, x.ned_name, x.ned_type,
            x.gaia_match, x.gaia_source_id, x.gaia_dist,

            rv.status, rv.priority AS human_priority, rv.human_notes,

            t.definition_version,
            t.weirdness_score, t.artefact_risk, t.review_score,
            t.triage_class, t.triage_flags,
            t.full_red_score, t.colour_smoothness, t.colour_jump_max,
            t.psf_per_radius, t.compactness_proxy, t.surface_brightness_offset,
            t.flag_gaia_matched, t.flag_catalogued
        FROM candidates c
        JOIN objects o ON o.source = c.source AND o.objID = c.objID
        JOIN features f ON f.source = c.source AND f.objID = c.objID
        LEFT JOIN crossmatches x ON x.source = c.source AND x.objID = c.objID
        LEFT JOIN reviews rv ON rv.source = c.source AND rv.objID = c.objID
        JOIN triage t ON t.candidate_id = c.candidate_id
        """
        params: list = []
        if min_review_score is not None:
            query += " WHERE t.review_score >= ?"
            params.append(min_review_score)
        query += " ORDER BY t.review_score DESC, t.artefact_risk ASC LIMIT ?"
        params.append(limit)
        return pd.read_sql_query(query, con, params=params)
    finally:
        con.close()


def pill_list(value):
    if value is None or pd.isna(value):
        return ""
    parts = [p.strip() for p in str(value).replace(",", ";").split(";") if p.strip() and p.strip() != "none"]
    return "".join(f'<span class="pill">{esc(p)}</span>' for p in parts)


def crossmatch_summary(row):
    parts = []
    if safe_float(row.get("simbad_match"), 0) or str(row.get("simbad_id", "")).strip() not in ("", "nan", "None"):
        label = str(row.get("simbad_id", "")).strip()
        otype = str(row.get("simbad_otype", "")).strip()
        if otype and otype.lower() != "nan":
            label += f" ({otype})"
        parts.append(f"SIMBAD: {esc(label or 'match')}")
    if safe_float(row.get("ned_match"), 0) or str(row.get("ned_name", "")).strip() not in ("", "nan", "None"):
        name = str(row.get("ned_name", "")).strip()
        ntype = str(row.get("ned_type", "")).strip()
        if ntype and ntype.lower() != "nan":
            name += f" ({ntype})"
        parts.append(f"NED: {esc(name or 'match')}")
    if safe_float(row.get("gaia_match"), 0) or str(row.get("gaia_source_id", "")).strip() not in ("", "nan", "None"):
        gid = str(row.get("gaia_source_id", "")).strip()
        dist = fmt(row.get("gaia_dist"), 3)
        parts.append(f"Gaia: {esc(gid or 'match')} / dist {dist} arcsec")
    return "<br>".join(parts) if parts else "No catalogue match recorded"


def metric_card(label, value):
    return f'<div class="metric"><div class="metric-value">{esc(value)}</div><div class="metric-label">{esc(label)}</div></div>'


def table_rows(row, names):
    out = []
    for label, col, digits in names:
        out.append(f"<tr><th>{esc(label)}</th><td>{fmt(row.get(col), digits)}</td></tr>")
    return "\n".join(out)


def band_profile(row):
    bands = [("u", row.get("u")), ("g", row.get("g")), ("r", row.get("r")), ("i", row.get("i")), ("z", row.get("z"))]
    vals = [safe_float(v) for _, v in bands]
    finite = [v for v in vals if v is not None and math.isfinite(v)]
    if not finite:
        return "<p class='subtle'>No band data.</p>"
    lo, hi = min(finite), max(finite)
    span = max(hi - lo, 0.1)
    html_parts = []
    for band, val in bands:
        v = safe_float(val)
        if v is None or not math.isfinite(v):
            width = 0
            label = "—"
        else:
            # Magnitudes are inverted: smaller number is brighter, so reverse the bar.
            width = 8 + 92 * ((hi - v) / span)
            label = f"{v:.2f}"
        html_parts.append(
            f'<div class="bar-row"><span>{band}</span><div class="bar"><i style="width:{width:.1f}%"></i></div><b>{label}</b></div>'
        )
    return "\n".join(html_parts)


def colour_profile(row):
    cols = [("u-g", "u_g"), ("g-r", "g_r"), ("r-i", "r_i"), ("i-z", "i_z")]
    vals = [abs(safe_float(row.get(c), 0.0)) for _, c in cols]
    maxv = max(vals + [0.1])
    out = []
    for label, col in cols:
        v = safe_float(row.get(col), 0.0)
        width = min(100, 100 * abs(v) / maxv)
        out.append(f'<div class="bar-row"><span>{label}</span><div class="bar"><i style="width:{width:.1f}%"></i></div><b>{fmt(v, 2)}</b></div>')
    return "\n".join(out)


def definitions_card() -> str:
    items = []
    for d in METRIC_DEFINITIONS:
        items.append(f"""
        <div class="def-item">
            <div class="def-head"><b>{esc(d['label'])}</b></div>
            <div class="def-what">{esc(d['what'])}</div>
            <details>
                <summary>Formula &amp; interpretation</summary>
                <div class="def-formula"><b>Formula:</b> {esc(d['formula'])}</div>
                <div class="def-why"><b>Why GSS records it:</b> {esc(d['why'])}</div>
                <div class="def-interpret"><b>Interpretation:</b> {esc(d['interpret'])}</div>
            </details>
        </div>""")
    return f"""
    <section class="definitions">
        <h3>Metric definitions</h3>
        <p class="subtle">Definitions v{esc(DEFINITIONS_VERSION)} &middot; last updated {esc(DEFINITIONS_UPDATED)}</p>
        {"".join(items)}
    </section>
    """


def make_card(row, index: int, thumb_dir: Path, review_dir: Path, download_thumbnails: bool) -> str:
    source = row.get("source", "")
    objid = int(row.get("objID"))
    ra, dec = row.get("ra"), row.get("dec")
    triage = row.get("triage_class", "untriaged")

    thumb_path = get_thumbnail(thumb_dir, source, objid, ra, dec, download_missing=download_thumbnails)
    thumb = Path(thumb_path).relative_to(review_dir).as_posix() if thumb_path else ""
    img_html = f'<img class="thumb" src="{esc(thumb)}" alt="SDSS thumbnail">' if thumb else '<div class="missing-thumb">No cached image</div>'

    return f"""
    <article class="card class-{esc(triage)}">
        <div class="card-header">
            <div>
                <h2>#{index:03d} — {esc(source)}:{objid}</h2>
                <p class="subtle">RA {fmt(ra, 6)} &nbsp; Dec {fmt(dec, 6)} &nbsp; Tile {esc(row.get('tile_id'))}</p>
            </div>
            <div class="score-box">
                <div class="score-main">{fmt(row.get('review_score'), 2)}</div>
                <div class="score-label">review score</div>
            </div>
        </div>

        <div class="card-body">
            <div class="image-panel">
                {img_html}
                <div class="links">
                    <a href="{esc(skyserver_url(objid))}" target="_blank">SkyServer</a>
                    <a href="{esc(sdss_stamp_url(ra, dec))}" target="_blank">SDSS JPEG</a>
                </div>
                {definitions_card()}
            </div>

            <div class="data-panel">
                <section>
                    <h3>Classification</h3>
<div class="class-line"><b>{esc(triage)}</b></div>
<div class="risk-line">
    Measurement risk: <b>{fmt(row.get('artefact_risk'), 2)}</b>
</div>
<div class="pill-row">{pill_list(row.get('triage_flags'))}</div>
                    <table class="compact">
                        <tr><th>Weirdness</th><td>{fmt(row.get('weirdness_score'), 3)}</td><th>Artefact risk</th><td>{fmt(row.get('artefact_risk'), 3)}</td></tr>
                        <tr><th>Anomaly score</th><td>{fmt(row.get('anomaly_score'), 6)}</td><th>Rank</th><td>{fmt(row.get('rank_in_run'), 0)}</td></tr>
                    </table>
                    <p><b>Crossmatch:</b><br>{crossmatch_summary(row)}</p>
                    <p><b>Status:</b> {esc(row.get('status') or 'unreviewed')} &nbsp; <b>Notes:</b> {esc(row.get('human_notes'))}</p>
                </section>

                <section>
                    <h3>Band profile</h3>
                    {band_profile(row)}
                </section>

                <section>
                    <h3>Colour profile</h3>
                    {colour_profile(row)}
                </section>

                <section>
                    <h3>Derived diagnostics</h3>
                    <table class="compact">
                        {table_rows(row, [
                            ('full red score', 'full_red_score', 3),
                            ('colour smoothness', 'colour_smoothness', 3),
                            ('colour jump max', 'colour_jump_max', 3),
                            ('PSF/radius', 'psf_per_radius', 3),
                            ('compactness proxy', 'compactness_proxy', 3),
                            ('SB offset', 'surface_brightness_offset', 3),
                        ])}
                    </table>
                </section>

                <section>
                    <h3>Catalogue values</h3>
                    <table class="compact">
                        <tr><th>u</th><td>{fmt(row.get('u'))}</td><th>g</th><td>{fmt(row.get('g'))}</td></tr>
                        <tr><th>r</th><td>{fmt(row.get('r'))}</td><th>i</th><td>{fmt(row.get('i'))}</td></tr>
                        <tr><th>z</th><td>{fmt(row.get('z'))}</td><th>mu_r</th><td>{fmt(row.get('mu_r'))}</td></tr>
                        <tr><th>PetroRad</th><td>{fmt(row.get('petroRad_r'))}</td><th>Concentration</th><td>{fmt(row.get('concentration_r'))}</td></tr>
                        <tr><th>R50</th><td>{fmt(row.get('petroR50_r'))}</td><th>R90</th><td>{fmt(row.get('petroR90_r'))}</td></tr>
                    </table>
                </section>
            </div>
        </div>
    </article>
    """


def histogram(values, bins=18):
    vals = [safe_float(v) for v in values]
    vals = [v for v in vals if v is not None and math.isfinite(v)]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1
    counts = [0] * bins
    for v in vals:
        idx = min(bins - 1, int((v - lo) / (hi - lo) * bins))
        counts[idx] += 1
    mx = max(counts) or 1
    bars = "".join(f'<span title="{c}" style="height:{8 + 52*c/mx:.1f}px"></span>' for c in counts)
    return f'<div class="hist">{bars}</div><div class="hist-label">{fmt(lo,2)} → {fmt(hi,2)}</div>'


def make_dashboard(df: pd.DataFrame) -> str:
    n = len(df)
    top_class = df['triage_class'].value_counts().idxmax() if n and 'triage_class' in df.columns else '—'
    mean_review = fmt(df['review_score'].mean() if n and 'review_score' in df.columns else None, 2)
    mean_risk = fmt(df['artefact_risk'].mean() if n and 'artefact_risk' in df.columns else None, 2)
    gaia = int(df.get('flag_gaia_matched', pd.Series(dtype=int)).sum()) if n else 0
    cat = int(df.get('flag_catalogued', pd.Series(dtype=int)).sum()) if n else 0

    counts_html = ""
    if n and 'triage_class' in df.columns:
        for cls, count in df['triage_class'].value_counts().items():
            pct = 100 * count / n
            counts_html += f'<div class="count-row"><span>{esc(cls)}</span><b>{count}</b><i style="width:{pct:.1f}%"></i></div>'

    return f"""
    <section class="dashboard">
        <div class="metrics">
            {metric_card('Candidates', n)}
            {metric_card('Mean review score', mean_review)}
            {metric_card('Mean artefact risk', mean_risk)}
            {metric_card('Top class', top_class)}
            {metric_card('Gaia matched', gaia)}
            {metric_card('Catalogued', cat)}
        </div>
        <div class="dash-grid">
            <div><h3>Review score histogram</h3>{histogram(df['review_score']) if 'review_score' in df else ''}</div>
            <div><h3>Artefact risk histogram</h3>{histogram(df['artefact_risk']) if 'artefact_risk' in df else ''}</div>
            <div><h3>Triage class counts</h3>{counts_html}</div>
        </div>
    </section>
    """


def make_html(df: pd.DataFrame, db_path: str, thumb_dir: Path, review_dir: Path,
              download_thumbnails: bool, max_cards: int | None = None) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    shown = df if max_cards is None else df.head(max_cards)
    cards = "\n".join(
        make_card(row, i, thumb_dir, review_dir, download_thumbnails)
        for i, (_, row) in enumerate(shown.iterrows(), start=1)
    )
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Candidate Review Pack</title>
<style>
:root {{
    --bg:#0f1117; --panel:#171a22; --panel2:#202532; --panel3:#11141c;
    --text:#e7eaf0; --muted:#a9b0c2; --line:#343b4d; --link:#8fc7ff;
    --accent:#ffd447; --bad:#ff6b6b; --good:#70c1b3;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:28px; font-family:Arial, Helvetica, sans-serif; background:var(--bg); color:var(--text); }}
h1 {{ margin:0; font-size:32px; }}
h2 {{ margin:0 0 4px 0; font-size:22px; }}
h3 {{ margin:0 0 10px 0; font-size:16px; border-bottom:1px solid var(--line); padding-bottom:6px; }}
a {{ color:var(--link); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
.subtle {{ color:var(--muted); margin:7px 0 22px; }}
.dashboard {{ margin:24px 0 28px; }}
.metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:16px; }}
.metric {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px; }}
.metric-value {{ font-size:26px; font-weight:bold; }} .metric-label {{ color:var(--muted); font-size:13px; margin-top:4px; }}
.dash-grid {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:14px; }}
.dash-grid > div, section {{ background:var(--panel2); border:1px solid var(--line); border-radius:10px; padding:12px; }}
.hist {{ height:74px; display:flex; align-items:end; gap:3px; border-bottom:1px solid var(--line); padding-top:8px; }}
.hist span {{ display:block; flex:1; background:#56617d; border-radius:3px 3px 0 0; min-height:4px; }}
.hist-label {{ color:var(--muted); font-size:12px; margin-top:5px; }}
.count-row {{ position:relative; display:grid; grid-template-columns:1fr auto; gap:10px; padding:7px 0; border-bottom:1px solid #2d3445; overflow:hidden; }}
.count-row i {{ position:absolute; left:0; bottom:0; height:2px; background:#56617d; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-left:9px solid var(--accent); border-radius:14px; padding:18px; margin:22px 0; box-shadow:0 8px 24px rgba(0,0,0,.25); }}
.class-artefact_risk {{ border-left-color:var(--bad); opacity:.94; }}
.class-possible_lsb {{ border-left-color:#70c1b3; }}
.class-compact_red {{ border-left-color:#ff9f1a; }}
.class-extreme_colour {{ border-left-color:#bd7bff; }}
.class-high_interest {{ border-left-color:#64ff8a; }}
.card-header {{ display:flex; justify-content:space-between; gap:20px; align-items:start; margin-bottom:14px; }}
.score-box {{ background:var(--panel2); border:1px solid var(--line); border-radius:12px; padding:10px 14px; text-align:center; min-width:110px; }}
.score-main {{ font-size:28px; font-weight:bold; }} .score-label {{ color:var(--muted); font-size:12px; }}
.card-body {{ display:grid; grid-template-columns:540px 1fr; gap:22px; align-items:start; }}
.thumb, .missing-thumb {{ width:512px; height:512px; object-fit:contain; background:#000; border:1px solid var(--line); border-radius:10px; }}
.missing-thumb {{ display:grid; place-items:center; color:var(--muted); }}
.links {{ margin-top:10px; display:flex; gap:14px; }}
.definitions {{ margin-top:14px; width:512px; }}
.definitions h3 {{ font-size:14px; }}
.def-item {{ padding:8px 0; border-bottom:1px solid #2d3445; }}
.def-item:last-child {{ border-bottom:none; }}
.def-head {{ font-size:14px; margin-bottom:2px; }}
.def-what {{ color:var(--muted); font-size:13px; }}
.definitions details {{ margin-top:5px; }}
.definitions summary {{ color:var(--link); font-size:12px; cursor:pointer; }}
.definitions summary:hover {{ text-decoration:underline; }}
.def-formula, .def-why, .def-interpret {{ font-size:12px; color:var(--muted); margin-top:5px; line-height:1.5; }}
.def-formula {{ font-family:Consolas, monospace; }}
.data-panel section {{ margin-bottom:12px; }}
.pill-row {{ display:flex; gap:7px; flex-wrap:wrap; margin:8px 0 10px; }}
.pill {{ background:#30384c; border:1px solid #46506a; border-radius:999px; padding:4px 9px; font-size:13px; }}
.class-line {{ font-size:18px; margin-bottom:6px; }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
th {{ text-align:left; color:var(--muted); font-weight:normal; padding:6px 8px 6px 0; }}
td {{ padding:6px 14px 6px 0; border-bottom:1px solid #2d3445; font-family:Consolas, monospace; }}
.compact th {{ width:24%; }}
.bar-row {{ display:grid; grid-template-columns:38px 1fr 58px; gap:10px; align-items:center; margin:7px 0; font-size:13px; }}
.bar {{ height:12px; background:var(--panel3); border:1px solid var(--line); border-radius:999px; overflow:hidden; }}
.bar i {{ display:block; height:100%; background:#65718e; }}
.bar-row b {{ font-family:Consolas, monospace; font-weight:normal; color:var(--text); }}
@media (max-width:1100px) {{ .card-body, .dash-grid {{ grid-template-columns:1fr; }} .thumb,.missing-thumb,.definitions {{ width:100%; max-width:512px; }} }}
</style>
</head>
<body>
<h1>Candidate Review Pack</h1>
<p class="subtle">Generated {esc(timestamp)} from {esc(db_path)} — {len(df)} candidates sorted by review_score.</p>
{make_dashboard(df)}
{cards}
</body>
</html>"""


def build_review(
    db_path: str,
    output_html: Path,
    review_dir: Path,
    limit: int = 500,
    min_review_score: float | None = None,
    download_thumbnails: bool = True,
    fresh: bool = False,
    max_cards: int | None = None,
) -> pd.DataFrame:
    thumb_dir = review_dir / "thumbnails"

    if fresh and review_dir.exists():
        # Never wipe the thumbnail cache -- thumbnails should only ever be
        # downloaded if they haven't been downloaded before. --fresh only
        # clears other review_pack artifacts (review.html, stray CSVs, etc).
        for child in review_dir.iterdir():
            if child == thumb_dir:
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        print("Cleared old review_pack folder (kept cached thumbnails).")

    review_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)

    df = load_candidates(db_path, limit=limit, min_review_score=min_review_score)
    if df.empty:
        print("No triaged candidates found. Run score_candidates.py first.")

    output_html.parent.mkdir(parents=True, exist_ok=True)
    html_text = make_html(df, db_path=db_path, thumb_dir=thumb_dir, review_dir=review_dir,
                           download_thumbnails=download_thumbnails, max_cards=max_cards)
    output_html.write_text(html_text, encoding="utf-8")
    return df


def main():
    parser = argparse.ArgumentParser(
        description="Build review_pack/review.html directly from survey.db. "
                    "Run score_candidates.py first so triage scores exist to sort/render."
    )
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--output", "-o", type=Path, default=DEFAULT_REVIEW_DIR / "review.html")
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--limit", type=int, default=500, help="Max candidates to include, by review_score.")
    parser.add_argument("--min-review-score", type=float, default=None)
    parser.add_argument("--fresh", action="store_true", help="Delete old review_pack before building.")
    parser.add_argument("--no-thumbnails", action="store_true",
                        help="Cache-only mode: use existing thumbnails, but do not download missing ones.")
    parser.add_argument("--max-cards", type=int, default=None, help="Optionally show only top N cards in HTML.")
    args = parser.parse_args()

    df = build_review(
        db_path=args.db,
        output_html=args.output,
        review_dir=args.review_dir,
        limit=args.limit,
        min_review_score=args.min_review_score,
        download_thumbnails=not args.no_thumbnails,
        fresh=args.fresh,
        max_cards=args.max_cards,
    )

    print("\n=== REVIEW BUILT ===")
    print(f"DB        : {args.db}")
    print(f"HTML      : {args.output}")
    print(f"Cards     : {len(df) if args.max_cards is None else min(len(df), args.max_cards)}")


if __name__ == "__main__":
    main()
