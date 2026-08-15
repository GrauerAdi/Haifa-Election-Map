# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An interactive map of Haifa's 2022 Knesset (25th) election turnout, station-by-station
and neighborhood-by-neighborhood, built with Streamlit + Folium. The full design spec
(data facts, party-code mapping, verification checklist) lives at
`C:\Users\adigr\.claude\plans\goal-build-a-web-distributed-teacup.md` — read it before
making structural changes. `FUTURE_TASKS.md` tracks session-by-session status, known
limitations, and why past bugs happened; check it first when resuming work.

## Commands

```bash
python setup_env.py                        # one-time: creates .venv, installs requirements.txt into it
.venv\Scripts\python.exe src\build_data.py # run the ETL pipeline (offline, writes data/processed/*.geojson)
.venv\Scripts\streamlit.exe run app.py     # run the map app (reads data/processed/*.geojson only)
```

There is no lint/test suite in this project. `requirements.txt` is a `pip freeze`
snapshot of the `.venv` — regenerate it the same way (`pip freeze > requirements.txt`)
after adding a dependency, don't hand-edit version pins.

App-only changes (styling, popups, colors) don't need a `build_data.py` rerun; only
changes to the raw data files, geocoding logic, or neighborhood boundaries do.

**No browser is available in this dev environment.** `app.py` behavior is verified here
via `streamlit.testing.v1.AppTest` (runs the script headlessly, catches exceptions,
inspects rendered elements) — but that only proves the script doesn't crash, not that
the UI looks or behaves right. Several real bugs (legend layout, popup pager buttons
not actually working) only surfaced once the user viewed it live — treat `AppTest`
green as necessary, not sufficient, and expect a round of live-feedback fixes after any
UI change.

## Architecture

**Two-stage pipeline, hard separation between offline ETL and the app:**

- `src/build_data.py` does all the slow/heavy work once, offline: loads the raw data,
  geocodes addresses, spatial-joins to neighborhoods, and writes two GeoJSON files to
  `data/processed/`.
- `app.py` (Streamlit) only ever reads those two pre-built GeoJSON files. It must never
  geocode or spatial-join at runtime — Streamlit reruns the whole script on every widget
  interaction, so anything slow belongs in `build_data.py`, not `app.py`.

### Data sources and joins

- `data/kalpi_resuls_haifa.csv` and `data/kalpi_locations_haifa.xlsx` are **nationwide**
  Israeli election files despite their filenames — both must be filtered to
  `שם ישוב == "חיפה"` (424 of ~12k/~11.7k rows) before use.
- The two files join on kalpi (polling station) ID, but the CSV stores it as clean text
  (`קלפי`) while the XLSX stores it as a float with binary imprecision (`סמל קלפי`, e.g.
  `1.1000000000000001`). The join key must be `round(float(x), 1)` on both sides, or the
  merge silently drops rows.
- The CSV's 40 party-vote columns are Hebrew ballot-letter codes (`מחל`, `פה`, `שס`, ...),
  not party names. `src/party_map.py` maps the ~11 major parties to real names for
  display; everything else falls back to the raw ballot letter. `NON_PARTY_COLS` in
  `build_data.py` is the fixed list of non-party columns — party columns are derived as
  the set difference, not hand-enumerated, so an unexpected schema change fails loudly
  (assertion on count) rather than silently mis-parsing.
- Neighborhood-level percentages are recomputed from summed raw vote counts
  (`sum(votes)/sum(valid)*100`), never averaged from per-station percentages — averaging
  percentages across stations with different voter counts would misweight small stations
  equally with large ones. The same weighted-aggregate logic is reused in `app.py` for
  merged multi-kalpi markers (`aggregate_group_metric`) — see below.
- All percentage calculations guard division by zero by replacing a zero denominator
  with `NaN` before dividing (`df["בזב"].replace(0, np.nan)`), not with `np.where` +
  raw division, to avoid spurious `RuntimeWarning`s while still producing `NaN` (not
  `inf`) for stations with zero eligible/valid votes. `NaN` metrics render as grey
  ("no data") in the app rather than being dropped.

### Geocoding

Neither raw file has coordinates; addresses (`כתובת קלפי`) are geocoded via Nominatim.
This turned out to be the hardest part of the pipeline — three distinct real bugs were
found and fixed here (full narrative in `FUTURE_TASKS.md`); the durable rules that came
out of it:

- Results are cached to `data/geocode_cache.json`, keyed by the *original* (pre-any-fix)
  full address string, written to disk after every single lookup (not batched), so
  re-running the pipeline never re-hits Nominatim for an address already resolved —
  necessary given Nominatim's ~1 req/sec free-tier limit.
- `geopy`'s `RateLimiter` defaults to `swallow_exceptions=True`, which makes a transient
  network error indistinguishable from Nominatim genuinely returning "not found" — the
  code passes `swallow_exceptions=False` and handles `GeocoderServiceError` separately.
  **Only cache an address as `"status": "failed"` when Nominatim genuinely returns no
  result**; a service exception must not be cached as a permanent failure.
- Addresses that fail as given are retried under alternate spellings
  (`build_geocode_candidates`, `load_street_variants`) sourced from
  `data/haifa_street_synonyms.csv` — Israel's CBS official street-name registry, which
  maps every official street name to its known synonym spellings (abbreviation
  expansions like `שד`→`שדרות`, word-order variants, etc.). This recovered a meaningful
  chunk of otherwise-unresolvable real addresses.
