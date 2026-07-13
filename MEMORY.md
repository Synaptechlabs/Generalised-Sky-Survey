<!--
File:        MEMORY.md
Version:     0.1
Date:        2026-07-11
Author:      Scott Douglass
Description: Repo-level notes on gotchas, layout decisions, and
             architecture history not obvious from the code alone.
-->

# Pipeline Notes

Repo-level notes on gotchas and layout decisions that aren't obvious from
reading the code alone. Keep this updated as the pipeline evolves.

## Known gotchas

- **`tmp_old/make_tiles.py`'s tile_id format does NOT match the live DB.**
  It generates `ra000_dec+00`-style tile_ids. `survey.db`'s real
  `sky_tiles`/`tile_scans` (built by `startup/populate.py`) use
  `000_+00`-style tile_ids instead. Running `make_tiles.py` against
  `survey.db` silently inserts orphan `sky_tiles` rows that `scan_tile.py`
  can never join against (no `tile_scans`, `objects`, or `candidates`
  reference them). This is why it's archived in `tmp_old/` instead of
  `startup/`, despite what an older README revision said.

- **`schema.sql` was out of sync with `survey.db` until 2026-07-11.**
  Two live migrations had been applied ad hoc via now-archived
  `tmp_old/tmp-gaia-update.py` and a since-commented-out `ALTER TABLE` in
  `tmp_old/tmp-sql.py`, adding `features.r`, `features.extinction_r`, and
  `crossmatches.gaia_checked_at` / `gaia_match` / `gaia_source_id` /
  `gaia_dist`. `schema.sql` now declares these columns too, so a fresh
  `startup/init_db.py` run reproduces the live schema exactly (verified by
  diffing `PRAGMA table_info` between an in-memory build of `schema.sql`
  and the live DB).

## Layout

- `startup/` — one-time initialization scripts, run once (or idempotently)
  before scanning begins: `init_db.py` (applies `schema.sql`),
  `populate.py` (generates the full-sky 64,800-tile grid and bootstraps
  `tile_scans` for `sdss`). Both insert `sys.path` back to the project root
  so `from db import ...` resolves correctly when invoked as
  `python startup/<script>.py` from the repo root.
- `tmp_old/` — archived/dead scripts kept for reference only: applied
  one-off DB migrations, a debug script, `make_tiles.py` (see gotcha
  above), and `filter_candidates.py` (superseded by `score_candidates.py` +
  `triage` table, see below). Nothing in the active pipeline imports or
  shells out to anything in this folder.

## Triage architecture (CSV chain removed, 2026-07-11)

Candidate triage (derived diagnostics, flags, `review_score`) used to be a
CSV-to-CSV transform (`export_candidates.py` → `filter_candidates.py` →
`build_review.py`). It's now computed once and stored in the DB:

- `triage.py` — pure scoring logic (`add_candidate_triage()`) plus
  `DEFINITIONS_VERSION`/`DEFINITIONS_UPDATED` and `METRIC_DEFINITIONS` (the
  human-readable descriptions rendered on each review card). This is the
  single source of truth for the formulas — keep it in sync if the scoring
  logic changes, and bump `DEFINITIONS_VERSION` when it does.
- `score_candidates.py` — reads directly from `candidates`/`objects`/
  `features`/`crossmatches`, computes triage via `triage.py`, and
  UPSERTs into the `triage` table (schema.sql). Only processes candidates
  missing a `triage` row or whose `definition_version` doesn't match the
  current `DEFINITIONS_VERSION` — mirrors the pending-work pattern
  `crossmatch_candidates.py` already used.
- `triage` is keyed by `candidate_id` (not `(source, objID)` like most
  other tables) because several fields (e.g. `weirdness_score`) derive from
  `anomaly_score`, which belongs to a specific run's candidate row, not the
  object itself.
