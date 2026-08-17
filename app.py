# -*- coding: utf-8 -*-
"""Streamlit app: interactive map of Haifa election turnout, station and neighborhood level."""

import sys
from pathlib import Path

import branca.colormap as cm
import folium
import geopandas as gpd
import numpy as np
import pandas as pd
import streamlit as st
from jinja2 import Template
from streamlit_folium import st_folium

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from bloc_map import BLOC_LABELS, full_blocs  # noqa: E402
from party_map import get_party_name  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent
STATIONS_PATH = PROJECT_ROOT / "data" / "processed" / "stations.geojson"
NEIGHBORHOODS_PATH = PROJECT_ROOT / "data" / "processed" / "neighborhoods_agg.geojson"
FAILURES_PATH = PROJECT_ROOT / "data" / "processed" / "geocode_failures.csv"

HAIFA_CENTER = [32.794, 34.989]
NO_DATA_COLOR = "#999999"
# Fixed screen-pixel radius (not meters) — deliberately constant across zoom
# levels. A meter-based version grew correctly-but-too-large at high zoom (a
# real building-footprint-sized circle dwarfs the street at zoom 18+).
STATION_RADIUS_PIXELS = 5

# Most polling addresses host multiple kalpi sub-stations (e.g. 405.1/405.2/
# 405.3 at the same building) — they geocode to the identical point. Rather
# than displacing them, one marker is placed at the true shared location and
# its popup lets you page through every kalpi at that address.
#
# The pager logic is inlined into each button's onclick and navigates purely
# via DOM traversal from the button itself (`this.closest(...)`) rather than
# calling a named global function — st_folium renders the map inside its own
# custom-component iframe, and a separately injected <script> defining a
# global function isn't guaranteed to execute in the scope those inline
# handlers run in by the time they're clicked. Self-contained onclick
# attributes have no such dependency: they only need the DOM nodes to exist,
# which they must, since the popup itself is already visibly rendering.

# branca's LinearColormap legend hardcodes its Leaflet control position to
# 'topright' (same corner folium.LayerControl defaults to), so the two
# overlap. This is branca's own color_scale.js template with that one string
# swapped to 'topleft' — there's no constructor/position kwarg to do this,
# so the template has to be overridden directly on the instance.
_LEGEND_TEMPLATE_TOPLEFT = """
{% macro script(this, kwargs) %}
    var {{this.get_name()}} = {};

    {%if this.color_range %}
    {{this.get_name()}}.color = d3.scale.threshold()
              .domain({{this.color_domain}})
              .range({{this.color_range}});
    {%else%}
    {{this.get_name()}}.color = d3.scale.threshold()
              .domain([{{ this.color_domain[0] }}, {{ this.color_domain[-1] }}])
              .range(['{{ this.fill_color }}', '{{ this.fill_color }}']);
    {%endif%}

    {{this.get_name()}}.x = d3.scale.linear()
              .domain([{{ this.color_domain[0] }}, {{ this.color_domain[-1] }}])
              .range([0, {{ this.width }} - 50]);

    {{this.get_name()}}.legend = L.control({position: 'topleft'});
    {{this.get_name()}}.legend.onAdd = function (map) {var div = L.DomUtil.create('div', 'legend'); return div};
    {{this.get_name()}}.legend.addTo({{this._parent.get_name()}});

    {{this.get_name()}}.xAxis = d3.svg.axis()
        .scale({{this.get_name()}}.x)
        .orient("top")
        .tickSize(1)
        .tickValues({{ this.tick_labels }});

    {{this.get_name()}}.svg = d3.select(".legend.leaflet-control").append("svg")
        .attr("id", 'legend')
        .attr("width", {{ this.width }})
        .attr("height", {{ this.height }});

    {{this.get_name()}}.g = {{this.get_name()}}.svg.append("g")
        .attr("class", "key")
        .attr("fill", {{ this.text_color | tojson }})
        .attr("transform", "translate(25,16)");

    {{this.get_name()}}.g.selectAll("rect")
        .data({{this.get_name()}}.color.range().map(function(d, i) {
          return {
            x0: i ? {{this.get_name()}}.x({{this.get_name()}}.color.domain()[i - 1]) : {{this.get_name()}}.x.range()[0],
            x1: i < {{this.get_name()}}.color.domain().length ? {{this.get_name()}}.x({{this.get_name()}}.color.domain()[i]) : {{this.get_name()}}.x.range()[1],
            z: d
          };
        }))
      .enter().append("rect")
        .attr("height", {{ this.height }} - 30)
        .attr("x", function(d) { return d.x0; })
        .attr("width", function(d) { return d.x1 - d.x0; })
        .style("fill", function(d) { return d.z; });

    {{this.get_name()}}.g.call({{this.get_name()}}.xAxis).append("text")
        .attr("class", "caption")
        .attr("y", 21)
        .attr("fill", {{ this.text_color | tojson }})
        .text({{ this.caption|tojson }});
{% endmacro %}
"""

