<!--
File:        README.md
Version:     0.5
Date:        2026-07-14
Author:      Scott Douglass
Description: Project description, pipeline overview, and command
             reference for GSS.
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

## Requirements

Python 3.13+. No accounts or API keys are required: all catalogue/archive
access (SDSS SkyServer, the Gaia archive, IRSA, SIMBAD, NED) is via public,
unauthenticated queries, so the pipeline needs outbound internet access but
nothing to configure beyond that.

## Current scope

- Three catalogues ingested: SDSS DR17 (photometric imaging, `scan_tile.py`),
  Gaia DR3 (astrometry and photometry, `scan_tile_gaia.py`), and AllWISE
  (infrared photometry, `scan_tile_wise.py`), each under a distinct source
  label with independent tile-scan tracking.
- One Isolation Forest fit globally across SDSS and Gaia on a shared,
  survey-agnostic colour feature set (`global_features.py`), not siloed
  per catalogue. Each source's features are normalised against its own
  median/MAD before fitting, so no source's scale or sample density
  dominates the pooled fit. WISE is ingested the same way as SDSS/Gaia but
  deliberately does not feed this shared fit (see below) -- it is not
  itself scored for anomalies.
- Versioned evidence synthesis (`triage.py`): derived diagnostics, flags,
  and a composite review score per candidate, recomputed only when the
  definition version changes. WISE photometry reaches this stage only via
  a local crossmatch join (`crossmatch_candidates.py` against the cached
  WISE objects), feeding a `wise_red_excess` flag (W1-W2 > 0.8 mag,
  an AGN/dust/cool-dwarf discriminator) on SDSS/Gaia candidates that have
  a nearby WISE match -- no match means the flag simply doesn't evaluate.
- Crossmatch (`crossmatch_candidates.py`) against SIMBAD, NED, and Gaia via
  live queries, plus WISE via a local lookup against already-ingested data.
- Rank history tracking (`rank_tracking.py`): since the Isolation Forest is
  refit from scratch at every tile scan, a candidate's `review_score`/rank
  isn't stable over time on its own -- rank can move purely from
  population growth. `score_candidates.py` appends a top-50-by-review_score
  snapshot to an append-only `rank_history` table after each run that
  scored something, and the review pack flags candidates newly entering
  the top 50, distinguishing a genuinely new candidate from one that
  merely climbed in as the population shifted.
- A browsable review pack (`build_review.py`) and a project overview page
  with live pipeline statistics and per-source sky-coverage maps
  (`build_landing.py`, `build_skymap.py`).
- A long-running orchestrator (`run_pipeline.py`) that scans every
  registered source and periodically triggers scoring, crossmatching, and
  export.

## Install

```bash
pip install -r requirements.txt
```

## Run with Docker

An alternative to the local install above. Builds a `python:3.13-slim`
image with `requirements.txt` installed, and bind-mounts the repo into the
container so `survey.db`, `figures/`, `review_pack/`, and `runner.log` land
at their normal paths on the host rather than inside the container:

```bash
docker compose build
```

Every command in this README and in [quickstart.md](quickstart.md) works
the same way, run through `docker compose run --rm pipeline` in place of
`python` directly, e.g.:

```bash
docker compose run --rm pipeline python startup/init_db.py
docker compose run --rm pipeline python scan_tile.py
```

`docker compose up` alone runs the default command, `run_pipeline.py --forever`.

The container runs as the host user rather than root, so files it creates
are owned correctly on the host. It reads `UID`/`GID` from a local `.env`
file (not committed, since these differ per machine) and falls back to
`1000:1000` if none is set.

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

## License

[MIT](LICENSE)