- `build_review.py` now reads `survey.db` directly (one JOIN across
  `candidates`/`objects`/`features`/`crossmatches`/`reviews`/`triage`,
  `INNER JOIN`ed on `triage` — so **`score_candidates.py` must run first**
  or a candidate won't appear). It also fetches/caches thumbnails itself
  now (via `thumbnails.py`), rather than depending on `export_candidates.py`
  having pre-populated a `thumbnail_path` CSV column.
- `export_candidates.py` is now a standalone CSV dump only — nothing else
  in the pipeline reads its output. It LEFT JOINs `triage` so the CSV
  includes scores where available, but doesn't require them.
- `thumbnails.py` — shared thumbnail cache helpers (`get_thumbnail()` etc.),
  used by `build_review.py`. **Thumbnails should only ever download if not
  already cached** — `get_thumbnail()` already enforces this via
  `valid_cached_image()`, and `build_review.py --fresh` deliberately
  preserves the `thumbnails/` subdirectory while clearing everything else
  in `review_pack/` (confirmed with the user 2026-07-11: never wipe the
  thumbnail cache).
- Verified against the live DB: `add_candidate_triage()` reproduces the
  exact pre-refactor values for a known reference candidate
  (`sdss:1237651758286045802`, `candidate_id=412840`) —
  `review_score=12.449`, `weirdness_score=12.699`, `artefact_risk=0.25`,
  `triage_class=extreme_colour`.

## Multi-survey architecture (Gaia added, 2026-07-11)

`scan_tile_gaia.py` scans Gaia DR3 into the same schema under
`source='gaia'`, alongside SDSS's `source='sdss'`. Two things are load-bearing
here, not incidental:

- **The Isolation Forest is ONE global model across every source.** It is
  NOT fit separately per source — that was tried and explicitly rejected.
  Since SDSS and Gaia share no raw columns, a shared model needs a common
  feature space: `global_features.py` defines
  `GLOBAL_FEATURE_COLS = ["global_colour_span", "global_colour_jump"]`,
  two survey-agnostic colour features every source computes from its own
  bands into the same `features` table columns. `scan_tile.py`'s
  `load_all_global_features()` queries across all sources with no
  `WHERE source=?` filter, and `scan_tile_gaia.py` imports that exact
  function rather than duplicating it — both scanners fit the literal same
  model. Verified live: scanning tile `180_+00` for both sources produced
  one ranking with SDSS and Gaia candidates genuinely interleaved by
  `anomaly_score` (14 SDSS + 1 Gaia in the top 15).
- Tradeoff: SDSS's morphology columns (`concentration_r`, `mu_r`, etc.) and
  Gaia's astrometry columns (`parallax`, `pmra`/`pmdec`, `ruwe`) are NOT
  part of the shared model — only the two global colour features are.
  Those richer per-source columns still exist in `objects`/`features` for
  triage-time diagnostics, they just don't drive `anomaly_score`.
- **Fixed (same day, `triage.py` bumped to `DEFINITIONS_VERSION="0.2"`):**
  `add_candidate_triage()` previously defaulted missing morphology
  (`petroRad_r`, `concentration_r`, `mu_r`) to `0.0`, which tripped
  `petro <= 0` and auto-flagged every Gaia candidate as
  `triage_class=artefact_risk` regardless of actual quality. Now guarded
  by an explicit `morphology_available` check (`True` only when
  `petroRad_r`/`concentration_r`/`mu_r` are all non-null) — morphology-only
  flags (`likely_model_issue`, `probable_shred`, `possible_lsb`,
  `compact_red`'s morphology term) and diagnostics
  (`compactness_proxy`/`diffuse_proxy`/`psf_per_radius`/
  `surface_brightness_offset`) simply don't apply when unavailable,
  returning `NaN`/`False` instead of a misleading `0`/`True`. Colour
  diagnostics (`full_red_score`, `colour_jump_max`, etc.) fall back to the
  survey-agnostic `global_colour_span`/`global_colour_jump` columns
  (`global_features.py`) when the rich SDSS 4-band colours aren't present.
  Verified byte-identical output for the SDSS reference candidate
  (`candidate_id=412840`) before/after, and confirmed Gaia candidates now
  get `mixed_anomaly`/`extreme_colour` based on real signal instead of
  uniform `artefact_risk`. `score_candidates.py`'s query was updated to
  also select `f.global_colour_span, f.global_colour_jump` for the
  fallback to work.
- New files: `scan_tile_gaia.py`, `features_gaia.py`, `global_features.py`.

**Fixed (same day): `run_pipeline.py` never actually called `scan_tile_gaia.py`.**
`scan_tile_gaia.py` existed as a standalone script but the orchestration
loop only ever shelled out to `scan_tile.py` (hardcoded, SDSS-only) — so a
long `run_pipeline.py` session silently never scanned Gaia at all, with no
indication of that in the log. Fixed: `--source` (single, default `sdss`)
replaced with `--sources` (comma-separated, default `None` = every
registered scanner, no hardcoded preference), backed by a
`SOURCE_SCANNERS = {"sdss": "scan_tile.py", "gaia": "scan_tile_gaia.py"}`
registry. Confirmed with the user 2026-07-11: an initial fix that defaulted
`--sources` to the literal string `"sdss,gaia"` was explicitly rejected —
"there should be no fucking default! They should all just run with no
preference!" — because a hardcoded default string goes stale the moment a
third source is registered and implies an ordering/preference that isn't
real. Corrected to `default=None` → `sources = list(SOURCE_SCANNERS)` when
unset, so a new source added to the registry is automatically included in
default runs with no other change needed. Each loop iteration is now a "round" that
scans one tile from every listed source (skipping a source once it has no
pending tiles left) — both sources' `Scanning ...`/`Finished ...` lines
show up in the log every round. Verified live with a 2-round test run:
`Pending tiles: {'sdss': 40706, 'gaia': 64799}` logged each round,
followed by `scan_tile_sdss_N` then `scan_tile_gaia_N` steps, each with
their own scan output. Add a new source to `SOURCE_SCANNERS` when a third
scanner is built, rather than hardcoding it into the loop again.

## Landing page (`build_landing.py`, 2026-07-11)

`build_landing.py` builds `landing.html` — a project overview (what GSS
is, the pipeline steps, design principles) plus a live stats dashboard
(per-source object/candidate/triage counts, tile scan progress bars,
triage class distribution, crossmatch coverage, recent runs), reading
`survey.db` directly. Same dark visual theme as `build_review.py`, but
**deliberately a fully separate, self-contained script** — no shared CSS
module between the two. An earlier attempt extracted the shared CSS into
a `theme.py` module and started refactoring `build_review.py` to use it;
this was explicitly rejected by the user mid-edit — they'd asked for a new
landing page, not a refactor of the already-working review script. Reverted
immediately, `theme.py` deleted. **Lesson: don't touch a working, verified
file as a side effect of an unrelated request, even for a reasonable-looking
DRY improvement — if it wasn't asked for, it's scope creep.**

Landing page content went through several rounds of correction before
converging: no marketing/product styling (hero banners, CTA buttons, pill
badges), no duplicating `triage.py`'s `METRIC_DEFINITIONS` (that belongs on
the review page only), methodology as one continuous prose section (not
fragmented into one card per subsection), and dry/factual academic register
throughout (no "designed to," no "defining architectural constraint" —
state what the system does, not why the design is good). Section 4 is
titled "Evidence synthesis," not "Triage scoring." A "Design principles"
subsection lists architecture-level invariants with zero implementation
detail (no Python/SQLite/Isolation Forest mentions) — see `build_landing.py`
for current wording.

## Sky coverage maps (`build_skymap.py`, 2026-07-12)

Renders a Mollweide-projection tile-coverage map per source (dark-themed to
match the site, tiles coloured by `tile_scans.status`) into `figures/`.
This is the standard astronomical convention for showing survey footprint
— deliberately not a literal 3D globe, which was also requested but
intentionally deferred (a fixed-viewpoint 3D sphere only shows one
hemisphere at a time without added interactivity/rotation; the 2D
projection was built first as agreed). `build_landing.py` embeds whatever
images exist under `figures/skymap_<source>.png` for each source currently
in the DB; it does not regenerate them itself, so `build_skymap.py` must be
re-run manually after further scanning to refresh the images.

## Per-source normalization before the global Isolation Forest (2026-07-12)

The global anomaly model (see multi-survey section above) originally fit a
single `RobustScaler` across the pooled multi-source `GLOBAL_FEATURE_COLS`
before the `IsolationForest`. Problem: `RobustScaler`'s median/IQR are
computed over the *pooled* population, so whichever source has more rows
or a wider natural scale dominates the fit — verified live: Gaia
contributes 3.4x more rows than SDSS (1.64M vs 483K), and SDSS's
`global_colour_span` median (2.25) is ~60% larger than Gaia's (1.43) with a
wider spread too. An object's anomaly score was partly reflecting which
catalogue it came from, not genuine rarity within that catalogue.

Fixed: `global_features.py` gained `compute_source_norm_stats()` /
`apply_norm_stats()` — each source's `GLOBAL_FEATURE_COLS` values are
centered/scaled against that source's own median and MAD (median absolute
deviation, not mean/std, since the values being normalized include exactly
the tail cases the model is meant to detect) before the pooled fit.
`scan_tile.py`'s `score_tile()` now takes this approach directly (no more
`RobustScaler`/`make_pipeline`), and gained an `id_col` parameter
(`"objID"` default) so it could be genuinely shared rather than
re-implemented — `scan_tile_gaia.py` now imports `score_tile` from
`scan_tile.py` directly (passing `id_col="source_id"`) instead of carrying
its own near-duplicate copy.

This is NOT a versioned/persisted subsystem (no `norm_version` table) —
normalization stats are recomputed fresh on every scan, in the same spirit
as the `IsolationForest` itself being refit fresh every run rather than
cached. Don't add a persisted normalization-stats table without a reason;
it would be new architecture the rest of this pipeline doesn't use for the
anomaly-detection step.

Scope note: this was implemented from a short external brainstorm the user
pasted in (ideas for per-band errors/flags, cross-band centroid offsets,
WISE crossmatch, extinction lookup, per-band cutouts, etc.) — only the
per-source normalization idea was actually approved ("1 makes sense"); the
rest of that list was NOT implemented and should not be assumed to be
wanted without separate confirmation.
