-- ---------------------------------------------------------------------------
-- File:        schema.sql
-- Version:     0.4
-- Date:        2026-07-14
-- Author:      Scott Douglass
-- Description: SQLite schema for survey.db -- tiles, objects, features,
--              candidates, crossmatches, reviews, and triage tables.
-- ---------------------------------------------------------------------------
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    notes TEXT
);

-- Pure sky geometry. Source-independent: the same tile_id is the same
-- patch of sky no matter which survey you're scanning. make_tiles.py
-- never needs to know about sources.
CREATE TABLE IF NOT EXISTS sky_tiles (
    tile_id TEXT PRIMARY KEY,
    ra_min REAL NOT NULL,
    ra_max REAL NOT NULL,
    dec_min REAL NOT NULL,
    dec_max REAL NOT NULL
);

-- Per-source scan state for a tile. A tile can be 'complete' in SDSS and
-- 'pending' in Gaia at the same time -- that's the whole point of this
-- table existing separately from sky_tiles.
--   pending      -- not yet scanned for this source
--   running      -- scan in progress
--   complete     -- scanned, data found (object_count may still be low)
--   no_coverage  -- scanned, source has no data here (e.g. outside footprint)
--   failed       -- a real error occurred (network, bad query, etc); retried
CREATE TABLE IF NOT EXISTS tile_scans (
    tile_id TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    last_run_id INTEGER,
    last_scanned_at TEXT,
    object_count INTEGER DEFAULT 0,
    candidate_count INTEGER DEFAULT 0,
    notes TEXT,
    PRIMARY KEY(tile_id, source),
    FOREIGN KEY(tile_id) REFERENCES sky_tiles(tile_id),
    FOREIGN KEY(last_run_id) REFERENCES runs(run_id)
);

