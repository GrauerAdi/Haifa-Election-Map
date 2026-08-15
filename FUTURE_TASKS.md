# Continuation notes

The approved plan is at `C:\Users\adigr\.claude\plans\goal-build-a-web-distributed-teacup.md`
(full spec: data facts, project structure, party mapping, verification steps).
`CLAUDE.md` documents the architecture. This file tracks status and known
limitations as of the last session.

## Status: core build complete, UI iterated on with live user feedback

- `setup_env.py`, `requirements.txt` — venv bootstrap, verified working.
- `data/haifa_neighborhoods.geojson` — 76 neighborhood polygons from Haifa's
  municipal open-data portal.
- `data/haifa_street_synonyms.csv` — CBS official street-name registry
  (official + synonym spellings per street), used as a geocoding fallback.
- `src/party_map.py`, `src/geocode_cache.py`, `src/build_data.py` — full ETL
  pipeline, run end-to-end multiple times as bugs were found and fixed (see
  below).
- `app.py` — Streamlit map app, iterated on with the user actually viewing it
  live in a browser (this environment has no browser access itself — all my
  own verification is via `streamlit.testing.v1.AppTest`, which the user's
  visual feedback caught real issues that didn't show up there, e.g. the
  legend layout and the pager buttons not actually working). Current design:
  - OSM basemap, dark saturated red→black→green color scale (deliberately
    avoids OSM's own pale cream/green/blue/orange tile colors).
  - Station markers: white-outlined `CircleMarker`, fixed pixel radius
    (`STATION_RADIUS_PIXELS`), constant across zoom — NOT meter-based (tried
    that, looked huge when zoomed in; also tried jittering overlapping
    markers apart, replaced by the grouping approach below per user request).
  - **One marker per exact coordinate, not per kalpi.** Most polling
    addresses host several kalpi sub-stations that geocode identically
    (up to 11 at one point). `group_stations_by_exact_coord` groups them;
    the marker is colored by their combined weighted-aggregate metric
    (`aggregate_group_metric`); its popup is a self-contained prev/next
    pager (`build_multi_kalpi_popup_html`) over one panel per kalpi. The
    pager's buttons use pure `this.closest('.kalpi-pager')` DOM traversal —
    an earlier version that called a shared globally-defined JS function
    didn't work once actually clicked in the browser (`st_folium` renders
    the map in its own component iframe; a separately-injected `<script>`
    defining a global function wasn't reliably in scope for inline onclick
    handlers by click time). Self-contained onclick logic has no such
    dependency and was confirmed working by the user.
  - Station popups show the full address, all counts, and **every** party
    sorted by vote count descending (not just top 3) with 3-decimal-place
    percentages (not rounded to 1) — both were explicit user requests.
  - Neighborhood polygons are added to the map *before* stations (Leaflet
    stacks by add-order) so stations render on top, not hidden underneath.
  - Neighborhood info uses `GeoJsonPopup` (click), not `GeoJsonTooltip`
    (hover) — a tooltip fired constantly while just panning the map.
  - branca's colormap legend hardcodes Leaflet position `topright` in its
    own JS template (no constructor kwarg for this) — overridden to
    `topleft` via a copied-and-edited template string, since it was
    otherwise stacking on top of `LayerControl` in the same corner.

## Current pipeline results (last run)

- **424/424 Haifa stations geocoded successfully (100%)** —
  `data/processed/geocode_failures.csv` is now empty.
- 424/424 geocoded stations (100%) matched to one of the 76 neighborhoods
  (51/76 neighborhoods have ≥1 matched station).
- Turnout % sanity-checked: mean 55.4%, range 19–81% — plausible.

## Known limitations / things worth knowing

1. **All 35 originally-failing addresses were eventually recovered** (see
   items 2-6 below) — the last one, `שד הרא"ה,10` (4 kalpiyot), turned out to
   be the same ktiv-male issue as `הגבורים`/`המימוני`: OSM's actual spelling
   is `שדרות הרוא"ה` (extra `ו`), confirmed by the user against Google Maps
   and verified live against Nominatim before accepting. Added to
   `MANUAL_OSM_SPELLING_OVERRIDES` as `'שד הרא"ה': 'שד הרוא"ה'`. This one also
   needed the `STREET_PREFIXES_TO_STRIP` logic to run against the *override*
   spelling, not just the original — OSM's full untruncated name only
   matched once the "שד" prefix was stripped from the corrected spelling too
   (`build_geocode_candidates` now does this). No known geocode gaps remain;
   `data/processed/geocode_failures.csv` is empty and the "Data notes"
   expander should show no excluded stations.
