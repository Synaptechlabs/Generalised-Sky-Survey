# ---------------------------------------------------------------------------
# File:        build_review.py
# Version:     0.5
# Date:        2026-07-14
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
from wise_cutouts import get_wise_cutouts, wise_viewer_url
from rank_tracking import latest_cycle, new_entrants
from scan_tile_wise import backfill_coadd_id

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
            x.wise_match, x.wise_objID, x.wise_dist, x.wise_w1_w2,

            wo.w1mpro AS wise_w1mpro, wo.w1sigmpro AS wise_w1sigmpro,
            wo.w2mpro AS wise_w2mpro, wo.w2sigmpro AS wise_w2sigmpro,
            wo.w3mpro AS wise_w3mpro, wo.w3sigmpro AS wise_w3sigmpro,
            wo.w4mpro AS wise_w4mpro, wo.w4sigmpro AS wise_w4sigmpro,
            wo.coadd_id AS wise_coadd_id,
            wf.w2_w3 AS wise_w2_w3,

            rv.status, rv.priority AS human_priority, rv.human_notes,

            t.definition_version,
            t.weirdness_score, t.artefact_risk, t.review_score,
            t.triage_class, t.triage_flags,
            t.full_red_score, t.colour_smoothness, t.colour_jump_max,
            t.psf_per_radius, t.compactness_proxy, t.surface_brightness_offset,
            t.flag_gaia_matched, t.flag_catalogued, t.flag_wise_red_excess
        FROM candidates c
        JOIN objects o ON o.source = c.source AND o.objID = c.objID
        JOIN features f ON f.source = c.source AND f.objID = c.objID
        LEFT JOIN crossmatches x ON x.source = c.source AND x.objID = c.objID
        LEFT JOIN objects wo ON wo.source = 'wise' AND wo.objID = x.wise_objID
        LEFT JOIN features wf ON wf.source = 'wise' AND wf.objID = x.wise_objID
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


def entrant_label(candidate_id, entrants) -> str:
    """
    entrants is {candidate_id: is_new_candidate} from rank_tracking.
    new_entrants() for the latest recorded scan_cycle -- not a triage flag
    (not versioned/stored in the triage table), computed fresh at build
    time. Not in entrants at all means "not a new top-N entrant this
    cycle", same as any other candidate re-ranking within an already-
    stable set. Returns "" in that case.
    """
    if candidate_id not in entrants:
        return ""
    return "new_candidate" if entrants[candidate_id] else "climbed_top50"


def entrant_badge(candidate_id, entrants):
    label = entrant_label(candidate_id, entrants)
    if not label:
        return ""
    return f'<span class="pill pill-entrant">{esc(label)}</span>'


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
    # WISE is deliberately not repeated here -- see wise_panel(), shown in
    # the image panel alongside the thumbnail instead of this text summary.
    return "<br>".join(parts) if parts else "No catalogue match recorded"


def wise_cutout_row(row, thumb_dir: Path, review_dir: Path, download_thumbnails: bool, con) -> str:
    if not safe_float(row.get("wise_match"), 0):
        return ""
    wise_objid = row.get("wise_objID")
    if wise_objid is None or pd.isna(wise_objid):
        return ""

    coadd_id = row.get("wise_coadd_id")
    if (not coadd_id or pd.isna(coadd_id)) and download_thumbnails:
        # Atomic data check: this object was ingested before coadd_id was
        # tracked (see scan_tile_wise.py). A single-object TAP lookup is
        # cheap and precise -- no need to wait for/trigger a tile rescan
        # just to render one card's imagery.
        coadd_id = backfill_coadd_id(con, int(wise_objid))
    if not coadd_id or pd.isna(coadd_id):
        return ""

    w1_path, w2_path, composite_path = get_wise_cutouts(
        thumb_dir, wise_objid, coadd_id, row.get("ra"), row.get("dec"),
        download_missing=download_thumbnails,
    )
    if not any((w1_path, w2_path, composite_path)):
        return ""

    def cutout_img(path, label):
        if not path:
            return f'<div class="wise-cutout"><div class="missing-thumb">No image</div><span>{esc(label)}</span></div>'
        rel = Path(path).relative_to(review_dir).as_posix()
        return f'<div class="wise-cutout"><img src="{esc(rel)}" alt="WISE {esc(label)} cutout"><span>{esc(label)}</span></div>'

    return f"""
    <div class="wise-cutout-row">
        {cutout_img(w1_path, "W1")}
        {cutout_img(w2_path, "W2")}
        {cutout_img(composite_path, "W1+W2")}
    </div>
    """


