# ---------------------------------------------------------------------------
# File:        build_landing.py
# Version:     0.5
# Date:        2026-07-18
# Author:      Scott Douglass
# Description: Builds landing.html -- a live stats dashboard pulled
#              directly from survey.db -- plus about.html, a static page
#              covering what GSS is and how the pipeline works (moved out
#              of landing.html so the dashboard page stays focused on
#              current state). Same visual theme as build_review.py, but a
#              fully separate, self-contained script -- no shared module
#              between the two.
# ---------------------------------------------------------------------------
import argparse
import html
import sqlite3
from datetime import datetime
from pathlib import Path

from global_features import GLOBAL_FEATURE_COLS
from triage import DEFINITIONS_VERSION, DEFINITIONS_UPDATED

DEFAULT_DB = "survey.db"
DEFAULT_OUTPUT = Path("landing.html")
TOTAL_SKY_TILES = 64800

THEME_CSS = """
:root {
    --bg:#0f1117; --panel:#171a22; --panel2:#202532; --panel3:#11141c;
    --text:#e7eaf0; --muted:#a9b0c2; --line:#343b4d; --link:#8fc7ff;
    --accent:#ffd447; --bad:#ff6b6b; --good:#70c1b3;
}
* { box-sizing:border-box; }
body { margin:0; padding:28px; font-family:Arial, Helvetica, sans-serif; background:var(--bg); color:var(--text); max-width:1200px; margin-left:auto; margin-right:auto; }
h1 { margin:0; font-size:26px; font-weight:normal; }
h2 { margin:36px 0 14px; font-size:20px; font-weight:normal; border-bottom:1px solid var(--line); padding-bottom:8px; }
h3 { margin:0 0 10px 0; font-size:15px; font-weight:normal; border-bottom:1px solid var(--line); padding-bottom:6px; }
a { color:var(--link); text-decoration:none; } a:hover { text-decoration:underline; }
.subtitle { color:var(--muted); margin:8px 0 4px; font-size:14px; }
.subtle { color:var(--muted); margin:7px 0 22px; }
.hero { padding-bottom:14px; border-bottom:1px solid var(--line); margin-bottom:8px; }
.hero .links { display:flex; gap:18px; flex-wrap:wrap; }
ul.principles { margin:0; padding-left:20px; line-height:1.7; color:var(--muted); }
.metrics { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:16px; }
.metric { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px; }
.metric-value { font-size:26px; font-weight:bold; } .metric-label { color:var(--muted); font-size:13px; margin-top:4px; }
.dash-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.dash-grid > div, section { background:var(--panel2); border:1px solid var(--line); border-radius:10px; padding:12px; }
.count-row { position:relative; display:grid; grid-template-columns:1fr auto; gap:10px; padding:7px 0; border-bottom:1px solid #2d3445; overflow:hidden; }
.count-row i { position:absolute; left:0; bottom:0; height:2px; background:#56617d; }
.source-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:16px; }
.skymap-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(420px,1fr)); gap:16px; margin-bottom:20px; }
.skymap-item img { width:100%; height:auto; border:1px solid var(--line); border-radius:8px; display:block; }
.source-card { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px; }
.source-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }
.source-badge { font-weight:bold; font-size:14px; }
.source-pct { color:var(--muted); font-size:13px; }
.tile-bar { height:10px; border-radius:999px; overflow:hidden; display:flex; background:var(--panel3); border:1px solid var(--line); margin-bottom:12px; }
.tile-bar i { display:block; height:100%; }
.seg-complete { background:var(--good); }
.seg-no-coverage { background:#56617d; }
.seg-pending { background:#3a4257; }
.seg-failed { background:var(--bad); }
.legend { display:flex; gap:14px; flex-wrap:wrap; font-size:12px; color:var(--muted); margin:8px 0 20px; }
.legend span { display:inline-flex; align-items:center; gap:5px; }
.legend i { width:10px; height:10px; border-radius:3px; display:inline-block; }
table { width:100%; border-collapse:collapse; font-size:14px; }
th { text-align:left; color:var(--muted); font-weight:normal; padding:6px 8px 6px 0; }
td { padding:6px 14px 6px 0; border-bottom:1px solid #2d3445; font-family:Consolas, monospace; }
.compact th { width:55%; }
footer { margin-top:40px; padding-top:16px; border-top:1px solid var(--line); color:var(--muted); font-size:13px; }
p { line-height:1.6; }
code { background:var(--panel3); padding:1px 5px; border-radius:4px; font-size:13px; }
.quality-cuts { margin:10px 0 0; padding-left:20px; line-height:1.7; color:var(--muted); font-size:14px; }
@media (max-width:900px) { .dash-grid { grid-template-columns:1fr; } }
"""


