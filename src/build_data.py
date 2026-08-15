# -*- coding: utf-8 -*-
"""
Offline ETL pipeline for the Haifa election turnout map.

Loads the two raw nationwide data files, filters to Haifa, joins them,
computes turnout/party percentages, geocodes polling-station addresses
(cached + resumable), spatial-joins stations to neighborhood polygons,
aggregates to neighborhood level, and writes the processed GeoJSON files
that app.py reads at runtime.

Run manually (from the project root, using the project venv):
    .venv\\Scripts\\python.exe src\\build_data.py

Safe to re-run: already-geocoded addresses (success or failure) are served
from data/geocode_cache.json without re-hitting Nominatim.
"""

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from geopy.exc import GeocoderServiceError
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geocode_cache import load_cache, save_cache  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"

CSV_PATH = DATA_DIR / "kalpi_resuls_haifa.csv"
XLSX_PATH = DATA_DIR / "kalpi_locations_haifa.xlsx"
NEIGHBORHOODS_PATH = DATA_DIR / "haifa_neighborhoods.geojson"
GEOCODE_CACHE_PATH = DATA_DIR / "geocode_cache.json"
STREET_SYNONYMS_PATH = DATA_DIR / "haifa_street_synonyms.csv"

STATIONS_OUT = PROCESSED_DIR / "stations.geojson"
NEIGHBORHOODS_OUT = PROCESSED_DIR / "neighborhoods_agg.geojson"
FAILURES_OUT = PROCESSED_DIR / "geocode_failures.csv"

HAIFA_NAME = "חיפה"
NOMINATIM_USER_AGENT = "kalpi_geomap_haifa_app (contact: adigrauer@gmail.com)"

# Biases Nominatim results toward Haifa's bounding box, as two (lat, lon)
# corner points (geopy's expected format). This alone is NOT sufficient to
# guarantee results are actually in Haifa: it's a loose rectangle that also
# covers neighboring municipalities (Tirat Carmel, parts of the Krayot),
# so a street name that exists in one of those too (e.g. "בן צבי יצחק") can
# still resolve to the wrong city if it doesn't have a strong Haifa match.
# See HAIFA_BOUNDARY_BUFFER_DEG below for the actual correctness check.
HAIFA_VIEWBOX = [(32.70, 34.90), (32.90, 35.15)]

# The real fix for wrong-city matches: every geocode result is checked
# against Haifa's *actual* municipal shape (the union of its own 76
# neighborhood polygons), not just the loose viewbox rectangle above. A
# small buffer (~300m) tolerates points right at the edge — e.g. a real
# Haifa street whose OSM-tagged point falls just outside the neighborhood
# polygon covering it.
HAIFA_BOUNDARY_BUFFER_DEG = 0.003


def load_haifa_boundary():
    neigh = gpd.read_file(NEIGHBORHOODS_PATH)
    return neigh.union_all().buffer(HAIFA_BOUNDARY_BUFFER_DEG)

# Max alternate spellings to try (from the CBS street-synonym registry) when
# the address as given in the election data doesn't geocode directly, e.g.
# "שד" vs "שדרות", word order, or a missing/extra letter.
MAX_SYNONYM_ATTEMPTS = 6

# Confirmed (manually verified against live Nominatim results) OSM spellings
# for streets whose CBS-official name — used by the election data and the
# synonym registry itself — doesn't match how OSM actually tags the street.
# Not a general transliteration rule: OSM's Hebrew street names are
# crowd-sourced and inconsistent, so these are one-off, verified corrections,
# not a pattern to extrapolate from.
MANUAL_OSM_SPELLING_OVERRIDES = {
    "המימוני": "המיימוני",  # OSM uses ktiv male (extra י) vs the CBS ktiv haser spelling
    "גלאל אל דין": "ג'לאל א-דין",  # OSM tags the Arabic street name with a gershayim + hyphen
    "הגבורים": "הגיבורים",  # ktiv male; multiple same-named segments exist, but only this one
    # (Tel Amal / Neve Sha'anan-Yizre'elia) has any addr:housenumber-tagged buildings at all
    # (47-78, verified via Overpass), consistent with house 17 on the same sequence — the
    # other candidate segments (Wadi Salib, Halisa, Hadar) have no address data whatsoever.
    "שטרן יאיר": "יאיר שטרן",  # election data has the family/given name reversed; OSM (and
    # Google Maps, per user confirmation) only knows it in this order. Still not enough on
    # its own to geocode: OSM has no "highway" way for this street, only two bus-stop nodes
    # referencing it in a compound name at its intersection with העוגן — see the manual
    # cache entry for this address in geocode_cache.json.
    'שד הרא"ה': 'שד הרוא"ה',  # ktiv male (extra ו), per user confirmation. OSM's own name
    # tag is actually the full "שדרות הרוא\"ה" with no abbreviation, which free-text search
    # only matches once the "שד" prefix is stripped entirely — handled by also running
    # STREET_PREFIXES_TO_STRIP against this override, not just the original (misspelled)
    # street name.
}