PARTY_CODES = [
    "אמת", "אצ", "ב", "ג", "ד", "ום", "ז", "זך", "זנ", "זץ", "ט", "י", "יז", "ינ",
    "יץ", "יק", "כן", "ך", "ל", "מחל", "מרצ", "נז", "ני", "נף", "נץ", "נק", "נר",
    "עם", "פה", "ף", "צ", "ץ", "ק", "קי", "קך", "קנ", "קץ", "רז", "שס", "ת",
]

# Bloc groupings (src/bloc_map.py) — additional context alongside the
# per-party breakdown, computed here (not in build_data.py) since it's a
# cheap in-memory grouping of columns the processed GeoJSONs already
# contain, not a new offline computation.
BLOCS_FULL = full_blocs(PARTY_CODES)

st.set_page_config(page_title="Haifa Election Turnout Map", layout="wide")


def guard_processed_data_exists():
    if not STATIONS_PATH.exists() or not NEIGHBORHOODS_PATH.exists():
        st.error(
            "Processed data not found — run `.venv\\Scripts\\python.exe src\\build_data.py` "
            "first to generate data/processed/stations.geojson and neighborhoods_agg.geojson."
        )
        st.stop()


def add_bloc_columns(gdf):
    """Adds bloc raw-vote-count and _pct columns — purely additive, doesn't
    touch any existing column. Bloc % = sum(member party votes) / כשרים *
    100, the same weighted formula already used for turnout_pct and every
    per-party _pct column (never averaging per-party percentages)."""
    valid = gdf["כשרים"].replace(0, np.nan)
    for bloc_key, codes in BLOCS_FULL.items():
        gdf[bloc_key] = gdf[codes].sum(axis=1)
        gdf[f"{bloc_key}_pct"] = gdf[bloc_key] / valid * 100
    return gdf


@st.cache_data
def load_data():
    stations = gpd.read_file(STATIONS_PATH)
    neighborhoods = gpd.read_file(NEIGHBORHOODS_PATH)
    stations = add_bloc_columns(stations)
    neighborhoods = add_bloc_columns(neighborhoods)
    return stations, neighborhoods


def metric_options():
    options = [("turnout_pct", "אחוז הצבעה (Turnout %)")]
    for code in PARTY_CODES:
        name = get_party_name(code)
        # get_party_name falls back to the raw code when unmapped in
        # party_map.py — show both letter and name together when a real
        # name exists, so the ballot-letter code is still visible either way.
        label = f"{code} ({name}) %" if name != code else f"{code} %"
        options.append((f"{code}_pct", label))
    return options


def group_stations_by_exact_coord(stations):
    """Group station row-indices by their true (unrounded-for-placement,
    tolerance-rounded-for-grouping) coordinate, preserving first-seen order.
    Returns a list of {"lat", "lon", "idxs"} dicts, one per unique location.
    """
    groups = {}
    order = []
    for idx, geom in zip(stations.index, stations.geometry):
        key = (round(geom.y, 7), round(geom.x, 7))
        if key not in groups:
            groups[key] = {"lat": geom.y, "lon": geom.x, "idxs": []}
            order.append(key)
        groups[key]["idxs"].append(idx)
    return [groups[key] for key in order]