def esc(x) -> str:
    if x is None:
        return ""
    return html.escape(str(x))


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def fetch_all(con, query, params=()):
    return [dict(r) for r in con.execute(query, params).fetchall()]


def fetch_one(con, query, params=()):
    row = con.execute(query, params).fetchone()
    return dict(row) if row else {}


def source_summary(con):
    objects = {r["source"]: r["n"] for r in fetch_all(con, "SELECT source, COUNT(*) as n FROM objects GROUP BY source")}
    candidates = {r["source"]: r["n"] for r in fetch_all(con, "SELECT source, COUNT(*) as n FROM candidates GROUP BY source")}
    triaged = {r["source"]: r["n"] for r in fetch_all(con, """
        SELECT c.source, COUNT(*) as n FROM triage t
        JOIN candidates c ON c.candidate_id = t.candidate_id
        GROUP BY c.source
    """)}
    sources = sorted(set(objects) | set(candidates) | set(triaged))
    return [
        {
            "source": s,
            "objects": objects.get(s, 0),
            "candidates": candidates.get(s, 0),
            "triaged": triaged.get(s, 0),
        }
        for s in sources
    ]


def tile_progress(con):
    rows = fetch_all(con, "SELECT source, status, COUNT(*) as n FROM tile_scans GROUP BY source, status")
    total_tiles = con.execute("SELECT COUNT(*) FROM sky_tiles").fetchone()[0] or TOTAL_SKY_TILES

    by_source = {}
    for r in rows:
        by_source.setdefault(r["source"], {})[r["status"]] = r["n"]

    result = []
    for source, statuses in sorted(by_source.items()):
        complete = statuses.get("complete", 0)
        no_coverage = statuses.get("no_coverage", 0)
        pending = statuses.get("pending", 0)
        failed = statuses.get("failed", 0)
        running = statuses.get("running", 0)
        resolved = complete + no_coverage
        result.append({
            "source": source,
            "total": total_tiles,
            "complete": complete,
            "no_coverage": no_coverage,
            "pending": pending,
            "failed": failed,
            "running": running,
            "pct_resolved": 100 * resolved / total_tiles if total_tiles else 0,
            "pct_complete": 100 * complete / total_tiles if total_tiles else 0,
        })
    return result


def triage_class_distribution(con):
    return fetch_all(con, "SELECT triage_class, COUNT(*) as n FROM triage GROUP BY triage_class ORDER BY n DESC")


def triage_overview(con):
    return fetch_one(con, "SELECT COUNT(*) as n, AVG(review_score) as avg_review, AVG(artefact_risk) as avg_risk FROM triage")


def crossmatch_overview(con):
    return fetch_one(con, """
        SELECT COUNT(*) as n, SUM(simbad_match) as simbad, SUM(ned_match) as ned, SUM(gaia_match) as gaia,
               SUM(wise_match) as wise
        FROM crossmatches
    """)


def review_status_summary(con):
    return fetch_all(con, "SELECT status, COUNT(*) as n FROM reviews GROUP BY status ORDER BY n DESC")


def recent_runs(con, limit=12):
    return fetch_all(con, """
        SELECT run_id, run_type, status, started_at, finished_at, notes
        FROM runs ORDER BY run_id DESC LIMIT ?
    """, (limit,))


def db_file_stats(db_path):
    p = Path(db_path)
    size_mb = p.stat().st_size / (1024 * 1024)
    mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    return {"size_mb": size_mb, "mtime": mtime}


