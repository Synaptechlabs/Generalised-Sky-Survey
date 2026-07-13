<!--
File:        README.md
Version:     0.1
Date:        2026-07-11
Author:      Scott Douglass
Description: Project description, pipeline overview, design notes, and
             command reference for GSS.
-->

# GSS — Generalised Sky Survey

GSS scans photometric survey catalogues on a per-tile basis, computes
derived features from the raw measurements, and applies an Isolation
Forest to identify objects with atypical feature values relative to the
observed population. Identified candidates are cross-matched against
SIMBAD, NED, and Gaia, assigned a set of derived diagnostic scores, and
compiled for manual review.

`survey.db` is the source of truth for all pipeline state, including
triage scores. CSVs are exports only — `export_candidates.py`'s output is
a standalone download, not read by any other script in the pipeline.

## Current scope

- Two catalogues ingested: SDSS (photometric imaging, `scan_tile.py`) and
  Gaia DR3 (astrometry and photometry, `scan_tile_gaia.py`), each under a
  distinct source label with independent tile-scan tracking.
- One Isolation Forest fit globally across all sources on a shared,
  survey-agnostic colour feature set (`global_features.py`), not siloed
  per catalogue. Each source's features are normalised against its own
  median/MAD before fitting, so no source's scale or sample density
  dominates the pooled fit.
- Versioned evidence synthesis (`triage.py`): derived diagnostics, flags,
  and a composite review score per candidate, recomputed only when the
  definition version changes.
- Crossmatch against SIMBAD, NED, and Gaia (`crossmatch_candidates.py`).
- A browsable review pack (`build_review.py`) and a project overview page
  with live pipeline statistics and per-source sky-coverage maps
  (`build_landing.py`, `build_skymap.py`).
- A long-running orchestrator (`run_pipeline.py`) that scans every
  registered source and periodically triggers scoring, crossmatching, and
  export.

## Install

```bash
pip install astroquery astropy pandas scikit-learn matplotlib numpy requests
```

## Initialise

```bash
python startup/init_db.py
```

## Create sky tiles

Generates the full-sky 1x1 degree tile grid and bootstraps `tile_scans` for
the `sdss` source. Safe to re-run — it's a no-op once `sky_tiles` is populated.

```bash
python startup/populate.py --db survey.db
```

## Scan one pending tile

```bash
python scan_tile.py
```

Or scan a specific tile:

```bash
python scan_tile.py --tile-id "..."
```

## Crossmatch cached candidates

```bash
python crossmatch_candidates.py --limit 50
```

## Score candidates (triage)

Computes derived diagnostics, flags, and the composite `review_score` for
any candidate that doesn't have them yet (or was scored under an older
`triage.DEFINITIONS_VERSION`), and stores them in `survey.db`'s `triage`
table. See `triage.py` for the formulas.

```bash
python score_candidates.py --limit 500
```

## Build the review pack

Reads `survey.db` directly (candidates must already be scored — run
`score_candidates.py` first), caches thumbnails, and builds the triaged
review HTML:

```bash
python build_review.py --limit 500
```

## Export candidates to CSV (optional, for download/sharing)

Standalone data dump, independent of the review pack — nothing else in the
pipeline reads this CSV. Includes triage columns if `score_candidates.py`
has already run.

```bash
python export_candidates.py --limit 500
```

## Run everything continuously

`run_pipeline.py` wraps the per-source tile scanners (`scan_tile.py` for
SDSS, `scan_tile_gaia.py` for Gaia) in a bounded or long-running loop and
periodically shells out to the other steps, so you don't have to run them
by hand during a long scan session. Every round scans one tile from every
registered source (no source is preferred over another) — all show up in
the log each round:

```bash
python run_pipeline.py --forever
```

Key flags:
- `--sources sdss,gaia` — restrict to a subset of sources instead of every registered scanner (skips a source once it has no pending tiles left)
- `--tiles N` — run N rounds then stop (default 1 if `--forever` isn't given; a round scans one tile per source)
- `--score-every N` / `--score-limit N` — run `score_candidates.py` every N tiles (default: every 5, limit 500)
- `--crossmatch-every N` / `--crossmatch-limit N` — run `crossmatch_candidates.py` every N tiles (default: every 10, limit 50)
- `--export-every N` / `--export-limit N` — run `export_candidates.py` every N tiles (default: every 10, limit 500)
- `--sleep SECONDS` — pause between tiles (default 5s)
- `--max-consecutive-failures N` — stop after N scan failures in a row (default 5, 0 = unlimited)

It does not call `build_review.py` — that's a separate, on-demand step
(see [quickstart.md](quickstart.md)) since you only need a fresh HTML page
when you're actually about to review candidates, not on every tile.

## Build the landing page

Project overview and live pipeline statistics, reading `survey.db`
directly. Independent of the review pack build:

```bash
python build_skymap.py
python build_landing.py
```

`build_skymap.py` renders a Mollweide-projection tile-coverage map per
source into `figures/`, which `build_landing.py` embeds if present. It is
not regenerated automatically — re-run it after further scanning to
refresh the images.

## Design note

This is intentionally not an agent system.
The immediate goal is persistent survey state:

- which tiles were scanned
- which objects were seen
- which objects became candidates
- which crossmatches were already done
- what the human review status is