def aggregate_group_metric(group_rows, metric_col):
    """A merged marker can represent several kalpiyot with different values
    for the selected metric — color it by their combined totals
    (sum(votes)/sum(denominator)), the same weighted-aggregate logic used for
    neighborhoods, rather than an arbitrary single member's value."""
    if metric_col == "turnout_pct":
        denom = group_rows["בזב"].sum()
        numer = group_rows["מצביעים"].sum()
    else:
        code = metric_col[: -len("_pct")]
        denom = group_rows["כשרים"].sum()
        numer = group_rows[code].sum()
    if denom == 0:
        return None
    return numer / denom * 100


def rtl_html(inner: str) -> str:
    return f'<div dir="rtl" style="text-align:right; font-family: Arial, sans-serif;">{inner}</div>'


def build_bloc_breakdown_html(row):
    """Additional info: how this kalpi's valid votes split across the 4
    party blocs (src/bloc_map.py) — shown alongside, not instead of, the
    full per-party table below it."""
    rows_html = []
    for bloc_key, label in BLOC_LABELS.items():
        votes = row.get(bloc_key)
        pct = row.get(f"{bloc_key}_pct")
        if pd.notna(votes):
            pct_str = f"{pct:.3f}%" if pd.notna(pct) else "—"
            rows_html.append(f"<tr><td>{label}</td><td>{int(votes)}</td><td>{pct_str}</td></tr>")
    if not rows_html:
        return ""
    return (
        "<b>פילוח לפי גוש:</b>"
        '<table><tr><th>גוש</th><th>קולות</th><th>%</th></tr>'
        f"{''.join(rows_html)}</table><br>"
    )


def build_station_info_html(row, party_codes):
    """Inner content for one kalpi — no outer <div dir="rtl"> wrapper, so it
    can be reused standalone (build_station_popup_html) or embedded as one
    of several swappable panels (build_multi_kalpi_popup_html)."""
    party_rows = []
    for code in party_codes:
        votes = row.get(code)
        pct = row.get(f"{code}_pct")
        if pd.notna(votes) and votes > 0:
            party_rows.append((get_party_name(code), int(votes), pct))
    # Descending by vote count — every party with at least one vote at this
    # kalpi, not just the top few (but zero-vote parties are skipped, since
    # most kalpiyot have several minor parties with 0 votes).
    party_rows.sort(key=lambda x: x[1], reverse=True)

    accessible = "כן" if str(row.get("נגישה", "")).strip() else "לא"
    accessible_special = "כן" if str(row.get("נגישה מיוחדת", "")).strip() else "לא"

    turnout_pct = row.get("turnout_pct")
    turnout_str = f"{turnout_pct:.3f}%" if pd.notna(turnout_pct) else "אין מידע"

    full_address = f"{row.get('כתובת קלפי', '')}, חיפה"

    parties_html = "".join(
        f"<tr><td>{name}</td><td>{votes}</td><td>{pct:.3f}%</td></tr>"
        if pd.notna(pct) else f"<tr><td>{name}</td><td>{votes}</td><td>—</td></tr>"
        for name, votes, pct in party_rows
    )

    neighborhood = row.get("neighborhood")
    neighborhood_str = neighborhood if pd.notna(neighborhood) else "אין מידע"

    html = f"""
    <b>{row.get('מקום קלפי', '')}</b><br>
    כתובת מלאה: {full_address}<br>
    שכונה: {neighborhood_str}<br>
    <br>
    קלפי: {row.get('קלפי', '')}<br>
    זכאי הצבעה: {int(row['בזב']) if pd.notna(row.get('בזב')) else 'אין מידע'}<br>
    הצביעו: {int(row['מצביעים']) if pd.notna(row.get('מצביעים')) else 'אין מידע'} ({turnout_str})<br>
    קולות כשרים: {int(row['כשרים']) if pd.notna(row.get('כשרים')) else 'אין מידע'}<br>
    קולות פסולים: {int(row['פסולים']) if pd.notna(row.get('פסולים')) else 'אין מידע'}<br>
    נגישות: {accessible} (מיוחדת: {accessible_special})<br>
    <br>
    {build_bloc_breakdown_html(row)}
    <b>כל המפלגות (מהרוב לקולות למיעוט):</b>
    <div style="max-height:220px; overflow-y:auto;">
    <table><tr><th>מפלגה</th><th>קולות</th><th>%</th></tr>{parties_html}</table>
    </div>
    """
    return html


