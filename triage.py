# ---------------------------------------------------------------------------
# File:        triage.py
# Version:     0.1
# Date:        2026-07-11
# Author:      Scott Douglass
# Description: Pure scoring logic for candidate triage (derived
#              diagnostics, flags, review_score) plus the versioned metric
#              definitions shown on review cards.
# ---------------------------------------------------------------------------
"""
Candidate triage scoring: derived diagnostics, flags, and the composite
review_score used to prioritise human review.

Ground truth for every formula below is add_candidate_triage() itself --
keep DEFINITIONS_VERSION and METRIC_DEFINITIONS' prose in sync with any
change to the logic. score_candidates.py imports the scoring function and
writes results into the `triage` table; build_review.py imports the
constants/definitions to render them on each candidate card.
"""
import math

DEFINITIONS_VERSION = "0.2"
DEFINITIONS_UPDATED = "2026-07-11"


def safe_float(value, default=math.nan):
    if value is None:
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(v) else v


def _isna(value):
    if value is None:
        return True
    try:
        return math.isnan(value)
    except TypeError:
        return False


def _nansum(values):
    total = 0.0
    for v in values:
        if v is None:
            continue
        try:
            if math.isnan(v):
                continue
        except TypeError:
            continue
        total += v
    return total


def add_candidate_triage(row: dict) -> dict:
    u = safe_float(row.get("u"))
    g = safe_float(row.get("g"))
    r = safe_float(row.get("r"))
    i = safe_float(row.get("i"))
    z = safe_float(row.get("z"))

    u_g = safe_float(row.get("u_g"))
    g_r = safe_float(row.get("g_r"))
    r_i = safe_float(row.get("r_i"))
    i_z = safe_float(row.get("i_z"))

    anomaly = safe_float(row.get("anomaly_score"), 0.0)

    # Morphology (petroRad_r/concentration_r/mu_r/psf_minus_model_r) is
    # SDSS-specific -- other sources (e.g. Gaia) simply don't measure it.
    # Only apply morphology-dependent logic when it's actually present:
    # defaulting these to 0 previously tripped "petro <= 0" on every
    # non-SDSS candidate, auto-flagging them all as likely_model_issue.
    morphology_available = (
        row.get("petroRad_r") is not None
        and row.get("concentration_r") is not None
        and row.get("mu_r") is not None
    )

    psf = safe_float(row.get("psf_minus_model_r"), 0.0)
    conc = safe_float(row.get("concentration_r"), 0.0)
    mu = safe_float(row.get("mu_r"), 0.0)
    petro = safe_float(row.get("petroRad_r"), 0.0)

    # Colour diagnostics: prefer the rich SDSS 4-band decomposition when
    # present, else fall back to the survey-agnostic global_colour_span/
    # global_colour_jump columns (global_features.py) that every source
    # populates the same way.
    colours = [u_g, g_r, r_i, i_z]
    finite_colours = [c for c in colours if math.isfinite(c)]
    colour_data_available = len(finite_colours) > 0

    if colour_data_available:
        colour_span = u - z if math.isfinite(u) and math.isfinite(z) else math.nan
        red_score = _nansum([u_g, g_r, r_i])
        full_red_score = _nansum([u_g, g_r, r_i, i_z])
        colour_curvature_gr_ri = g_r - r_i if math.isfinite(g_r) and math.isfinite(r_i) else math.nan
        colour_curvature_ri_iz = r_i - i_z if math.isfinite(r_i) and math.isfinite(i_z) else math.nan
        colour_smoothness = _nansum([abs(u_g - g_r), abs(g_r - r_i), abs(r_i - i_z)])
        colour_jump_max = max([abs(c) for c in finite_colours], default=0.0)
    else:
        global_span = safe_float(row.get("global_colour_span"))
        global_jump = safe_float(row.get("global_colour_jump"), 0.0)
        colour_span = global_span
        red_score = global_span if math.isfinite(global_span) else 0.0
        full_red_score = red_score
        colour_curvature_gr_ri = math.nan
        colour_curvature_ri_iz = math.nan
        colour_smoothness = 0.0
        colour_jump_max = global_jump

    # Derived morphology / artefact diagnostics -- only meaningful when
    # morphology_available.
    size_ratio_petro_r50 = math.nan
    r50 = math.nan
    if morphology_available and "petroR50_r" in row and safe_float(row.get("petroR50_r"), 0.0) > 0:
        r50 = safe_float(row.get("petroR50_r"), 0.0)
        size_ratio_petro_r50 = petro / r50 if r50 else math.nan

    r90_r50_width = math.nan
    if morphology_available and "petroR90_r" in row and math.isfinite(r50):
        r90 = safe_float(row.get("petroR90_r"))
        r90_r50_width = r90 - r50 if math.isfinite(r90) else math.nan

    if morphology_available:
        compactness_proxy = conc / petro if petro > 0 else 0.0
        diffuse_proxy = mu / petro if petro > 0 else 0.0
        psf_per_radius = psf / petro if petro > 0 else 0.0
        surface_brightness_offset = mu - r if math.isfinite(r) else math.nan
    else:
        compactness_proxy = math.nan
        diffuse_proxy = math.nan
        psf_per_radius = math.nan
        surface_brightness_offset = math.nan

    # Crossmatch indicators. These are deliberately soft: known objects are not dropped.
    simbad_id_str = str(row.get("simbad_id", "")).strip()
    simbad_known = bool(simbad_id_str) and simbad_id_str.lower() != "nan"
    ned_name_str = str(row.get("ned_name", "")).strip()
    ned_known = bool(ned_name_str) and ned_name_str.lower() != "nan"
    gaia_known = False
    for col in row:
        lc = col.lower()
        if lc.startswith("gaia") and any(k in lc for k in ["source", "id", "match"]):
            val = row.get(col)
            if val is not None and not _isna(val) and str(val).strip() not in ("", "0", "False", "false"):
                gaia_known = True
                break

    # Flags. These are intentionally conservative so one broad condition cannot flood the report.
    flag_extreme_colour = (
        colour_jump_max > 2.8
        or abs(full_red_score) > 4.5
        or colour_smoothness > 4.0
    )

    if morphology_available:
        flag_likely_model_issue = (
            abs(psf) > 3.0
            or abs(psf_per_radius) > 0.50
            or conc > 9.0
            or petro <= 0
        )

        flag_probable_shred = (
            (petro > 18.0 and conc > 5.5)
            or (petro > 28.0)
            or (conc > 8.0 and mu > 23.5)
        )

        flag_possible_lsb = (
            mu > 24.2
            and 4.0 <= petro <= 13.5
            and 1.8 <= conc <= 6.5
            and abs(psf) < 2.0
            and abs(psf_per_radius) < 0.45
            and colour_jump_max < 2.8
            and not flag_probable_shred
            and not flag_likely_model_issue
        )

        flag_compact_red = (
            full_red_score > 3.5
            and petro < 4.5
            and conc > 2.5
            and not flag_likely_model_issue
        )
    else:
        flag_likely_model_issue = False
        flag_probable_shred = False
        flag_possible_lsb = False
        flag_compact_red = False

    flag_gaia_matched = gaia_known
    flag_catalogued = simbad_known or ned_known or gaia_known

    # Separate interesting weirdness from measurement/deblend risk.
    # anomaly_score from IsolationForest is usually more negative = weirder.
    weirdness_score = 0.0
    weirdness_score += max(-anomaly, 0.0) * 10.0
    weirdness_score += min(abs(full_red_score), 8.0) * 0.45
    if morphology_available:
        weirdness_score += min(colour_smoothness, 8.0) * 0.25
        weirdness_score += max(mu - 22.5, 0.0) * 0.35
        weirdness_score += max(min(conc, 7.0) - 2.5, 0.0) * 0.20

    if flag_possible_lsb:
        weirdness_score += 1.25
    if flag_compact_red:
        weirdness_score += 0.75
    if flag_extreme_colour:
        weirdness_score += 0.75

    artefact_risk = 0.0
    if flag_likely_model_issue:
        artefact_risk += 2.5
    if flag_probable_shred:
        artefact_risk += 2.0
    if morphology_available:
        if petro > 25.0:
            artefact_risk += 1.5
        elif petro > 18.0:
            artefact_risk += 0.75
        if conc > 9.0:
            artefact_risk += 1.5
        elif conc > 7.0:
            artefact_risk += 0.75
    if colour_jump_max > 3.5:
        artefact_risk += 0.75
    if morphology_available and abs(psf_per_radius) > 0.75:
        artefact_risk += 1.0

    # Slight penalty for already-catalogued sources, but do not bury them completely.
    if flag_catalogued:
        artefact_risk += 0.25

    review_score = weirdness_score - artefact_risk

    flags = []
    if flag_extreme_colour:
        flags.append("extreme_colour")
    if flag_likely_model_issue:
        flags.append("likely_model_issue")
    if flag_possible_lsb:
        flags.append("possible_lsb")
    if flag_compact_red:
        flags.append("compact_red")
    if flag_probable_shred:
        flags.append("probable_shred")
    if flag_gaia_matched:
        flags.append("gaia_matched")
    if flag_catalogued:
        flags.append("catalogued")

    if artefact_risk >= 5.0 or flag_likely_model_issue:
        triage_class = "artefact_risk"
    elif flag_probable_shred:
        triage_class = "probable_shred"
    elif morphology_available and petro > 18 and mu > 24:
        triage_class = "large_diffuse"
    elif flag_possible_lsb:
        triage_class = "possible_lsb"
    elif flag_compact_red:
        triage_class = "compact_red"
    elif flag_extreme_colour:
        triage_class = "extreme_colour"
    elif review_score >= 4.0 and not flag_catalogued:
        triage_class = "high_interest"
    else:
        triage_class = "mixed_anomaly"

    return {
        "colour_span": colour_span,
        "red_score": red_score,
        "full_red_score": full_red_score,
        "colour_curvature_gr_ri": colour_curvature_gr_ri,
        "colour_curvature_ri_iz": colour_curvature_ri_iz,
        "colour_smoothness": colour_smoothness,
        "colour_jump_max": colour_jump_max,
        "size_ratio_petro_r50": size_ratio_petro_r50,
        "r90_r50_width": r90_r50_width,
        "compactness_proxy": compactness_proxy,
        "diffuse_proxy": diffuse_proxy,
        "psf_per_radius": psf_per_radius,
        "surface_brightness_offset": surface_brightness_offset,
        "weirdness_score": weirdness_score,
        "artefact_risk": artefact_risk,
        "review_score": review_score,
        "triage_class": triage_class,
        "triage_flags": "; ".join(flags) if flags else "none",
        "flag_extreme_colour": int(flag_extreme_colour),
        "flag_likely_model_issue": int(flag_likely_model_issue),
        "flag_possible_lsb": int(flag_possible_lsb),
        "flag_compact_red": int(flag_compact_red),
        "flag_probable_shred": int(flag_probable_shred),
        "flag_gaia_matched": int(flag_gaia_matched),
        "flag_catalogued": int(flag_catalogued),
    }