- Two more candidate-generation steps in `build_geocode_candidates`, added after
  diagnosing 8 originally-failing addresses directly against live Nominatim (not
  guessed at): `STREET_PREFIXES_TO_STRIP` strips a `שד`/`שדרות` prefix the election data
  includes but OSM's `name` tag often omits (general fix, not per-street); and
  `MANUAL_OSM_SPELLING_OVERRIDES` is a small, explicitly one-off dict for cases where
  OSM's crowd-sourced Hebrew spelling differs from the CBS-official one (ktiv male vs.
  ktiv haser, punctuation) in a way the CBS synonym registry itself doesn't capture —
  each entry was individually verified against a live Nominatim result before being
  added, not derived from a general transliteration rule. Recovered 22 of 35
  previously-failing kalpiyot (one more, `שטרן יאיר`→`יאיר שטרן`, was a word-order
  reversal rather than a spelling variant); one further group (`כיכר ואצלב האוול`, 5
  kalpiyot) was recovered not from the address at all but from `מקום קלפי` (the venue
  name column) — the plaza itself has no OSM presence, but its venue name named a real
  school, found via an Overpass proximity search (`amenity=school` within 1.5km of the
  neighborhood's suburb node) since the school also has no OSM `name` tag to text-search
  for. **All 424 stations now geocode successfully (100%)** — the last failing address,
  `שד הרא"ה` → OSM's `שדרות הרוא"ה`, was the same ktiv-male pattern, confirmed by the
  user against Google Maps; it needed `STREET_PREFIXES_TO_STRIP` to also run against the
  override spelling (not just the original), since OSM's untruncated name only matched
  once the "שד" prefix was stripped from the *corrected* spelling too.
- **A street name can have multiple disconnected same-named segments across Haifa**
  (`הגבורים`/`הגיבורים` has 4+, in different neighborhoods) — a plain geocode call is
  ambiguous, and geopy's `exactly_one=True` result is **not guaranteed to match**
  Nominatim's own multi-result ranking (confirmed directly: the single-result call
  picked a different, unevidenced segment than the top of a multi-result search for the
  same query). Don't trust "whatever the first live geocode call returns" for an
  ambiguous street name. Disambiguate using hard evidence instead — an Overpass query
  for buildings actually tagged `addr:street=<name>` reveals which segment has real
  address data — then write the verified coordinate directly into
  `data/geocode_cache.json` rather than relying on a live lookup to reproduce it.
- Every geocode result is validated against Haifa's *actual* municipal shape — the union
  of the neighborhood polygons themselves, buffered ~300m (`load_haifa_boundary`,
  `HAIFA_BOUNDARY_BUFFER_DEG`) — not just the loose `HAIFA_VIEWBOX` rectangle passed to
  Nominatim. The viewbox alone also covers neighboring municipalities (Tirat Carmel,
  parts of the Krayot), so a street name existing there too could silently resolve to the
  wrong city; the boundary check rejects that and retries the next candidate spelling.
- Failures are written to `data/processed/geocode_failures.csv` and surfaced in the
  app's "Data notes" expander — but note that only explains *why* a station is missing,
  it doesn't indicate *where* on the map it should have been.

### Neighborhood boundaries

`data/haifa_neighborhoods.geojson` (76 polygons, property `SchName`) comes from Haifa's
municipal open-data portal, not GovMap — GovMap's layer is a JS-rendered SPA requiring
API registration, so the municipal open-data GeoJSON was substituted as a directly
downloadable equivalent. Station-to-neighborhood assignment is a point-in-polygon
spatial join (`predicate="within"`), not reverse geocoding. This same polygon union also
serves as Haifa's ground-truth boundary for the geocode validation above.

### `app.py` map rendering

- **One marker per exact coordinate, not per kalpi.** Most polling addresses host
  several kalpi sub-stations that geocode identically (up to 11 at one shared point).
  `group_stations_by_exact_coord` groups them; the marker is colored by their combined
  weighted-aggregate metric (`aggregate_group_metric`), and its popup is a self-contained
  prev/next pager (`build_multi_kalpi_popup_html`) over one panel per kalpi rather than
  stacking indistinguishable overlapping markers. The pager buttons use plain
  `this.closest('.kalpi-pager')` DOM traversal, not a shared named JS function —
  `st_folium` renders the map inside its own custom-component iframe, and a
  separately-injected `<script>` defining a global function isn't reliably in scope for
  inline `onclick` handlers by click time. Self-contained inline logic has no such
  dependency.
- Station markers are `folium.CircleMarker` with a **fixed pixel radius**
  (`STATION_RADIUS_PIXELS`), deliberately constant across zoom levels — a meter-based
  version (`folium.Circle`) was tried and looked correct-but-oversized once zoomed in to
  street level.
- Neighborhood polygons are added to the map *before* stations — Leaflet stacks layers
  in add-order (last added = topmost) — so station dots render on top instead of being
  hidden under the polygon fill.
- Neighborhood info uses `folium.GeoJsonPopup` (click-triggered), not `GeoJsonTooltip`
  (hover-triggered) — a tooltip fired constantly while just panning the map.
- The color scale (`#b2182b` red → `#1a1a1a` near-black → `#1b7837` green) is
  deliberately dark/saturated with no yellow — OSM's default tiles use pale
  cream/tan/green/blue/orange, and a lighter scale (tried first) blended into the
  basemap and was hard to see.
- branca's `LinearColormap` legend hardcodes its Leaflet control position to `topright`
  inside its own JS template (`color_scale.js`) with no constructor kwarg to change
  it — `_LEGEND_TEMPLATE_TOPLEFT` in `app.py` is that same template with the one string
  swapped to `topleft`, since otherwise it stacked on top of `LayerControl` in the same
  corner. Also note: shrinking the legend's `height` below the ~40px default breaks the
  template's internal geometry (`height - 30` for the gradient bar's own height, etc.) —
  only `width` is safe to adjust.