def build_station_popup_html(row, party_codes):
    return rtl_html(build_station_info_html(row, party_codes))


def build_multi_kalpi_popup_html(group_rows, party_codes):
    """Popup for several kalpiyot sharing one exact location: a prev/next
    pager over one panel per kalpi, all in a single popup rather than one
    marker per kalpi. Each button finds its own popup's pager container via
    `this.closest('.kalpi-pager')`, so every popup is self-contained — no
    shared script or per-popup unique IDs needed."""
    n = len(group_rows)
    panels = []
    for i, (_, row) in enumerate(group_rows.iterrows()):
        display = "block" if i == 0 else "none"
        content = build_station_info_html(row, party_codes)
        panels.append(f'<div class="kalpi-panel" style="display:{display}">{content}</div>')

    def nav_js(delta_expr):
        return (
            "var box=this.closest('.kalpi-pager');"
            "var panels=box.querySelectorAll('.kalpi-panel');"
            "var cur=parseInt(box.getAttribute('data-cur'));"
            f"var next=(({delta_expr})+{n})%{n};"
            "panels[cur].style.display='none';"
            "panels[next].style.display='block';"
            "box.setAttribute('data-cur', next);"
            f"box.querySelector('.kalpi-counter').innerText=(next+1)+' / {n}';"
        )

    nav = f"""
    <div class="kalpi-pager" data-cur="0">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
        <button onclick="{nav_js('cur-1')}" style="cursor:pointer;">&#9664; הקודם</button>
        <b><span class="kalpi-counter">1 / {n}</span> קלפיות בכתובת זו</b>
        <button onclick="{nav_js('cur+1')}" style="cursor:pointer;">הבא &#9654;</button>
    </div>
    <hr>
    {"".join(panels)}
    </div>
    """
    return rtl_html(nav)