def wise_panel(row):
    if not safe_float(row.get("wise_match"), 0):
        return ""

    bands = [
        ("W1", row.get("wise_w1mpro"), row.get("wise_w1sigmpro")),
        ("W2", row.get("wise_w2mpro"), row.get("wise_w2sigmpro")),
        ("W3", row.get("wise_w3mpro"), row.get("wise_w3sigmpro")),
        ("W4", row.get("wise_w4mpro"), row.get("wise_w4sigmpro")),
    ]
    band_rows = "".join(
        f"<tr><th>{b}</th><td>{fmt(mag, 3)}{f' &plusmn; {fmt(err, 3)}' if safe_float(err) is not None else ''}</td></tr>"
        for b, mag, err in bands
        if safe_float(mag) is not None
    )

    red_excess = safe_float(row.get("flag_wise_red_excess"), 0)
    excess_pill = '<span class="pill">wise_red_excess</span>' if red_excess else ""

    return f"""
    <div class="wise-panel">
        <h3>WISE crossmatch {excess_pill}</h3>
        <p class="subtle" style="margin:0 0 8px">objID {esc(row.get('wise_objID'))} &middot; {fmt(row.get('wise_dist'), 3)} arcsec</p>
        <table class="compact">
            {band_rows}
            <tr><th>W1-W2</th><td>{fmt(row.get('wise_w1_w2'), 3)}</td></tr>
            <tr><th>W2-W3</th><td>{fmt(row.get('wise_w2_w3'), 3)}</td></tr>
        </table>
    </div>
    """


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


def make_card(row, index: int, thumb_dir: Path, review_dir: Path, download_thumbnails: bool, entrants: dict, con) -> str:
    source = row.get("source", "")
    objid = int(row.get("objID"))
    ra, dec = row.get("ra"), row.get("dec")
    triage = row.get("triage_class", "untriaged")
    candidate_id = int(row.get("candidate_id"))

    thumb_path = get_thumbnail(thumb_dir, source, objid, ra, dec, download_missing=download_thumbnails)
    thumb = Path(thumb_path).relative_to(review_dir).as_posix() if thumb_path else ""
    img_html = f'<img class="thumb" src="{esc(thumb)}" alt="SDSS thumbnail">' if thumb else '<div class="missing-thumb">No cached image</div>'

    search_blob = f"{source}:{objid} {row.get('tile_id') or ''}".lower()
    entrant = entrant_label(candidate_id, entrants)

    return f"""
    <article class="card class-{esc(triage)}" data-class="{esc(triage)}" data-search="{esc(search_blob)}" data-entrant="{esc(entrant)}">
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
                    <a href="{esc(wise_viewer_url(ra, dec))}" target="_blank">IRSA WISE viewer</a>
                </div>
                {wise_cutout_row(row, thumb_dir, review_dir, download_thumbnails, con)}
                {wise_panel(row)}
                {definitions_card()}
            </div>

            <div class="data-panel">
                <section>
                    <h3>Classification</h3>
<div class="class-line"><b>{esc(triage)}</b></div>
<div class="risk-line">
    Measurement risk: <b>{fmt(row.get('artefact_risk'), 2)}</b>
</div>
<div class="pill-row">{pill_list(row.get('triage_flags'))}{entrant_badge(candidate_id, entrants)}</div>
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
    wise_red = int(df.get('flag_wise_red_excess', pd.Series(dtype=int)).sum()) if n else 0

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
            {metric_card('WISE red excess', wise_red)}
        </div>
        <div class="dash-grid">
            <div><h3>Review score histogram</h3>{histogram(df['review_score']) if 'review_score' in df else ''}</div>
            <div><h3>Artefact risk histogram</h3>{histogram(df['artefact_risk']) if 'artefact_risk' in df else ''}</div>
            <div><h3>Triage class counts</h3>{counts_html}</div>
        </div>
    </section>
    """