def metric_card(label, value):
    return f'<div class="metric"><div class="metric-value">{esc(value)}</div><div class="metric-label">{esc(label)}</div></div>'


def count_bar_row(label, count, total, color=None):
    pct = 100 * count / total if total else 0
    style = f' style="background:{color}"' if color else ""
    return (
        f'<div class="count-row"><span>{esc(label)}</span><b>{count:,}</b>'
        f'<i{style} style="width:{pct:.2f}%"></i></div>'
    )


def source_card(s, tiles_by_source):
    tp = tiles_by_source.get(s["source"], {})
    total = tp.get("total", TOTAL_SKY_TILES)
    complete = tp.get("complete", 0)
    no_coverage = tp.get("no_coverage", 0)
    pending = tp.get("pending", 0)
    failed = tp.get("failed", 0)
    pct_resolved = tp.get("pct_resolved", 0)

    note = ""
    if s["source"] == "wise":
        note = (
            '<p class="subtle" style="margin:0 0 10px">Reference dataset only -- '
            "not scored by the Isolation Forest, so candidates/triaged are always "
            "0 by design. Reached via a local crossmatch join instead (see "
            "Crossmatch coverage below).</p>"
        )

    return f"""
    <div class="source-card">
        <div class="source-head">
            <span class="source-badge">{esc(s['source'])}</span>
            <span class="source-pct">{pct_resolved:.1f}% tiles resolved</span>
        </div>
        <div class="tile-bar">
            <i class="seg-complete" style="width:{100*complete/total if total else 0:.2f}%"></i><i class="seg-no-coverage" style="width:{100*no_coverage/total if total else 0:.2f}%"></i><i class="seg-pending" style="width:{100*pending/total if total else 0:.2f}%"></i><i class="seg-failed" style="width:{100*failed/total if total else 0:.2f}%"></i>
        </div>
        {note}
        <table class="compact">
            <tr><th>Objects scanned</th><td>{s['objects']:,}</td></tr>
            <tr><th>Candidates flagged</th><td>{s['candidates']:,}</td></tr>
            <tr><th>Candidates triaged</th><td>{s['triaged']:,}</td></tr>
            <tr><th>Tiles complete</th><td>{complete:,} / {total:,}</td></tr>
            <tr><th>Tiles no coverage</th><td>{no_coverage:,}</td></tr>
            <tr><th>Tiles pending</th><td>{pending:,}</td></tr>
            <tr><th>Tiles failed</th><td>{failed:,}</td></tr>
        </table>
    </div>
    """


def make_about_html() -> str:
    """
    Static -- no survey.db read. What GSS is and how the pipeline works,
    split out of make_html() (2026-07-18) so the dashboard page stays
    focused on current state and this explanatory content isn't rebuilt
    (and re-diffed) on every run for no reason.
    """
    global_cols_str = ", ".join(GLOBAL_FEATURE_COLS)

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>GSS — About</title>
<style>{THEME_CSS}</style>
</head>
<body>

<div class="hero">
    <h1>GSS — Generalised Sky Survey</h1>
    <div class="links">
        <a href="index.html">← Overview</a>
        <a href="review_pack/review.html">Object Catalogue →</a>
    </div>
</div>

<h2>Introduction</h2>
<section>
<p>GSS scans photometric survey catalogues on a per-tile basis, computes derived features from the raw measurements, and applies an Isolation Forest to identify objects with atypical feature values relative to the observed population. Identified candidates are cross-matched against SIMBAD, NED, Gaia, and WISE, assigned a set of derived diagnostic scores, and compiled for manual review.</p>
<p>The pipeline ingests three catalogues: SDSS (photometric imaging), Gaia DR3 (astrometry and photometry), and AllWISE (infrared photometry). Each is stored under a distinct source label, with independent tile-scan tracking per source. SDSS and Gaia are scored for anomalies; AllWISE is ingested the same way but serves only as a local crossmatch reference (see Section 5).</p>
<p>SDSS and Gaia report different quantities (SDSS: ugriz photometry and morphological parameters; Gaia: G/BP/RP photometry, parallax, proper motion). The anomaly-detection model is therefore restricted to two features computable identically from either source's photometry: the magnitude difference between the bluest and reddest available band, and the largest single adjacent-band colour difference. The Isolation Forest is fit once, across all SDSS/Gaia objects, using only these two features. Source-specific measurements (SDSS morphology, Gaia astrometry) are retained for diagnostic scoring but do not enter the anomaly model.</p>
</section>