# Street-name prefixes that the election data includes but OSM's `name` tag
# frequently omits (e.g. "שד אבא חושי" is tagged in OSM simply as "אבא חושי").
# Stripping these and retrying is a general fix, not a per-street override.
STREET_PREFIXES_TO_STRIP = ["שדרות ", "שד "]

NON_PARTY_COLS = [
    "סמל ועדה", "ברזל", "שם ישוב", "סמל ישוב", "קלפי",
    "ריכוז", "שופט", "בזב", "מצביעים", "פסולים", "כשרים",
]

# Consecutive geocode failures before we assume something systemic (network,
# rate-limiting, bad user-agent) is wrong rather than caching 400 addresses
# as permanently "failed".
MAX_CONSECUTIVE_FAILURES = 8


def load_street_variants():
    """Map every known Haifa street name (official or synonym, from the CBS
    street registry) to the set of all spellings sharing the same street code
    — e.g. 'אינשטיין אלברט', 'אינשטיין', and 'אלברט אינשטיין' all map to the
    same set. Used to retry geocoding under an alternate spelling when the
    address as given in the election data doesn't resolve directly."""
    df = pd.read_csv(STREET_SYNONYMS_PATH, encoding="utf-8-sig")
    name_to_variants = {}
    for _, group in df.groupby("official_code"):
        names = set(group["street_name"].str.strip())
        for name in names:
            name_to_variants[name] = names
    return name_to_variants


def split_street_and_number(address_field):
    """'שד הציונות,33' -> ('שד הציונות', '33'); 'כיכר ואצלב האוול' -> (that, '')."""
    if "," in address_field:
        street, _, number = address_field.rpartition(",")
        return street.strip(), number.strip()
    return address_field.strip(), ""


def build_geocode_candidates(address_field, street_variants):
    """Ordered list of full-address strings to try geocoding. The first
    candidate exactly reproduces the original (pre-fix) address format
    (comma directly between street and number) so previously-cached
    successful geocodes keep matching and aren't wastefully re-queried.
    Later candidates normalize the comma to a space (Nominatim frequently
    fails on 'street,number' but succeeds on 'street number'), try a manually
    verified OSM spelling if one is known, strip a "שד/שדרות" prefix OSM
    often omits, and fall back to known alternate spellings of the street
    name from the CBS registry."""
    street, number = split_street_and_number(address_field)
    candidates = [f"{address_field.strip()}, {HAIFA_NAME}, Israel"]

    def add(s):
        full = f"{s} {number}, {HAIFA_NAME}, Israel".strip() if number else f"{s}, {HAIFA_NAME}, Israel"
        if full not in candidates:
            candidates.append(full)

    add(street)

    override = MANUAL_OSM_SPELLING_OVERRIDES.get(street.strip())
    if override:
        add(override)
        for prefix in STREET_PREFIXES_TO_STRIP:
            if override.startswith(prefix):
                add(override[len(prefix):])

    for prefix in STREET_PREFIXES_TO_STRIP:
        if street.strip().startswith(prefix):
            add(street.strip()[len(prefix):])

    for variant in street_variants.get(street.strip(), []):
        if len(candidates) >= MAX_SYNONYM_ATTEMPTS:
            break
        add(variant)
    return candidates


def load_results():
    df = pd.read_csv(CSV_PATH, encoding="utf-8-sig", dtype={"קלפי": str})
    original_columns = list(df.columns)
    df = df[df["שם ישוב"] == HAIFA_NAME].copy()
    print(f"[build_data] Results CSV filtered to Haifa: {len(df)} rows")
    assert len(df) == 424, f"expected 424 Haifa rows in CSV, got {len(df)}"

    party_cols = [c for c in original_columns if c not in NON_PARTY_COLS]
    print(f"[build_data] Detected {len(party_cols)} party columns")
    assert len(party_cols) == 40, f"expected 40 party columns, got {len(party_cols)}"

    df["join_key"] = df["קלפי"].astype(float).round(1)
    return df, party_cols