2. **21 previously-failed addresses recovered this session** (35→14) by
   diagnosing each of the original 8 failing street groups directly against
   live Nominatim (raw HTTP queries, not just re-running the pipeline) rather
   than guessing at fixes. Two distinct root causes, both now handled in
   `build_geocode_candidates` (`src/build_data.py`):
   - **OSM tags some streets without the "שד"/"שדרות" (boulevard) prefix**
     that the election data includes (e.g. `שד אבא חושי` is tagged in OSM
     simply as `אבא חושי`). Fixed generally via `STREET_PREFIXES_TO_STRIP` —
     strips the prefix and retries, not a one-off patch. Recovered
     `שד אבא חושי,15` (8 kalpiyot) and `שד סיני,13` (6 kalpiyot).
   - **OSM's Hebrew street spelling sometimes differs from the CBS official
     spelling** the election data and the synonym registry both use (ktiv
     male vs. ktiv haser, or punctuation) — and this isn't something the CBS
     registry itself captures, so the existing synonym fallback couldn't
     catch it. Fixed via a small, explicitly one-off
     `MANUAL_OSM_SPELLING_OVERRIDES` dict (not a general transliteration
     rule — each entry was individually verified against a live Nominatim
     result before being added). Recovered `המימוני,2` → OSM's `המיימוני`
     (2 kalpiyot) and `גלאל אל דין,3` → OSM's `ג'לאל א-דין` (5 kalpiyot).
   All 4 recovered addresses' resulting coordinates were spot-checked to
   land inside the Haifa boundary (Hadar, Kababir, Giv'at Downes, Achuzat
   Shmuel) before accepting — same discipline as the wrong-city fix below.
3. **`הגבורים,17` (4 kalpiyot) recovered in a follow-up fix, with an extra
   wrinkle worth remembering.** Same ktiv-male issue as above (official
   `הגבורים` → OSM's `הגיבורים`), added to `MANUAL_OSM_SPELLING_OVERRIDES` —
   but this street name has **multiple disconnected same-named segments** in
   different Haifa neighborhoods (Wadi Salib, Tel Amal/Neve Sha'anan-
   Yizre'elia, Halisa, Hadar), so a plain geocode call is ambiguous.
   Disambiguated using hard evidence, not Nominatim's relevance ranking: an
   Overpass query for buildings actually tagged `addr:street=הגיבורים` found
   real house numbers (47-78) clustered in exactly one segment (Tel Amal),
   consistent with a web-search-confirmed nearby address (the associated
   synagogue at `הגיבורים 25`) on the same numbering sequence toward 17.
   **Important gotcha found in the process:** geopy's `geocode(exactly_one=
   True)` for this address returned a *different* (unevidenced, Hadar-area)
   segment than the one Nominatim's own multi-result search ranked first —
   i.e. the single-result and multi-result endpoints disagreed, so relying on
   "whatever the first successful live geocode returns" was not safe here.
   The final coordinate (32.7997052, 35.0146609, matched to neighborhood
   `נוה פז`) was written directly into `data/geocode_cache.json` rather than
   left to a live lookup, since the cache is the trusted source once
   verified. If another street with multiple disconnected segments turns up,
   don't trust a single live geocode call — cross-check against Overpass
   `addr:street`-tagged buildings first.
4. **`שטרן יאיר,20` (1 kalpi) recovered — word-order reversal, and OSM has
   no road geometry for it at all.** The election data has the surname
   before the given name (`שטרן יאיר` = "Stern Yair"); the real street,
   confirmed by the user against Google Maps, is `יאיר שטרן` ("Yair Stern"),
   added to `MANUAL_OSM_SPELLING_OVERRIDES`. But the override alone isn't
   enough to geocode it: OSM has **no `highway` way tagged with this name at
   all** in Haifa, only two bus-stop nodes whose compound name references it
   at its intersection with `העוגן` (confirmed via Overpass; the nodes'
   actual `object:street` tag is `העוגן`, not `יאיר שטרן`). The intersection
   point of those two nodes (32.827684, 34.9638295 → `עין הים` neighborhood)
   was written directly into `data/geocode_cache.json`, same as the
   `הגבורים` fix above — street/intersection-level precision, not an exact
   house number, since no house-level (or even road-level) OSM data exists
   for this street to resolve to.
5. **`כיכר ואצלב האוול` (5 kalpiyot, Neot Peres) recovered by geocoding the
   venue, not the address.** This plaza name has no OSM presence at all under
   any spelling — but the user pointed out `מקום קלפי` (the venue-name column
   in `kalpi_locations_haifa.xlsx`, separate from `כתובת קלפי`) has
   `בי"ס נאות פרס` ("Neot Peres School") for this kalpi, and that the school
   itself is findable on Google Maps even though the plaza address isn't.
   Nominatim free-text search for the school name still returned nothing (it
   has no OSM `name` tag), so it took an Overpass proximity query instead:
   searching for any `amenity=school` within 1.5km of the "נאות פרס" suburb
   node found exactly one unnamed school building — strong evidence given
   there's no other candidate at any radius tried. Its coordinate
   (32.7822014, 34.9677353, in the combined-name polygon
   `מרכז הקונגרסים, נאות פרס, יצחק נבון`) was written directly into
   `data/geocode_cache.json`. **General technique worth remembering:** when
   an address itself is unmappable, check whether `מקום קלפי` names a
   findable venue, then locate it via Overpass proximity search around a
   known nearby place/suburb node — venue *name* text search alone likely
   won't work (schools are often untagged with `name` in OSM), but
   *category* + *proximity* search often will.