# Ground truth for these is add_candidate_triage() above. Keep in sync if
# that formula changes, and bump DEFINITIONS_VERSION when it does.
METRIC_DEFINITIONS = [
    {
        "key": "review_score",
        "label": "Review score",
        "what": "Priority score GSS uses to rank candidates for human review. Higher ranks first.",
        "formula": "review_score = weirdness_score − artefact_risk",
        "why": "Combines how unusual an object is with how likely it is to just be a measurement artefact, so reviewers see the most promising cases first.",
        "interpret": "Higher is more worth reviewing. A high weirdness score can still net out low if artefact risk is also high.",
    },
    {
        "key": "weirdness_score",
        "label": "Weirdness",
        "what": "Composite measure of how statistically or physically unusual the object is.",
        "formula": "10 × max(−anomaly_score, 0) + 0.45 × min(|full_red_score|, 8) + 0.25 × min(colour_smoothness, 8) + 0.35 × max(mu_r − 22.5, 0) + 0.20 × max(min(concentration_r, 7) − 2.5, 0), plus fixed bonuses for possible_lsb (+1.25), compact_red (+0.75), and extreme_colour (+0.75) flags.",
        "why": "Turns the raw anomaly score plus colour/morphology diagnostics into one blended “how interesting is this” number.",
        "interpret": "Higher means more unusual by these diagnostics. It says nothing about whether the object is a real source or an artefact -- see artefact risk for that.",
    },
    {
        "key": "artefact_risk",
        "label": "Artefact risk",
        "what": "Estimate of how likely the object is a measurement or deblending artefact rather than a real unusual source.",
        "formula": "Sums fixed penalties for: likely_model_issue (+2.5), probable_shred (+2.0), large petroRad_r (+1.5 if >25, +0.75 if >18), high concentration_r (+1.5 if >9, +0.75 if >7), extreme colour_jump_max (+0.75 if >3.5), extreme psf_per_radius (+1.0 if >0.75), and a small penalty (+0.25) if already catalogued.",
        "why": "Isolation Forest anomalies are frequently deblending failures, saturated stars, or bad photometry rather than genuinely unusual astrophysics. This flags that risk explicitly instead of hiding it inside the weirdness score.",
        "interpret": "Higher means more likely to be junk. Values above ~5 (or any likely_model_issue flag) are auto-classified as triage_class = artefact_risk.",
    },
    {
        "key": "anomaly_score",
        "label": "Anomaly score",
        "what": "Raw Isolation Forest score for this object, fit against every object scanned so far (not just this tile).",
        "formula": "sklearn IsolationForest.score_samples() on RobustScaler-normalised features.",
        "why": "This is the underlying statistical outlier signal everything else in GSS's triage is built on top of.",
        "interpret": "More negative = more isolated from the rest of the population = more anomalous.",
    },
    {
        "key": "full_red_score",
        "label": "Full red score",
        "what": "Sum of all four adjacent-band colour indices.",
        "formula": "full_red_score = (u−g) + (g−r) + (r−i) + (i−z)",
        "why": "A simple, cheap proxy for how red the object's overall spectral energy distribution is.",
        "interpret": "Large positive values indicate an unusually red object; contributes to the extreme_colour and compact_red flags.",
    },
    {
        "key": "colour_smoothness",
        "label": "Colour smoothness",
        "what": "How smoothly colour changes across the five SDSS bands.",
        "formula": "colour_smoothness = |Δ(u−g, g−r)| + |Δ(g−r, r−i)| + |Δ(r−i, i−z)|",
        "why": "Real stellar/galaxy spectra usually vary smoothly band to band; abrupt jumps can indicate unusual astrophysics (e.g. strong emission features) or unreliable photometry.",
        "interpret": "Low = smooth spectral energy distribution. High = abrupt colour changes worth a closer look.",
    },
    {
        "key": "colour_jump_max",
        "label": "Colour jump max",
        "what": "The single largest colour index, in absolute value.",
        "formula": "colour_jump_max = max(|u−g|, |g−r|, |r−i|, |i−z|)",
        "why": "Catches a single extreme colour that a smoothness/sum metric could dilute.",
        "interpret": "Large values may indicate unusual spectra or bad photometry in one band.",
    },
    {
        "key": "psf_per_radius",
        "label": "PSF/radius",
        "what": "PSF-minus-model magnitude difference in r-band, scaled by the object's angular size.",
        "formula": "psf_per_radius = (psfMag_r − r) / petroRad_r",
        "why": "Indicates whether the object looks point-like (star-like) relative to its apparent size -- a useful artefact/star-vs-extended-source signal.",
        "interpret": "Large absolute values contribute to the likely_model_issue flag (deblending/PSF-model mismatch).",
    },
    {
        "key": "compactness_proxy",
        "label": "Compactness proxy",
        "what": "How concentrated the object's light is, relative to its overall size.",
        "formula": "compactness_proxy = concentration_r / petroRad_r",
        "why": "Cheap morphology diagnostic distinguishing compact sources from large diffuse ones.",
        "interpret": "Larger values indicate light concentrated into a small angular radius.",
    },
    {
        "key": "surface_brightness_offset",
        "label": "SB offset",
        "what": "Difference between mean surface brightness and point-source r-band magnitude.",
        "formula": "surface_brightness_offset = mu_r − r",
        "why": "A cross-check on the surface brightness (mu_r) calculation relative to the object's raw magnitude.",
        "interpret": "Large mu_r values (feeding a large positive offset) contribute to the possible_lsb (low surface brightness) flag.",
    },
    {
        "key": "triage_flags",
        "label": "Triage flags",
        "what": "Independent yes/no diagnostic flags, any of which may apply to the same object: extreme_colour, likely_model_issue, possible_lsb, compact_red, probable_shred, gaia_matched, catalogued.",
        "formula": "Each flag is a fixed threshold rule over the diagnostics above -- see triage.py:add_candidate_triage for the exact conditions.",
        "why": "Gives reviewers specific, checkable reasons an object was flagged, instead of just a single opaque score.",
        "interpret": "catalogued/gaia_matched are soft signals only -- known objects are not dropped, just slightly deprioritised (+0.25 artefact_risk).",
    },
    {
        "key": "triage_class",
        "label": "Triage class",
        "what": "Single best-fit bucket for this candidate, chosen from the flags and scores above in priority order (artefact risk first, then shred, then diffuse, LSB, compact red, extreme colour, high interest, else mixed_anomaly).",
        "formula": "See triage.py:add_candidate_triage for the exact if/elif ladder.",
        "why": "One label to sort/filter the review pack by, since a card can trip multiple flags at once.",
        "interpret": "Not mutually exclusive with the flags shown above -- it's a priority pick among the flags, not a merge of all of them.",
    },
]