def load_locations():
    df = pd.read_excel(XLSX_PATH, sheet_name="DataSheet")
    df = df[df["שם ישוב בחירות"] == HAIFA_NAME].copy()
    print(f"[build_data] Locations XLSX filtered to Haifa: {len(df)} rows")
    assert len(df) == 424, f"expected 424 Haifa rows in XLSX, got {len(df)}"

    df["join_key"] = df["סמל קלפי"].astype(float).round(1)
    keep_cols = [
        "join_key", "כתובת קלפי", "מקום קלפי",
        "נגישה", "נגישה מיוחדת", "בוחרי כנסת בפועל",
    ]
    return df[keep_cols]


def merge_results_and_locations(df_results, df_locations):
    df = df_results.merge(df_locations, on="join_key", how="inner", validate="one_to_one")
    print(f"[build_data] Merged results+locations: {len(df)} rows")
    assert len(df) == 424, f"expected 424 rows after merge, got {len(df)}"
    return df


def compute_percentages(df, party_cols):
    eligible = df["בזב"].replace(0, np.nan)
    valid = df["כשרים"].replace(0, np.nan)

    df["turnout_pct"] = df["מצביעים"] / eligible * 100
    for col in party_cols:
        df[f"{col}_pct"] = df[col] / valid * 100

    stats = df["turnout_pct"].describe()
    print(f"[build_data] turnout_pct stats:\n{stats}")
    assert not np.isinf(df["turnout_pct"]).any(), "found inf in turnout_pct"
    return df


def geocode_addresses(df, haifa_boundary):
    df["address_field"] = df["כתובת קלפי"].astype(str).str.strip()
    df["full_address"] = df["address_field"] + f", {HAIFA_NAME}, Israel"
    unique_fields = df["address_field"].drop_duplicates().tolist()
    print(f"[build_data] {len(unique_fields)} unique addresses to resolve "
          f"(out of {len(df)} stations)")

    street_variants = load_street_variants()
    cache = load_cache(GEOCODE_CACHE_PATH)
    geolocator = Nominatim(user_agent=NOMINATIM_USER_AGENT)
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.0, swallow_exceptions=False)

    consecutive_failures = 0
    new_lookups = 0
    for i, field in enumerate(unique_fields, start=1):
        candidates = build_geocode_candidates(field, street_variants)
        cache_key = candidates[0]  # matches the original (pre-fix) cache key format
        if cache_key in cache:
            continue
        new_lookups += 1

        result = None
        service_error = None
        for candidate in candidates:
            try:
                candidate_result = geocode(candidate, viewbox=HAIFA_VIEWBOX, bounded=True)
            except GeocoderServiceError as e:
                service_error = e
                break  # network/service issue — stop trying candidates, handle below
            if candidate_result and haifa_boundary.contains(
                Point(candidate_result.longitude, candidate_result.latitude)
            ):
                result = candidate_result
                break
            # Either no result, or a same-named street in a neighboring
            # municipality (the loose viewbox rectangle above doesn't
            # exclude those) — try the next candidate rather than accepting
            # a wrong-city match.

        if service_error is not None:
            # Distinct from Nominatim genuinely returning "not found": don't
            # cache anything for this address (retry it on the next run);
            # only count this toward the systemic-outage abort threshold.
            print(f"[build_data] Geocode service error for {field!r}: {service_error}")
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                raise RuntimeError(
                    f"{consecutive_failures} consecutive geocode service errors — "
                    "stopping before caching the rest of the run as permanently "
                    "failed. Check network connectivity / Nominatim availability "
                    "and re-run (already-cached addresses will be skipped)."
                ) from e
            continue

        if result:
            cache[cache_key] = {"lat": result.latitude, "lon": result.longitude, "status": "ok"}
        else:
            cache[cache_key] = {"status": "failed"}
        consecutive_failures = 0
        save_cache(cache, GEOCODE_CACHE_PATH)

        if new_lookups % 20 == 0:
            print(f"[build_data] Geocoded {i}/{len(unique_fields)} unique addresses...")

    print(f"[build_data] Geocoding done. {new_lookups} new lookups this run "
          f"({len(unique_fields) - new_lookups} served from cache).")

    df["lat"] = df["full_address"].map(lambda a: cache.get(a, {}).get("lat"))
    df["lon"] = df["full_address"].map(lambda a: cache.get(a, {}).get("lon"))

    n_ok = df["lat"].notna().sum()
    pct = n_ok / len(df) * 100
    print(f"[build_data] {n_ok}/{len(df)} stations geocoded successfully ({pct:.1f}%)")

    failed = df[df["lat"].isna()][["קלפי", "כתובת קלפי", "full_address"]]
    if len(failed):
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        failed.to_csv(FAILURES_OUT, index=False, encoding="utf-8-sig")
        print(f"[build_data] {len(failed)} failed geocodes written to {FAILURES_OUT}")
    elif FAILURES_OUT.exists():
        # Remove a stale file from a previous run — otherwise app.py's "Data
        # notes" expander would keep reporting addresses that now geocode
        # successfully, since it only checks FAILURES_OUT.exists().
        FAILURES_OUT.unlink()
        print(f"[build_data] All addresses geocoded; removed stale {FAILURES_OUT}")

    return df


