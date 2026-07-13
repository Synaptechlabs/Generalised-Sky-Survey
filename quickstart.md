<!--
File:        quickstart.md
Version:     0.2
Date:        2026-07-13
Author:      Scott Douglass
Description: Command-first quickstart reference for running the pipeline
             end-to-end.
-->

# Quickstart

Command-first reference. For the why behind these steps, see
[README.md](README.md).

## First-time setup

```bash
pip install -r requirements.txt
python startup/init_db.py
python startup/populate.py --db survey.db
```

Both `startup/` scripts are safe to re-run — they're no-ops once `survey.db`
already has the right schema/tiles.

Every command on this page also runs in Docker, unchanged, by prefixing it
with `docker compose run --rm pipeline` in place of `python` — see
[README.md](README.md#run-with-docker).

## Scan the sky

One tile:

```bash
python scan_tile.py
```

Long-running session (scans until no pending tiles remain, periodically
triages, crossmatches, and exports along the way):

```bash
python run_pipeline.py --forever
```

Ctrl+C is safe — every tile commits to `survey.db` as it finishes, so the
next run just resumes where it left off.

## Generate a review page

This is the step that replaced the old `export_candidates.py` → `filter_candidates.py`
→ `build_review.py` chain. It's now two steps:

```bash
python score_candidates.py --limit 500
python build_review.py --limit 500
```

Then open `review_pack/review.html` in a browser.

- `score_candidates.py` only scores candidates that don't have triage yet
  (or were scored under an older formula version), so it's cheap to re-run.
- `build_review.py` reads `survey.db` directly — run `score_candidates.py`
  first or a candidate won't appear (it INNER JOINs on `triage`).
- Add `--fresh` to `build_review.py` to clear old review pack artifacts.
  This never re-downloads cached thumbnails.
- Add `--min-review-score N` to only include candidates above a threshold.

## Generate the landing page

Project overview + live stats dashboard (objects/candidates/triage/tile
progress per source, crossmatch coverage, recent runs), reading `survey.db`
directly. Independent of the review pack build:

```bash
python build_skymap.py
python build_landing.py
```

`build_skymap.py` renders a Mollweide-projection tile-coverage map per
source into `figures/`, which `build_landing.py` embeds if present. Re-run
it after further scanning to refresh the images -- `build_landing.py`
does not regenerate them itself.

Then open `landing.html` in a browser — it links to `review_pack/review.html`.

## Other useful commands

Crossmatch against SIMBAD/NED/Gaia:

```bash
python crossmatch_candidates.py --limit 50
```

Export a standalone CSV for download/sharing (not read by anything else):

```bash
python export_candidates.py --limit 500
```

## Common gotchas

- Always generate tiles via `startup/populate.py` — a different, older tile
  generator existed on a prior machine with a `tile_id` format that doesn't
  match what's actually in `survey.db`, and never made it into this repo.
- `build_review.py` will print "No triaged candidates found" if you skip
  `score_candidates.py` first.