def build_map(stations, neighborhoods, metric_col, metric_label, view_mode):
    m = folium.Map(location=HAIFA_CENTER, zoom_start=12, tiles="OpenStreetMap")

    all_values = pd.concat(
        [stations[metric_col], neighborhoods[metric_col]], ignore_index=True
    ).dropna()
    if len(all_values):
        vmin, vmax = float(all_values.min()), float(all_values.max())
        if vmin == vmax:
            vmin, vmax = vmin - 1, vmax + 1
    else:
        vmin, vmax = 0.0, 100.0

    # OSM's default tiles are pale creams/tans (land), light green (parks),
    # light blue (water), and orange/yellow (roads) — the original
    # red/pale-yellow/green scale sat right on top of that palette and was
    # hard to pick out. These are deliberately dark/saturated, and the
    # middle stop is near-black instead of yellow so no part of the range
    # blends into OSM's roads or land fill.
    colormap = cm.LinearColormap(
        colors=["#b2182b", "#1a1a1a", "#1b7837"], vmin=vmin, vmax=vmax
    )
    colormap.caption = metric_label
    # Only narrow the width — the template's rect/text geometry (e.g.
    # "height - 30", "y=21") is hardcoded assuming the default height=40, so
    # shrinking height too broke the gradient bar (zero-height rects) and
    # overlapping text. Width scales cleanly since it only affects the
    # horizontal d3 scale range.
    colormap.width = 220
    colormap._template = Template(_LEGEND_TEMPLATE_TOPLEFT)

    # Neighborhoods are added to the map before Stations: Leaflet stacks
    # layers in add-order (last added = topmost), so with "Both" selected
    # this keeps the station dots drawn on top of the neighborhood polygons
    # instead of being covered by them.
    if view_mode in ("Neighborhoods", "Both"):
        def style_function(feature):
            value = feature["properties"].get(metric_col)
            color = colormap(value) if value is not None else NO_DATA_COLOR
            return {"fillColor": color, "color": "black", "weight": 1, "fillOpacity": 0.6}

        popup_fields = ["SchName", metric_col, "n_stations"]
        popup_aliases = ["שכונה", metric_label, "מספר קלפיות"]
        # Bloc breakdown as additional info, always shown regardless of
        # which layer (parties/blocs) is currently selected — skip a bloc
        # field if it's already the selected metric_col, to avoid listing
        # the same field twice.
        for bloc_key, bloc_label in BLOC_LABELS.items():
            field = f"{bloc_key}_pct"
            if field != metric_col:
                popup_fields.append(field)
                popup_aliases.append(f"{bloc_label} %")

        # GeoJsonPopup (click-triggered), not GeoJsonTooltip (hover-
        # triggered) — a tooltip would pop up stats on every mouse-over while
        # just panning around the map, which is distracting. Stations are
        # drawn on top of this layer, so clicking a station dot still opens
        # its own popup; only clicks on empty polygon area reach this one.
        folium.GeoJson(
            neighborhoods,
            name="Neighborhoods",
            style_function=style_function,
            popup=folium.GeoJsonPopup(fields=popup_fields, aliases=popup_aliases),
        ).add_to(m)

    if view_mode in ("Stations", "Both"):
        station_layer = folium.FeatureGroup(name="Stations")
        # One marker per exact location (not per kalpi): several kalpiyot
        # sharing a building geocode to the identical point, and the popup
        # lets you page between them rather than needing separate,
        # perfectly-overlapping markers.
        for group in group_stations_by_exact_coord(stations):
            group_rows = stations.loc[group["idxs"]]
            value = aggregate_group_metric(group_rows, metric_col)
            color = colormap(value) if value is not None else NO_DATA_COLOR
            if len(group["idxs"]) == 1:
                popup_html = build_station_popup_html(group_rows.iloc[0], PARTY_CODES)
            else:
                popup_html = build_multi_kalpi_popup_html(group_rows, PARTY_CODES)
            folium.CircleMarker(
                location=[group["lat"], group["lon"]],
                radius=STATION_RADIUS_PIXELS,
                # A white outline (instead of matching the fill) keeps every
                # dot visible against the basemap regardless of fill color,
                # and against the neighborhood polygons underneath.
                color="white",
                weight=1.5,
                fill=True,
                fill_color=color,
                fill_opacity=0.95,
                popup=folium.Popup(popup_html, max_width=340),
            ).add_to(station_layer)
        station_layer.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    m.add_child(colormap)
    return m


def main():
    guard_processed_data_exists()
    stations, neighborhoods = load_data()

    st.title("מפת הצבעה בחירות 2022 - חיפה")
    st.caption("Haifa Election Turnout Map — 2022 Knesset Election (25th Knesset)")

    with st.sidebar:
        view_mode = st.radio("תצוגה (View)", ["Stations", "Neighborhoods", "Both"], index=2)
        options = metric_options()
        labels = [label for _, label in options]
        chosen_label = st.selectbox("צביעה לפי (Color by)", labels)
        metric_col = dict(zip(labels, [c for c, _ in options]))[chosen_label]

    m = build_map(stations, neighborhoods, metric_col, chosen_label, view_mode)
    st_folium(m, width=None, height=700, returned_objects=[])

    with st.expander("Data notes"):
        n_stations = len(stations)
        n_matched = stations["neighborhood"].notna().sum() if "neighborhood" in stations.columns else 0
        st.write(f"Stations on map: {n_stations}")
        st.write(f"Stations matched to a neighborhood: {n_matched}/{n_stations}")
        if FAILURES_PATH.exists():
            failures = pd.read_csv(FAILURES_PATH, encoding="utf-8-sig")
            st.warning(f"{len(failures)} station(s) excluded — address could not be geocoded:")
            st.dataframe(failures)


if __name__ == "__main__":
    main()