def build_station_gdf(df):
    geocoded = df[df["lat"].notna() & df["lon"].notna()].copy()
    lat_ok = geocoded["lat"].between(32.70, 32.90).all()
    lon_ok = geocoded["lon"].between(34.90, 35.15).all()
    if not (lat_ok and lon_ok):
        print("[build_data] WARNING: some geocoded points fall outside Haifa's "
              "expected bounding box — spot-check data/geocode_cache.json")

    gdf = gpd.GeoDataFrame(
        geocoded,
        geometry=gpd.points_from_xy(geocoded["lon"], geocoded["lat"]),
        crs="EPSG:4326",
    )
    return gdf


def join_neighborhoods(gdf_stations):
    gdf_neigh = gpd.read_file(NEIGHBORHOODS_PATH)
    if gdf_neigh.crs is None:
        gdf_neigh = gdf_neigh.set_crs("EPSG:4326")
    print(f"[build_data] Loaded {len(gdf_neigh)} neighborhood polygons")

    joined = gpd.sjoin(
        gdf_stations, gdf_neigh[["SchName", "geometry"]], how="left", predicate="within"
    )
    joined = joined.rename(columns={"SchName": "neighborhood"}).drop(columns=["index_right"])

    n_matched = joined["neighborhood"].notna().sum()
    pct = n_matched / len(joined) * 100
    print(f"[build_data] {n_matched}/{len(joined)} stations matched to a neighborhood ({pct:.1f}%)")
    if pct < 95:
        print("[build_data] WARNING: neighborhood match rate below 95% — "
              "investigate CRS / boundary edge cases before trusting the "
              "neighborhood layer.")

    return joined, gdf_neigh


def aggregate_neighborhoods(gdf_stations, gdf_neigh, party_cols):
    sum_cols = ["בזב", "מצביעים", "כשרים"] + party_cols
    agg = gdf_stations.groupby("neighborhood")[sum_cols].sum()
    agg["n_stations"] = gdf_stations.groupby("neighborhood")["קלפי"].count()

    eligible = agg["בזב"].replace(0, np.nan)
    valid = agg["כשרים"].replace(0, np.nan)
    agg["turnout_pct"] = agg["מצביעים"] / eligible * 100
    for col in party_cols:
        agg[f"{col}_pct"] = agg[col] / valid * 100

    gdf_out = gdf_neigh.merge(agg, left_on="SchName", right_index=True, how="left")
    n_with_data = gdf_out["n_stations"].notna().sum()
    print(f"[build_data] {n_with_data}/{len(gdf_out)} neighborhoods have at least one matched station")
    return gdf_out


def write_outputs(gdf_stations, gdf_neigh_agg):
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    stations_out = gdf_stations.drop(columns=["full_address", "address_field", "join_key"], errors="ignore")
    stations_out.to_file(STATIONS_OUT, driver="GeoJSON")
    print(f"[build_data] Wrote {len(stations_out)} stations to {STATIONS_OUT}")

    gdf_neigh_agg.to_file(NEIGHBORHOODS_OUT, driver="GeoJSON")
    print(f"[build_data] Wrote {len(gdf_neigh_agg)} neighborhoods to {NEIGHBORHOODS_OUT}")

    reloaded_stations = gpd.read_file(STATIONS_OUT)
    reloaded_neigh = gpd.read_file(NEIGHBORHOODS_OUT)
    assert len(reloaded_stations) == len(stations_out), "station output row count mismatch on reload"
    assert len(reloaded_neigh) == len(gdf_neigh_agg), "neighborhood output row count mismatch on reload"
    print("[build_data] Verified both outputs reload with matching row counts.")


def main():
    df_results, party_cols = load_results()
    df_locations = load_locations()
    df = merge_results_and_locations(df_results, df_locations)
    df = compute_percentages(df, party_cols)
    haifa_boundary = load_haifa_boundary()
    df = geocode_addresses(df, haifa_boundary)

    gdf_stations = build_station_gdf(df)
    gdf_stations, gdf_neigh = join_neighborhoods(gdf_stations)
    gdf_neigh_agg = aggregate_neighborhoods(gdf_stations, gdf_neigh, party_cols)

    write_outputs(gdf_stations, gdf_neigh_agg)
    print("[build_data] Done.")


if __name__ == "__main__":
    main()