-- Wide, nullable, multi-source table: SDSS-specific columns (u/g/r/i/z,
-- petroRad_r, etc.) are populated for source='sdss' rows and NULL for
-- other sources; Gaia-specific columns (phot_g_mean_mag, parallax, ruwe,
-- etc.) are populated for source='gaia' rows and NULL for SDSS. WISE
-- columns (w1mpro..w4mpro, added 2026-07-13) follow the same precedent.
-- WISE's own catalogue ID (AllWISE 'source_id') is a text designation, not
-- an integer, so objID for source='wise' rows holds 'cntr' instead -- the
-- catalogue's actual unique int64 key -- matching the role objID/source_id
-- play for SDSS/Gaia. W3/W4 are nullable independently of W1/W2: AllWISE
-- detects W1/W2 far more often than the shallower W3/W4 bands, so requiring
-- all four would silently gut the ingested sample.
CREATE TABLE IF NOT EXISTS objects (
    source TEXT NOT NULL,
    objID INTEGER NOT NULL,
    ra REAL NOT NULL,
    dec REAL NOT NULL,
    u REAL, g REAL, r REAL, i REAL, z REAL,
    psfMag_u REAL, psfMag_g REAL, psfMag_r REAL, psfMag_i REAL, psfMag_z REAL,
    psfMagErr_u REAL, psfMagErr_g REAL, psfMagErr_r REAL, psfMagErr_i REAL, psfMagErr_z REAL,
    petroRad_r REAL,
    petroR50_r REAL,
    petroR90_r REAL,
    extinction_r REAL,
    flags INTEGER,
    clean INTEGER,
    type INTEGER,
    first_seen_run_id INTEGER,
    last_seen_run_id INTEGER,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    phot_g_mean_mag REAL,
    phot_bp_mean_mag REAL,
    phot_rp_mean_mag REAL,
    parallax REAL,
    parallax_error REAL,
    pmra REAL,
    pmdec REAL,
    ruwe REAL,
    astrometric_excess_noise REAL,
    w1mpro REAL, w1sigmpro REAL,
    w2mpro REAL, w2sigmpro REAL,
    w3mpro REAL, w3sigmpro REAL,
    w4mpro REAL, w4sigmpro REAL,
    -- AllWISE coadd image tile ID (added 2026-07-14), needed to fetch a
    -- WISE FITS cutout (see wise_cutouts.py). NULL for objects ingested
    -- before this column existed -- see scan_tile_wise.py.
    coadd_id TEXT,
    PRIMARY KEY(source, objID),
    FOREIGN KEY(first_seen_run_id) REFERENCES runs(run_id),
    FOREIGN KEY(last_seen_run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS object_tiles (
    source TEXT NOT NULL,
    objID INTEGER NOT NULL,
    tile_id TEXT NOT NULL,
    first_seen_run_id INTEGER,
    PRIMARY KEY(source, objID, tile_id),
    FOREIGN KEY(source, objID) REFERENCES objects(source, objID),
    FOREIGN KEY(tile_id) REFERENCES sky_tiles(tile_id),
    FOREIGN KEY(first_seen_run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS features (
    source TEXT NOT NULL,
    objID INTEGER NOT NULL,
    u_g REAL,
    g_r REAL,
    r_i REAL,
    i_z REAL,
    psf_minus_model_r REAL,
    log_petroRad_r REAL,
    log_petroR50_r REAL,
    log_petroR90_r REAL,
    concentration_r REAL,
    mu_r REAL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    r REAL,
    extinction_r REAL,
    bp_rp REAL,
    bp_g REAL,
    g_rp REAL,
    pm_total REAL,
    parallax_over_error REAL,
    ruwe REAL,
    astrometric_excess_noise REAL,
    -- Survey-agnostic colour-shape features, computed by every source from
    -- its own bands into the same shared columns (bluest_band_mag minus
    -- reddest_band_mag; max single adjacent-band colour jump). This is the
    -- ONLY thing the global cross-survey Isolation Forest fits on --
    -- source-specific columns above (morphology for SDSS, astrometry for
    -- Gaia) are rich triage diagnostics but cannot feed one shared model
    -- since they don't exist across all sources.
    global_colour_span REAL,
    global_colour_jump REAL,
    -- WISE-only diagnostics (added 2026-07-13). Deliberately NOT part of
    -- the survey-agnostic pair above -- features_wise.py never populates
    -- global_colour_span/global_colour_jump for source='wise' rows, so
    -- load_all_global_features()'s IS NOT NULL filter (scan_tile.py)
    -- naturally excludes WISE from the shared Isolation Forest fit rather
    -- than needing special-case code. w1_w2/w2_w3 instead reach candidates
    -- only via a crossmatch join, feeding triage.py's wise_red_excess flag.
    w1_w2 REAL,
    w2_w3 REAL,
    PRIMARY KEY(source, objID),
    FOREIGN KEY(source, objID) REFERENCES objects(source, objID)
);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    objID INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    tile_id TEXT,
    anomaly_score REAL NOT NULL,
    rank_in_run INTEGER,
    reason_flags TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, objID, run_id),
    FOREIGN KEY(source, objID) REFERENCES objects(source, objID),
    FOREIGN KEY(run_id) REFERENCES runs(run_id),
    FOREIGN KEY(tile_id) REFERENCES sky_tiles(tile_id)
);

CREATE TABLE IF NOT EXISTS crossmatches (
    source TEXT NOT NULL,
    objID INTEGER NOT NULL,
    search_radius_arcsec REAL,
    simbad_checked_at TEXT,
    simbad_match INTEGER DEFAULT 0,
    simbad_id TEXT,
    simbad_otype TEXT,
    ned_checked_at TEXT,
    ned_match INTEGER DEFAULT 0,
    ned_name TEXT,
    ned_type TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gaia_checked_at TEXT,
    gaia_match INTEGER DEFAULT 0,
    gaia_source_id TEXT,
    gaia_dist REAL,
    -- WISE match (added 2026-07-13), unlike simbad_*/ned_*/gaia_* above, is
    -- looked up locally against already-ingested objects/features rows
    -- (source='wise') rather than a live external query -- see
    -- crossmatch_candidates.py. wise_objID is the matched row's cntr.
    wise_checked_at TEXT,
    wise_match INTEGER DEFAULT 0,
    wise_objID INTEGER,
    wise_dist REAL,
    wise_w1_w2 REAL,
    PRIMARY KEY(source, objID),
    FOREIGN KEY(source, objID) REFERENCES objects(source, objID)
);

CREATE TABLE IF NOT EXISTS reviews (
    source TEXT NOT NULL,
    objID INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'unreviewed',
    priority INTEGER,
    human_notes TEXT DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(source, objID),
    FOREIGN KEY(source, objID) REFERENCES objects(source, objID)
);

-- Derived triage scores/flags for a candidate, computed by score_candidates.py
-- (see triage.py for the formulas). Keyed by candidate_id rather than
-- (source, objID) because anomaly_score -- which several of these derive
-- from -- belongs to a specific run's candidate row, not the object itself.
-- definition_version lets a formula change (bump triage.DEFINITIONS_VERSION)
-- be detected and re-scored without losing what an old candidate page showed
-- at the time a human reviewed it.
CREATE TABLE IF NOT EXISTS triage (
    candidate_id INTEGER PRIMARY KEY,
    definition_version TEXT NOT NULL,
    colour_span REAL,
    red_score REAL,
    full_red_score REAL,
    colour_curvature_gr_ri REAL,
    colour_curvature_ri_iz REAL,
    colour_smoothness REAL,
    colour_jump_max REAL,
    size_ratio_petro_r50 REAL,
    r90_r50_width REAL,
    compactness_proxy REAL,
    diffuse_proxy REAL,
    psf_per_radius REAL,
    surface_brightness_offset REAL,
    weirdness_score REAL,
    artefact_risk REAL,
    review_score REAL,
    triage_class TEXT,
    triage_flags TEXT,
    flag_extreme_colour INTEGER,
    flag_likely_model_issue INTEGER,
    flag_possible_lsb INTEGER,
    flag_compact_red INTEGER,
    flag_probable_shred INTEGER,
    flag_gaia_matched INTEGER,
    flag_catalogued INTEGER,
    flag_wise_red_excess INTEGER,
    computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
);

-- Append-only top-N-by-review_score snapshots, one set of rows per
-- scoring cycle (see rank_tracking.py). Exists because the Isolation
-- Forest refits from scratch every tile scan against a growing population
-- (scan_tile.py), so review_score/rank for a given candidate is not a
-- stable quantity over time -- a candidate's rank can move purely from
-- population growth, not from anything about the object itself. This
-- table lets rank_tracking.py tell "newly entered the top N" (the useful
-- signal) apart from ordinary re-ranking within an already-stable set.
-- scan_cycle is runs.run_id of the score_candidates.py invocation that
-- wrote the snapshot -- there is no separate cycle counter (run_pipeline.py
-- has no persisted one; its in-memory round counter resets on restart).
-- Rows are never updated or deleted, only appended.
CREATE TABLE IF NOT EXISTS rank_history (
    candidate_id INTEGER NOT NULL,
    scan_cycle INTEGER NOT NULL,
    rank_in_cycle INTEGER NOT NULL,
    review_score REAL NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(candidate_id, scan_cycle),
    FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_objects_ra_dec ON objects(ra, dec);
CREATE INDEX IF NOT EXISTS idx_candidates_run ON candidates(run_id, anomaly_score);
CREATE INDEX IF NOT EXISTS idx_candidates_obj ON candidates(source, objID);
CREATE INDEX IF NOT EXISTS idx_tile_scans_status ON tile_scans(source, status);
CREATE INDEX IF NOT EXISTS idx_triage_review_score ON triage(review_score);
CREATE INDEX IF NOT EXISTS idx_rank_history_cycle ON rank_history(scan_cycle);