<h2>Methodology</h2>
<section>

<h3>1. Sky tiling</h3>
<p>The sky is divided into 64,800 fixed 1°×1° tiles (360 steps in RA, 180 in Dec). Tile geometry (<code>sky_tiles</code>) is source-independent; scan progress (<code>tile_scans</code>) is recorded per (tile, source) pair, so a given tile's status is tracked separately for each catalogue. Tiles are scanned in order of increasing distance from the celestial equator (<code>ORDER BY ABS(dec_min) ASC</code>), reflecting the coverage geometry of ground-based optical surveys. Each (tile, source) pair takes one of five states: <code>pending</code>, <code>running</code>, <code>complete</code> (data found), <code>no_coverage</code> (no data at this position for this source; not retried), or <code>failed</code> (a query or processing error; retried).</p>

<h3>2. Per-source ingestion and quality cuts</h3>
<p>SDSS data are queried from <code>PhotoObj</code> via the DR17 SkyServer (Abdurro'uf et al. 2022), restricted to <code>clean=1 AND type=3</code> (galaxies), with <code>u/g/r/i/z</code> magnitudes in approximately 10–25 (r: 10–22) and positive Petrosian radii. Rows are excluded if:</p>
<ul class="quality-cuts">
    <li>any of <code>psfMagErr_u/g/r/i/z</code> ≥ 0.5 mag,</li>
    <li><code>petroRad_r</code> or <code>petroR50_r</code> ≤ 0.2 arcsec, or</li>
    <li><code>petroR90_r</code> ≤ <code>petroR50_r</code>.</li>
</ul>
<p>Gaia data are queried from <code>gaiadr3.gaia_source</code> via the Gaia archive TAP service (Gaia Collaboration, Vallenari et al. 2023), restricted to the tile's RA/Dec bounds with non-null <code>phot_g_mean_mag</code>, <code>phot_bp_mean_mag</code>, and <code>phot_rp_mean_mag</code>. Rows are excluded if <code>phot_g_mean_mag</code> ≤ 0 or <code>parallax_error</code> ≤ 0.</p>
<p>AllWISE data (Cutri et al. 2013) are queried from <code>allwise_p3as_psd</code> via IRSA's TAP service, restricted to the tile's RA/Dec bounds with non-null W1/W2 profile-fit magnitudes. Rows are excluded if either band's magnitude uncertainty ≥ 0.5 mag or either band's contamination/confusion flag is non-zero. W3/W4 are recorded when present but not required, since AllWISE detects them far less often than W1/W2.</p>

<h3>3. Anomaly detection</h3>
<p>Before fitting, each source's values for the two shared features are normalised independently per source. For a feature value <var>x</var> with per-source median <var>m</var> and median absolute deviation <var>MAD</var>, the normalised value is (<var>x</var> − <var>m</var>) / <var>MAD</var>, falling back to the sample standard deviation when <var>MAD</var> is degenerate (≈0 — typically too few rows scanned yet for that source). Normalising against the pooled population instead of per source would let whichever source has the larger scale or denser sampling dominate the fit (Gaia currently contributes several times more rows than SDSS), so an object's anomaly score would partly reflect which catalogue it came from rather than genuine rarity within that catalogue.</p>
<p>A single scikit-learn <code>IsolationForest</code> (Liu, Ting &amp; Zhou 2008; <code>n_estimators=500</code>, <code>max_samples=256</code> — the subsample size the original paper found sufficient for isolation to emerge independent of dataset size, <code>random_state=42</code>) is fit against the normalised values of all SDSS/Gaia objects scanned to date, using only the two shared features described above (<code>{esc(global_cols_str)}</code>), and is not fit separately per source. <code>anomaly_score</code> is the output of <code>score_samples()</code>; more negative values indicate greater isolation from the combined population. <code>contamination</code> is not used, as GSS calls only <code>score_samples()</code> and never <code>predict()</code> or <code>decision_function()</code>.</p>
<p>The model is refit from scratch at every tile scan, against whatever population has accumulated so far — it is not cached or persisted between scans. <code>anomaly_score</code> is therefore a function of survey state at scan time, not a fixed per-object quantity: the same object would generally receive a different score if it were rescored later in the survey's history, as the reference population it's compared against grows. Candidate selection (the <code>top_n</code> most isolated objects per tile) is likewise a per-tile operation against this evolving population, not a globally maintained ranking. Section 6 covers how the review pack accounts for this when surfacing what's newly worth attention.</p>
<p>Source-specific measurements — SDSS morphology (concentration, surface brightness, PSF-minus-model magnitude) and Gaia astrometry (parallax, proper motion, RUWE) — are not included in this model. They are used in the diagnostic scoring described in Section 4. AllWISE photometry is never part of this model at all, for any source: see Section 5.</p>

<h3>4. Evidence synthesis</h3>
<p>Each candidate is assigned a set of derived diagnostics, flags, and a composite <code>review_score</code> (definitions version {esc(DEFINITIONS_VERSION)}, last modified {esc(DEFINITIONS_UPDATED)}). Scores are computed once and stored with the definition version that produced them, so a subsequent change to the scoring formula does not alter the record of what a candidate showed at the time of review. Diagnostics that depend on SDSS-specific morphology are omitted, rather than substituted with a default value, for sources that do not report morphology. A candidate with a WISE crossmatch (Section 5) is additionally checked against the W1-W2 &gt; 0.8 mag colour cut (Stern et al. 2012; Assef et al. 2013) — a discriminator for AGN, dust, and cool (L/T) dwarfs rather than any one of those alone — via the <code>wise_red_excess</code> flag; no WISE match means the flag simply does not evaluate. Metric-level definitions are given on each candidate's review page.</p>

<h3>5. Crossmatch</h3>
<p>Each candidate is queried against SIMBAD and NED (30 arcsec search radius) and Gaia (10 arcsec search radius). WISE is matched differently: rather than a live query, each candidate is matched locally (6 arcsec, AllWISE's own angular resolution) against WISE photometry already ingested by the same per-tile scanning described in Section 2 — so a candidate only picks up a WISE match once that patch of sky has itself been WISE-scanned. A match does not exclude a candidate from review; it applies a fixed +0.25 penalty to the <code>artefact_risk</code> component of the review score.</p>

<h3>6. Rank tracking</h3>
<p>Because the Isolation Forest is refit at every tile scan against a growing population (Section 3), a candidate's <code>review_score</code> and rank are not stable per-object quantities — rank can move purely from population growth, independent of anything about the object itself. After every scoring run that scored at least one candidate, <code>rank_tracking.py</code> appends a snapshot of the current top 50 candidates by <code>review_score</code> to an append-only <code>rank_history</code> table, keyed to that run's own <code>runs.run_id</code> rather than a separate cycle counter. Comparing consecutive snapshots identifies candidates newly entering the top 50 — the useful signal, as opposed to ordinary re-ranking within an already-stable set — and further distinguishes a genuinely new candidate (no earlier appearance in <code>rank_history</code> at all) from one that existed in an earlier snapshot outside the top 50 and has since climbed in. Both are flagged on the review page.</p>

<h3>Implementation notes</h3>
<ul class="principles">
    <li><code>survey.db</code> holds all pipeline state. CSV output is generated only for export.</li>
    <li>Scoring definitions are versioned; a version change triggers rescoring rather than overwriting prior results silently.</li>
    <li>Metric definitions are displayed alongside their values on the review page.</li>
    <li>Pipeline runs commit incrementally and resume from the last committed state after interruption.</li>
    <li>Sources are declared in a registry (<code>SOURCE_SCANNERS</code>) rather than referenced individually in pipeline code.</li>
    <li>Rank history is appended, never overwritten or deleted, so past top-50 snapshots stay available for comparison.</li>
</ul>

<h3>Design principles</h3>
<ul class="principles">
    <li>Preserve primary evidence.</li>
    <li>Derived quantities are reproducible.</li>
    <li>Interpretation is separated from observation.</li>
    <li>Crossmatches are evidence, not exclusion criteria.</li>
    <li>Pipeline stages are modular.</li>
    <li>New surveys should require only source adapters.</li>
</ul>

<h3>References</h3>
<ul class="principles">
    <li>Abdurro'uf et al. 2022, ApJS, 259, 35 (SDSS DR17) — doi:10.3847/1538-4365/ac4414</li>
    <li>Gaia Collaboration, Vallenari et al. 2023, A&amp;A, 674, A1 (Gaia DR3) — doi:10.1051/0004-6361/202243940</li>
    <li>Cutri et al. 2013, Explanatory Supplement to the AllWISE Data Release Products</li>
    <li>Liu, Ting &amp; Zhou 2008, ICDM, 413–422 (Isolation Forest) — doi:10.1109/ICDM.2008.17</li>
    <li>Stern et al. 2012, ApJ, 753, 30 — doi:10.1088/0004-637X/753/1/30; Assef et al. 2013, ApJ, 772, 26 (WISE AGN colour selection)</li>
</ul>
</section>

<footer>
    GSS — Generalised Sky Survey. See <code>README.md</code> for the full command reference and <code>quickstart.md</code> to run the pipeline yourself.
</footer>

</body>
</html>"""


def make_html(db_path: str, out_path: Path) -> str:
    con = connect(db_path)
    try:
        sources = source_summary(con)
        tiles = tile_progress(con)
        tiles_by_source = {t["source"]: t for t in tiles}
        triage_dist = triage_class_distribution(con)
        triage_stats = triage_overview(con)
        cross_stats = crossmatch_overview(con)
        review_stats = review_status_summary(con)
        runs = recent_runs(con)
        db_stats = db_file_stats(db_path)

        total_objects = sum(s["objects"] for s in sources)
        total_candidates = sum(s["candidates"] for s in sources)
        total_triaged = sum(s["triaged"] for s in sources)
        total_tile_slots = sum(t["total"] for t in tiles) or 1
        total_resolved = sum(t["complete"] + t["no_coverage"] for t in tiles)
        overall_pct = 100 * total_resolved / total_tile_slots
    finally:
        con.close()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    source_cards_html = "\n".join(source_card(s, tiles_by_source) for s in sources) or "<p class='subtle'>No sources scanned yet.</p>"

    triage_dist_html = ""
    triage_total = sum(r["n"] for r in triage_dist) or 1
    for r in triage_dist:
        triage_dist_html += count_bar_row(r["triage_class"], r["n"], triage_total)

    review_html = ""
    review_total = sum(r["n"] for r in review_stats) or 1
    for r in review_stats:
        review_html += count_bar_row(r["status"], r["n"], review_total)

    cross_n = int(cross_stats.get("n") or 0)
    cross_simbad = int(cross_stats.get("simbad") or 0)
    cross_ned = int(cross_stats.get("ned") or 0)
    cross_gaia = int(cross_stats.get("gaia") or 0)
    cross_wise = int(cross_stats.get("wise") or 0)

    runs_rows = "\n".join(
        f"<tr><td>{esc(r['run_id'])}</td><td>{esc(r['run_type'])}</td><td>{esc(r['status'])}</td>"
        f"<td>{esc(r['started_at'])}</td><td>{esc(r['finished_at'] or '—')}</td></tr>"
        for r in runs
    ) or "<tr><td colspan='5' class='subtle'>No runs yet.</td></tr>"

    avg_review = triage_stats.get("avg_review")
    avg_risk = triage_stats.get("avg_risk")

    skymap_items = []
    for s in sources:
        img_path = Path("figures") / f"skymap_{s['source']}.png"
        if img_path.exists():
            skymap_items.append(
                f'<div class="skymap-item"><img src="{esc(img_path.as_posix())}" alt="{esc(s["source"])} tile coverage"></div>'
            )
    composite_path = Path("figures") / "skymap_composite.png"
    if composite_path.exists():
        skymap_items.append(
            f'<div class="skymap-item"><img src="{esc(composite_path.as_posix())}" alt="composite tile coverage"></div>'
        )
    skymap_html = "\n".join(skymap_items) or "<p class='subtle'>No skymaps generated yet -- run build_skymap.py.</p>"

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>GSS — Generalised Sky Survey</title>
<style>{THEME_CSS}</style>
</head>
<body>

<div class="hero">
    <h1>GSS — Generalised Sky Survey</h1>
    <div class="links">
        <a href="about.html">About →</a>
        <a href="review_pack/review.html">Object Catalogue →</a>
    </div>
</div>

<h2>Summary</h2>
<p class="subtle">Figures below are generated from the pipeline database ({db_stats['size_mb']:.1f} MB, last modified {esc(db_stats['mtime'])}) at {esc(timestamp)}.</p>

<div class="metrics">
    {metric_card('Objects scanned', f"{total_objects:,}")}
    {metric_card('Candidates flagged', f"{total_candidates:,}")}
    {metric_card('Candidates triaged', f"{total_triaged:,}")}
    {metric_card('Tiles resolved', f"{overall_pct:.1f}%")}
    {metric_card('Mean review score', f"{avg_review:.2f}" if avg_review is not None else "—")}
    {metric_card('Mean artefact risk', f"{avg_risk:.2f}" if avg_risk is not None else "—")}
</div>

<h2>Sky coverage</h2>
<p class="subtle">Mollweide projection of scanned sky tiles, coloured by scan status, per source.</p>
<div class="skymap-grid">
    {skymap_html}
</div>

<h2>Sources</h2>
<div class="source-grid">
    {source_cards_html}
</div>
<div class="legend">
    <span><i style="background:var(--good)"></i> complete</span>
    <span><i style="background:#56617d"></i> no coverage</span>
    <span><i style="background:#3a4257"></i> pending</span>
    <span><i style="background:var(--bad)"></i> failed</span>
</div>

<h2>Triage &amp; review</h2>
<div class="dash-grid">
    <div>
        <h3>Triage class distribution</h3>
        {triage_dist_html or "<p class='subtle'>No candidates triaged yet.</p>"}
    </div>
    <div>
        <h3>Human review status</h3>
        {review_html or "<p class='subtle'>No reviews recorded yet.</p>"}
    </div>
</div>

<h2>Crossmatch coverage</h2>
<div class="metrics">
    {metric_card('Candidates crossmatched', f"{cross_n:,}")}
    {metric_card('SIMBAD matches', f"{cross_simbad:,}")}
    {metric_card('NED matches', f"{cross_ned:,}")}
    {metric_card('Gaia matches', f"{cross_gaia:,}")}
    {metric_card('WISE matches', f"{cross_wise:,}")}
</div>

<h2>Recent runs</h2>
<table>
    <tr><th>Run ID</th><th>Type</th><th>Status</th><th>Started</th><th>Finished</th></tr>
    {runs_rows}
</table>

<footer>
    GSS — Generalised Sky Survey. See <code>README.md</code> for the full command reference and <code>quickstart.md</code> to run the pipeline yourself.
</footer>

</body>
</html>"""


def build_landing(db_path: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    html_text = make_html(db_path, out_path)
    out_path.write_text(html_text, encoding="utf-8")

    about_path = out_path.parent / "about.html"
    about_path.write_text(make_about_html(), encoding="utf-8")

    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="Build landing.html -- a live stats dashboard -- and about.html -- what GSS is and how "
                    "the pipeline works -- reading survey.db directly."
    )
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--output", "-o", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    out_path = build_landing(args.db, args.output)
    print(f"Landing page built: {out_path}")
    print(f"About page built: {out_path.parent / 'about.html'}")


if __name__ == "__main__":
    main()