def make_html(df: pd.DataFrame, db_path: str, thumb_dir: Path, review_dir: Path,
              download_thumbnails: bool, entrants: dict, con, max_cards: int | None = None) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    shown = df if max_cards is None else df.head(max_cards)
    cards = "\n".join(
        make_card(row, i, thumb_dir, review_dir, download_thumbnails, entrants, con)
        for i, (_, row) in enumerate(shown.iterrows(), start=1)
    )
    classes = sorted(shown["triage_class"].dropna().unique()) if "triage_class" in shown.columns and len(shown) else []
    class_buttons = "".join(
        f'<button class="filter-btn" data-filter="{esc(c)}">{esc(c)}</button>' for c in classes
    )
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Object Catalogue</title>
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
.links {{ margin-top:10px; display:flex; gap:14px; flex-wrap:wrap; }}
.wise-cutout-row {{ margin-top:14px; width:512px; display:flex; gap:8px; }}
.wise-cutout {{ flex:1; text-align:center; }}
.wise-cutout img, .wise-cutout .missing-thumb {{ width:100%; aspect-ratio:1/1; object-fit:contain; background:#000; border:1px solid var(--line); border-radius:8px; display:block; }}
.wise-cutout .missing-thumb {{ display:grid; place-items:center; color:var(--muted); font-size:11px; }}
.wise-cutout span {{ display:block; margin-top:4px; font-size:11px; color:var(--muted); }}
.wise-panel {{ margin-top:14px; width:512px; }}
.wise-panel h3 {{ font-size:14px; display:flex; gap:8px; align-items:center; }}
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
.pill-entrant {{ background:#4a3d10; border-color:var(--accent); color:var(--accent); }}
.class-line {{ font-size:18px; margin-bottom:6px; }}
.filter-bar {{ display:flex; gap:14px; flex-wrap:wrap; align-items:center; background:var(--panel2); border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin:0 0 22px; }}
#search {{ background:var(--panel); border:1px solid var(--line); color:var(--text); border-radius:8px; padding:8px 12px; font-size:14px; min-width:240px; }}
.filter-group {{ display:flex; gap:8px; flex-wrap:wrap; }}
.filter-btn {{ background:var(--panel); border:1px solid var(--line); color:var(--muted); border-radius:999px; padding:6px 14px; font-size:13px; cursor:pointer; }}
.filter-btn.active {{ background:var(--accent); border-color:var(--accent); color:#171a22; }}
.filter-count {{ color:var(--muted); font-size:13px; }}
.pager {{ display:flex; gap:10px; align-items:center; justify-content:center; margin:26px 0; }}
.pager button {{ background:var(--panel); border:1px solid var(--line); color:var(--text); border-radius:8px; padding:8px 16px; font-size:14px; cursor:pointer; }}
.pager button:disabled {{ color:var(--muted); cursor:default; opacity:.5; }}
.pager-info {{ color:var(--muted); font-size:13px; }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
th {{ text-align:left; color:var(--muted); font-weight:normal; padding:6px 8px 6px 0; }}
td {{ padding:6px 14px 6px 0; border-bottom:1px solid #2d3445; font-family:Consolas, monospace; }}
.compact th {{ width:24%; }}
.bar-row {{ display:grid; grid-template-columns:38px 1fr 58px; gap:10px; align-items:center; margin:7px 0; font-size:13px; }}
.bar {{ height:12px; background:var(--panel3); border:1px solid var(--line); border-radius:999px; overflow:hidden; }}
.bar i {{ display:block; height:100%; background:#65718e; }}
.bar-row b {{ font-family:Consolas, monospace; font-weight:normal; color:var(--text); }}
@media (max-width:1100px) {{ .card-body, .dash-grid {{ grid-template-columns:1fr; }} .thumb,.missing-thumb,.wise-cutout-row,.wise-panel,.definitions {{ width:100%; max-width:512px; }} }}
</style>
</head>
<body>
<h1>Object Catalogue</h1>
<p class="subtle">Generated {esc(timestamp)} from the pipeline database — {len(df)} candidates sorted by review_score.</p>
{make_dashboard(df)}

<div class="filter-bar">
    <input type="text" id="search" placeholder="Search source:objID or tile...">
    <div class="filter-group" id="classFilters">
        <button class="filter-btn active" data-filter="all">All classes</button>
        {class_buttons}
    </div>
    <button class="filter-btn" id="entrantToggle">New/climbed only</button>
    <span class="filter-count" id="filterCount"></span>
</div>

<div class="pager" id="pagerTop"></div>
<div id="cardContainer">
{cards}
</div>
<div class="pager" id="pagerBottom"></div>

<script>
(function () {{
    var PAGE_SIZE = 50;
    var cards = Array.prototype.slice.call(document.querySelectorAll('#cardContainer .card'));
    var search = document.getElementById('search');
    var classButtons = Array.prototype.slice.call(document.querySelectorAll('#classFilters .filter-btn'));
    var entrantToggle = document.getElementById('entrantToggle');
    var filterCount = document.getElementById('filterCount');
    var pagerTop = document.getElementById('pagerTop');
    var pagerBottom = document.getElementById('pagerBottom');

    var state = {{ activeClass: 'all', entrantOnly: false, page: 1 }};

    function matches(card) {{
        var classOk = state.activeClass === 'all' || card.dataset.class === state.activeClass;
        var q = search.value.trim().toLowerCase();
        var searchOk = !q || card.dataset.search.indexOf(q) !== -1;
        var entrantOk = !state.entrantOnly || !!card.dataset.entrant;
        return classOk && searchOk && entrantOk;
    }}

    function renderPager(container, totalPages) {{
        container.innerHTML = '';
        if (totalPages <= 1) return;
        var prev = document.createElement('button');
        prev.textContent = '← Prev';
        prev.disabled = state.page <= 1;
        prev.onclick = function () {{ state.page -= 1; render(); window.scrollTo(0, 0); }};
        var info = document.createElement('span');
        info.className = 'pager-info';
        info.textContent = 'Page ' + state.page + ' of ' + totalPages;
        var next = document.createElement('button');
        next.textContent = 'Next →';
        next.disabled = state.page >= totalPages;
        next.onclick = function () {{ state.page += 1; render(); window.scrollTo(0, 0); }};
        container.appendChild(prev);
        container.appendChild(info);
        container.appendChild(next);
    }}

    function render() {{
        var filtered = cards.filter(matches);
        var totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
        if (state.page > totalPages) state.page = totalPages;
        if (state.page < 1) state.page = 1;

        var start = (state.page - 1) * PAGE_SIZE;
        var end = start + PAGE_SIZE;
        var visible = filtered.slice(start, end);
        var visibleSet = new Set(visible);

        cards.forEach(function (c) {{ c.style.display = visibleSet.has(c) ? '' : 'none'; }});

        filterCount.textContent = filtered.length
            ? 'Showing ' + (start + 1) + '-' + Math.min(end, filtered.length) + ' of ' + filtered.length
                + ' (' + cards.length + ' total)'
            : '0 candidates match (' + cards.length + ' total)';

        renderPager(pagerTop, totalPages);
        renderPager(pagerBottom, totalPages);
    }}

    classButtons.forEach(function (b) {{
        b.addEventListener('click', function () {{
            classButtons.forEach(function (x) {{ x.classList.remove('active'); }});
            b.classList.add('active');
            state.activeClass = b.dataset.filter;
            state.page = 1;
            render();
        }});
    }});

    entrantToggle.addEventListener('click', function () {{
        state.entrantOnly = !state.entrantOnly;
        entrantToggle.classList.toggle('active', state.entrantOnly);
        state.page = 1;
        render();
    }});

    search.addEventListener('input', function () {{ state.page = 1; render(); }});

    render();
}})();
</script>
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

    con = connect(db_path)
    try:
        cycle = latest_cycle(con)
        entrants = new_entrants(con, cycle) if cycle is not None else {}

        output_html.parent.mkdir(parents=True, exist_ok=True)
        html_text = make_html(df, db_path=db_path, thumb_dir=thumb_dir, review_dir=review_dir,
                               download_thumbnails=download_thumbnails, entrants=entrants,
                               con=con, max_cards=max_cards)
    finally:
        con.close()

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