6. **`שד הרא"ה,10` (4 kalpiyot) — the last failing address, recovered
   last.** Same ktiv-male pattern as `הגבורים`/`המימוני`: OSM's actual
   spelling is `שדרות הרוא"ה` (extra `ו`), which the user confirmed against
   Google Maps and which was verified live against Nominatim (resolves
   cleanly to Kiryat Chaim — Kiryat Chaim/Kiryat Shmuel district — with no
   ambiguity) before accepting. Added to `MANUAL_OSM_SPELLING_OVERRIDES` as
   `'שד הרא"ה': 'שד הרוא"ה'`. Needed one code change beyond a simple dict
   entry: `STREET_PREFIXES_TO_STRIP` previously only ran against the
   *original* (misspelled) street name, but OSM's untruncated name
   (`שדרות הרוא"ה`) only matches once the "שד" prefix is stripped from the
   *corrected* spelling too — `build_geocode_candidates` now does both.
   **All 424 stations now geocode successfully; `geocode_failures.csv` is
   empty.**
7. **Wrong-city geocode matches — found by the user, fixed in an earlier
   session.**
   The Nominatim `viewbox`/`bounded` restriction used a loose rectangular
   bounding box that also covers neighboring municipalities (Tirat Carmel,
   parts of the Krayot). A street name existing in one of those too (e.g.
   `בן צבי יצחק`) could resolve there instead of Haifa. Fixed by validating
   every geocode result against Haifa's *actual* shape — the union of its
   own 76 neighborhood polygons, buffered ~300m (`load_haifa_boundary`,
   `HAIFA_BOUNDARY_BUFFER_DEG`) — and rejecting/retrying-next-candidate if a
   result falls outside it. Confirmed zero cached "ok" entries now fall
   outside this boundary. If new wrong-city cases turn up later, the
   diagnostic pattern that worked: `Point(lon,lat).distance(boundary)` on
   every cached "ok" entry, sorted descending — real Haifa streets near a
   polygon edge are ~0.01deg (~1km) away; wrong-city matches were 0.01–0.05deg
   (1–5km).
8. **Party name mapping covers ~11 major parties** (`src/party_map.py`);
   remaining ballot-letter codes display as-is. Not verified against the
   official CEC ballot-letter list — spot check before treating as
   authoritative if this matters for a real deliverable.
9. **Some "grey" (zero-station) neighborhoods are real gaps, others are
   plausibly just underserved by their own dedicated kalpi.** Investigated
   two on user request: `נאות פרס` (the combined polygon
   `מרכז הקונגרסים, נאות פרס, יצחק נבון`) is no longer grey — its 5 kalpiyot
   at `כיכר ואצלב האוול` were recovered via the venue-geocoding fix above
   (item 5). `סביוני הכרמל` has no obviously-matching failed address, but its
   statistical-district siblings (`הוד הכרמל`, `רמת גולדה`, `רמת אלמוגי`) do
   have stations — the working theory (not proven) is that this small
   (~0.41 km²) enclave has no dedicated public building to host a kalpi and
   its residents vote at a
   station in one of those neighboring sub-areas. Not something the pipeline
   can resolve without a voter-to-station assignment dataset it doesn't have.

## How to resume

Tell Claude: "continue from FUTURE_TASKS.md".
