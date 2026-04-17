from __future__ import annotations

from pathlib import Path
from typing import Optional
import re
import json
import io
import os
import tempfile
import dash

import pandas as pd
import geopandas as gpd

from dash import Dash, html, dcc, dash_table, Input, Output, State, no_update
from dash.dash_table.Format import Format, Scheme
import dash_bootstrap_components as dbc
import plotly.express as px
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib import colors

from flask import session


# =========================
# Helpers
# =========================
def norm_name(x: str) -> str:
    if x is None:
        return ""
    s = str(x).strip().upper()
    s = re.sub(r"\b(KABUPATEN|KOTA|KAB\.|KOTA\.)\b", "", s)
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def read_tabular(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in [".xlsx", ".xls"]:
        return pd.read_excel(path)
    return pd.read_csv(path)


def pick_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    cols = {str(c).strip().lower(): c for c in df.columns}
    for c in candidates:
        key = str(c).strip().lower()
        if key in cols:
            return cols[key]
    return None


def ensure_prioritas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = df.columns.astype(str).str.strip()

    skor_col = pick_col(
        df,
        ["Skor_Akhir", "Skor Akhir", "skor_akhir", "skor_wlc", "Score", "Skor", "SkorAkhir"],
    )
    if skor_col is None:
        raise ValueError(
            "Tidak menemukan kolom skor akhir. Tambahkan salah satu: "
            "Skor_Akhir / Skor Akhir / skor_akhir / skor_wlc / Score / Skor / SkorAkhir"
        )

    df["Skor_Akhir"] = pd.to_numeric(df[skor_col], errors="coerce")

    prio_col = pick_col(df, ["Prioritas", "prioritas"])
    if prio_col is None:
        s = df["Skor_Akhir"]
        if s.notna().sum() == 0:
            df["Prioritas"] = "Tidak Ada Data"
            return df

        q1, q2 = s.quantile([1 / 3, 2 / 3]).tolist()

        def cat(v):
            if pd.isna(v):
                return "Tidak Ada Data"
            if v >= q2:
                return "Tinggi"
            if v >= q1:
                return "Sedang"
            return "Rendah"

        df["Prioritas"] = s.apply(cat)
    else:
        df["Prioritas"] = df[prio_col].astype(str).str.strip()

    return df


def add_poverty_fields(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    p0_col = pick_col(df, ["P0", "p0", "p0_persen_miskin", "p0_asli"])
    p1_col = pick_col(df, ["P1", "p1", "p1_kedalaman", "p1_asli"])
    p2_col = pick_col(df, ["P2", "p2", "p2_keparahan", "p2_asli"])

    if p0_col is not None:
        df["P0"] = pd.to_numeric(df[p0_col], errors="coerce")
    if p1_col is not None:
        df["P1"] = pd.to_numeric(df[p1_col], errors="coerce")
    if p2_col is not None:
        df["P2"] = pd.to_numeric(df[p2_col], errors="coerce")

    return df


def build_geo(shp_path: Path, key_col: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(shp_path).to_crs(4326)

    # Simplify geometry untuk mengurangi ukuran GeoJSON (lebih cepat di browser)
    gdf["geometry"] = gdf["geometry"].simplify(tolerance=0.005, preserve_topology=True)

    gdf.columns = gdf.columns.astype(str).str.strip()
    key_col = str(key_col).strip()

    colmap = {c.lower(): c for c in gdf.columns}
    if key_col.lower() not in colmap:
        raise ValueError(f"Kolom key '{key_col}' tidak ada di shapefile. Kolom tersedia: {list(gdf.columns)}")

    key_actual = colmap[key_col.lower()]
    gdf["Nama"] = gdf[key_actual].astype(str)
    gdf["__name_norm__"] = gdf[key_actual].apply(norm_name)

    gdf = gdf.reset_index(drop=True)
    gdf["__gid__"] = gdf.index.astype(int)
    return gdf


def read_current_config() -> dict:
    cfg_path = Path("uploads") / "current.json"
    if cfg_path.exists():
        return json.loads(cfg_path.read_text(encoding="utf-8"))

    return {
        "xlsx_path": "data/HASIL_WLC_MBG.xlsx",
        "shp_path": "data/Provinsi Sumatera Utara-KAB_KOTA_PL.shp",
        "key_col": "kab_kota",
        "name_col_data": "kab_kota",
        "last_updated": 0,
    }


# =========================
# Helper: buat gambar peta untuk PDF
# — menggunakan carto-positron agar tile tidak diblokir saat render headless
# =========================
def _render_map_image(dfx_f: pd.DataFrame, geojson: dict, cent: pd.DataFrame, zoom: float) -> str:
    """
    Render peta ke file PNG sementara dan kembalikan path-nya.
    Menggunakan mapbox_style='carto-positron' supaya tile tidak diblokir
    oleh kebijakan referer OpenStreetMap saat rendering headless (kaleido).
    """
    hover_data = {"__gid__": False, "__name_norm__": False}
    for col in ["Skor_Akhir", "P0", "P1", "P2"]:
        if col in dfx_f.columns:
            hover_data[col] = ":.3f"

    fig_pdf = px.choropleth_mapbox(
        dfx_f,
        geojson=geojson,
        locations="__gid__",
        color="Prioritas",
        category_orders={"Prioritas": ["Tinggi", "Sedang", "Rendah", "Tidak Ada Data"]},
        hover_name="Nama",
        hover_data=hover_data,
        # ← PERBAIKAN: ganti open-street-map → carto-positron
        # carto-positron tidak memerlukan token dan tidak memblokir headless render
        mapbox_style="carto-positron",
        zoom=zoom,
        center={"lat": 2.8, "lon": 99.2},
        opacity=0.75,
        color_discrete_map={
            "Tinggi": "#d7191c",
            "Sedang": "#fdae61",
            "Rendah": "#1a9641",
            "Tidak Ada Data": "#bdbdbd",
        },
    )
    fig_pdf.update_layout(margin=dict(l=0, r=0, t=30, b=0), legend_title_text="Prioritas")
    fig_pdf.update_traces(marker_line_width=1, marker_line_color="black")

    fig_pdf.add_trace(
        dict(
            type="scattermapbox",
            lon=cent["lon"],
            lat=cent["lat"],
            mode="text",
            text=cent["Nama"],
            textfont=dict(size=10, color="black"),
            showlegend=False,
            hoverinfo="skip",
        )
    )

    # Tulis ke file temp yang TIDAK langsung dihapus (delete=False)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    fig_pdf.write_image(tmp.name, format="png", width=800, height=500, scale=1.5)
    return tmp.name


# =========================
# Dash factory
# =========================
def create_dash_app(
    server,
    shp_path: str,
    data_path: str,
    key_col: str,
    name_col_data: Optional[str] = None,
    url_base_pathname: str = "/dashboard/",
) -> Dash:
    # initial load
    gdf = build_geo(Path(shp_path), key_col)

    df_raw = read_tabular(Path(data_path))
    df_raw.columns = df_raw.columns.astype(str).str.strip()

    if name_col_data is None:
        name_col_data = pick_col(
            df_raw,
            ["kab_kota", "Kabupaten_Kota", "Kabupaten/Kota", "KabKota", "KAB_KOTA", "Nama", "NAMA", "NAMA_KABKOT"],
        )
    else:
        tmp = pick_col(df_raw, [name_col_data])
        if tmp is None:
            raise ValueError(f"Kolom data '{name_col_data}' tidak ditemukan. Kolom tersedia: {list(df_raw.columns)}")
        name_col_data = tmp

    if name_col_data is None:
        raise ValueError("Tidak menemukan kolom nama kab/kota di data.")

    df = df_raw.copy()
    df["__name_norm__"] = df[name_col_data].apply(norm_name)
    df = ensure_prioritas(df)
    df = add_poverty_fields(df)

    mg = gdf.merge(df, on="__name_norm__", how="left")
    geojson_init = mg[["__gid__", "geometry"]].set_index("__gid__").__geo_interface__

    # Pre-compute centroids sekali saat startup
    cent_init = gdf.copy()
    cent_init["lon"] = cent_init.geometry.centroid.x
    cent_init["lat"] = cent_init.geometry.centroid.y
    cent_init = cent_init[["__gid__", "Nama", "lon", "lat"]]

    # Cache data records (tidak dihitung ulang di setiap serve_layout)
    _cached_records = mg.drop(columns="geometry").to_dict("records")
    _cached_geojson = geojson_init

    category_orders = {"Prioritas": ["Tinggi", "Sedang", "Rendah", "Tidak Ada Data"]}
    prioritas_opts = [{"label": p, "value": p} for p in ["Tinggi", "Sedang", "Rendah"]]

    app = Dash(
        __name__,
        server=server,
        url_base_pathname=url_base_pathname,
        external_stylesheets=[dbc.themes.FLATLY],
        suppress_callback_exceptions=True,
    )
    app.title = "Dashboard Prioritas MBG - Sumut"

    # layout per request (bisa baca session)
    def serve_layout():
        role = session.get("role", "user")
        username = session.get("username", "User")

        user_menu = dbc.DropdownMenu(
            label=f"{username}",
            color="primary",
            className="mt-3",
            children=[
                dbc.DropdownMenuItem(f"Role: {role.capitalize()}", disabled=True, style={"fontSize": "12px", "color": "#888"}),
                dbc.DropdownMenuItem(divider=True),
                dbc.DropdownMenuItem("Logout", id="btn-logout", n_clicks=0),
            ],
        )

        download_menu = dbc.DropdownMenu(
            label="Unduh Hasil",
            color="success",
            className="mt-3 me-2",
            children=[
                dbc.DropdownMenuItem("Unduh Semua Data", id="dl-all"),
                dbc.DropdownMenuItem("Unduh PDF", id="dl-pdf"),
                dbc.DropdownMenuItem(divider=True),
                dbc.DropdownMenuItem("Prioritas Tinggi saja", id="dl-tinggi"),
                dbc.DropdownMenuItem("Prioritas Sedang saja", id="dl-sedang"),
                dbc.DropdownMenuItem("Prioritas Rendah saja", id="dl-rendah"),
            ],
        )

        # tombol admin: element tetap ada, tapi disembunyikan kalau bukan admin (biar callback aman)
        upload_btn = html.A(
            "Upload Data",
            href="/admin/upload",
            className="btn btn-secondary mt-3 me-2",
            style={"display": "inline-block"} if role == "admin" else {"display": "none"},
        )
        refresh_btn = dbc.Button(
            "Refresh Data",
            id="btn-refresh",
            color="secondary",
            className="mt-3 me-2",
            style={"display": "inline-block"} if role == "admin" else {"display": "none"},
        )

        return dbc.Container(
            fluid=True,
            children=[
                dbc.Row(
                    [
                        dbc.Col(
                            html.Div(
                                [
                                    html.H4("Sistem Pendukung Keputusan Prioritas Kabupaten/Kota Penerima Program MBG"),
                                    html.Div("Provinsi Sumatera Utara", className="text-muted"),
                                ],
                                className="py-2",
                            ),
                            width=9,
                        ),
                        dbc.Col(
                            html.Div(
                                [upload_btn, refresh_btn, download_menu, user_menu],
                                className="d-flex justify-content-end",
                            ),
                            width=3,
                        ),
                    ],
                    className="align-items-center",
                ),
                html.Hr(),

                dbc.Row(
                    [
                        dbc.Col(dbc.Card(dbc.CardBody([html.Div("Total Kab/Kota", className="text-muted"), html.H3(id="kpi-total")])), md=3),
                        dbc.Col(dbc.Card(dbc.CardBody([html.Div("Prioritas Tinggi", className="text-muted"), html.H3(id="kpi-tinggi")])), md=3),
                        dbc.Col(dbc.Card(dbc.CardBody([html.Div("Prioritas Sedang", className="text-muted"), html.H3(id="kpi-sedang")])), md=3),
                        dbc.Col(dbc.Card(dbc.CardBody([html.Div("Prioritas Rendah", className="text-muted"), html.H3(id="kpi-rendah")])), md=3),
                    ],
                    className="g-3",
                ),

                # =========================
                # RINGKASAN HASIL
                # =========================
                html.Div(
                    [
                        html.H5("Ringkasan Hasil", className="mb-2"),
                        html.P(
                            "Berdasarkan hasil perhitungan indeks kemiskinan menggunakan indikator BPS (P0, P1, dan P2), "
                            "dari total 33 kabupaten/kota di Provinsi Sumatera Utara, diperoleh 11 daerah prioritas tinggi, "
                            "11 daerah prioritas sedang, dan 11 daerah prioritas rendah untuk penerima Program MBG.",
                            className="mb-2",
                        ),
                        html.P(
                            "Wilayah dengan prioritas tertinggi didominasi oleh daerah di Kepulauan Nias, seperti Nias Utara, "
                            "Nias Barat, dan Nias Selatan, yang memiliki skor kemiskinan relatif lebih tinggi dibanding daerah lainnya.",
                            className="text-muted mb-3",
                        ),
                    ],
                    className="bg-light p-3 rounded mt-4",
                ),

                # =========================
                # ROW PETA (md=7) + TABEL+KETERANGAN (md=5)
                # =========================
                dbc.Row(
                    [
                        # KIRI: PETA
                        dbc.Col(
                            dbc.Card(
                                [
                                    dbc.CardHeader(
                                        dbc.Row(
                                            [
                                                dbc.Col(html.B("Peta Prioritas Kabupaten/Kota Penerima MBG")),
                                                dbc.Col(
                                                    dcc.Dropdown(
                                                        id="filter-prioritas",
                                                        options=prioritas_opts,
                                                        multi=True,
                                                        placeholder="Filter prioritas (opsional)",
                                                    ),
                                                    width=5,
                                                ),
                                            ],
                                            className="g-2 align-items-center",
                                        )
                                    ),
                                    dbc.CardBody(
                                        html.Div(
                                            [
                                                dcc.Graph(
                                                    id="map",
                                                    config={"displayModeBar": False},
                                                    style={"height": "520px"},
                                                ),
                                                # Tombol Zoom In/Out
                                                html.Div(
                                                    [
                                                        html.Button(
                                                            "+",
                                                            id="btn-zoom-in",
                                                            n_clicks=0,
                                                            style={
                                                                "width": "32px",
                                                                "height": "32px",
                                                                "fontSize": "18px",
                                                                "fontWeight": "bold",
                                                                "cursor": "pointer",
                                                                "backgroundColor": "white",
                                                                "border": "1px solid #ccc",
                                                                "borderRadius": "4px",
                                                                "display": "flex",
                                                                "alignItems": "center",
                                                                "justifyContent": "center",
                                                                "marginBottom": "4px",
                                                            },
                                                        ),
                                                        html.Button(
                                                            "−",
                                                            id="btn-zoom-out",
                                                            n_clicks=0,
                                                            style={
                                                                "width": "32px",
                                                                "height": "32px",
                                                                "fontSize": "20px",
                                                                "fontWeight": "bold",
                                                                "cursor": "pointer",
                                                                "backgroundColor": "white",
                                                                "border": "1px solid #ccc",
                                                                "borderRadius": "4px",
                                                                "display": "flex",
                                                                "alignItems": "center",
                                                                "justifyContent": "center",
                                                            },
                                                        ),
                                                    ],
                                                    style={
                                                        "position": "absolute",
                                                        "bottom": "45px",
                                                        "right": "10px",
                                                        "zIndex": "1000",
                                                    },
                                                ),
                                            ],
                                            style={"position": "relative"},
                                        )
                                    ),
                                    dbc.CardFooter(html.Small("Tip: hover untuk lihat detail.")),
                                ]
                            ),
                            md=7,
                        ),

                        # KANAN: TABEL + KETERANGAN
                        dbc.Col(
                            html.Div(
                                [
                                    # --- CARD TABEL
                                    dbc.Card(
                                        [
                                            dbc.CardHeader(html.B("Tabel Hasil Perhitungan Indeks Kemiskinan Prioritas Kab/Kota")),
                                            dbc.CardBody(
                                                dash_table.DataTable(
                                                    id="table",
                                                    page_size=10,
                                                    sort_action="native",
                                                    filter_action="native",
                                                    filter_options={"case": "insensitive"},
                                                    style_table={"overflowX": "auto"},
                                                    style_cell={"fontFamily": "Arial", "fontSize": "13px", "padding": "6px"},
                                                    style_header={"fontWeight": "bold"},
                                                )
                                            ),
                                        ]
                                    ),

                                    # --- CARD KETERANGAN
                                    dbc.Card(
                                        [
                                            dbc.CardHeader(
                                                html.B("Keterangan Indikator Kemiskinan (BPS)"),
                                                style={"padding": "8px 12px"},
                                            ),
                                            dbc.CardBody(
                                                [
                                                    html.Ul(
                                                        [
                                                            html.Li(html.B("P0: Persentase Penduduk Miskin (%)")),
                                                            html.Li(html.B("P1: Kedalaman Kemiskinan")),
                                                            html.Li(html.B("P2: Keparahan Kemiskinan")),
                                                        ],
                                                        style={"marginBottom": "6px", "paddingLeft": "18px"},
                                                    ),
                                                    html.Small(
                                                        "Sumber: Badan Pusat Statistik (BPS), 2025",
                                                        className="text-muted",
                                                    ),
                                                ],
                                                style={"fontSize": "14px", "padding": "8px 12px"},
                                            ),
                                        ],
                                        className="mt-3",
                                        style={"backgroundColor": "#fafafa"},
                                    ),
                                ]
                            ),
                            md=5,
                        ),
                    ],
                    className="g-3 mt-1",
                ),

                dcc.Store(id="store-data", data=_cached_records),
                dcc.Store(id="store-geojson", data=_cached_geojson),
                dcc.Store(id="store-zoom", data=5.8),
                dcc.Store(id="store-timestamp", data=read_current_config().get("last_updated", 0)),
                # ← PERBAIKAN: store-map-image sekarang hanya menyimpan data filtered rows & zoom
                #   untuk di-render ulang saat PDF diminta (bukan simpan path file yang bisa terhapus)
                dcc.Store(id="store-pdf-state", data=None),
                dcc.Download(id="download-data"),
                dcc.Download(id="download-pdf"),
                dcc.Interval(id="interval-refresh", interval=5000, n_intervals=0),
                dcc.Location(id="url-location", refresh=True),
            ],
            style={"backgroundColor": "#f6f8fb"},
            className="p-3",
        )

    app.layout = serve_layout

    # =========================
    # Logout handler
    # =========================
    @app.callback(
        Output("url-location", "href"),
        Output("url-location", "refresh"),
        Input("btn-logout", "n_clicks"),
        prevent_initial_call=True,
    )
    def handle_logout(n_clicks):
        if n_clicks and n_clicks > 0:
            session.clear()
            return "/login", True
        return no_update, no_update

    # =========================
    # Admin refresh
    # =========================
    @app.callback(
        Output("store-data", "data"),
        Output("store-geojson", "data"),
        Output("store-timestamp", "data"),
        Input("btn-refresh", "n_clicks"),
        Input("interval-refresh", "n_intervals"),
        State("store-timestamp", "data"),
        prevent_initial_call=True,
    )
    def load_data(n_clicks, n_intervals, stored_timestamp):
        ctx = dash.callback_context
        if not ctx.triggered:
            return no_update, no_update, no_update

        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

        if trigger_id == "interval-refresh":
            cfg = read_current_config()
            current_timestamp = cfg.get("last_updated", 0)
            if current_timestamp == stored_timestamp:
                return no_update, no_update, no_update
            stored_timestamp = current_timestamp
        elif trigger_id != "btn-refresh":
            return no_update, no_update, no_update

        if session.get("role") != "admin":
            return no_update, no_update, no_update

        cfg = read_current_config()

        gdf2 = build_geo(Path(cfg["shp_path"]), cfg["key_col"])

        df_raw2 = read_tabular(Path(cfg["xlsx_path"]))
        df_raw2.columns = df_raw2.columns.astype(str).str.strip()

        name_col = cfg.get("name_col_data")
        tmp = pick_col(df_raw2, [name_col]) if name_col else None
        if tmp is None:
            tmp = pick_col(df_raw2, ["kab_kota", "Nama", "NAMA", "NAMA_KABKOT"])
        if tmp is None:
            raise ValueError("Kolom nama kab/kota di Excel tidak ditemukan.")

        df2 = df_raw2.copy()
        df2["__name_norm__"] = df2[tmp].apply(norm_name)
        df2 = ensure_prioritas(df2)
        df2 = add_poverty_fields(df2)

        mg2 = gdf2.merge(df2, on="__name_norm__", how="left")
        geojson2 = mg2[["__gid__", "geometry"]].set_index("__gid__").__geo_interface__
        rows2 = mg2.drop(columns="geometry").to_dict("records")

        nonlocal cent_init, _cached_records, _cached_geojson
        cent_init = gdf2.copy()
        cent_init["lon"] = cent_init.geometry.centroid.x
        cent_init["lat"] = cent_init.geometry.centroid.y
        cent_init = cent_init[["__gid__", "Nama", "lon", "lat"]]
        _cached_records = rows2
        _cached_geojson = geojson2

        cfg = read_current_config()
        new_timestamp = cfg.get("last_updated", 0)

        return rows2, geojson2, new_timestamp

    # =========================
    # Update map + table
    # ← PERBAIKAN: hapus Output("store-map-image") dan ganti dengan store-pdf-state
    #   yang menyimpan (filtered_rows, zoom) — bukan path file temp
    # =========================
    @app.callback(
        Output("kpi-total", "children"),
        Output("kpi-tinggi", "children"),
        Output("kpi-sedang", "children"),
        Output("kpi-rendah", "children"),
        Output("map", "figure"),
        Output("table", "data"),
        Output("table", "columns"),
        Output("store-zoom", "data"),
        Output("store-pdf-state", "data"),   # ← simpan state filter+zoom untuk PDF
        Input("filter-prioritas", "value"),
        Input("store-data", "data"),
        Input("store-geojson", "data"),
        Input("btn-zoom-in", "n_clicks"),
        Input("btn-zoom-out", "n_clicks"),
        State("store-zoom", "data"),
    )
    def update(prioritas_selected, rows, geojson, n_zoom_in, n_zoom_out, current_zoom):
        ctx = dash.callback_context
        if not ctx.triggered:
            zoom = 5.8
        else:
            trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
            if trigger_id == "btn-zoom-in":
                zoom = current_zoom * 1.2 if current_zoom else 5.8
            elif trigger_id == "btn-zoom-out":
                zoom = current_zoom / 1.2 if current_zoom else 5.8
            else:
                zoom = current_zoom if current_zoom else 5.8

        zoom = max(1, min(zoom, 20))

        if not rows or not geojson:
            empty_fig = px.scatter()
            return "0", "0", "0", "0", empty_fig, [], [], zoom, None

        dfx = pd.DataFrame(rows)
        dfx_f = dfx[dfx["Prioritas"].isin(prioritas_selected)].copy() if prioritas_selected else dfx.copy()

        cent = cent_init[cent_init["__gid__"].isin(dfx_f["__gid__"])]

        total = len(dfx_f)
        tinggi = int((dfx_f["Prioritas"] == "Tinggi").sum())
        sedang = int((dfx_f["Prioritas"] == "Sedang").sum())
        rendah = int((dfx_f["Prioritas"] == "Rendah").sum())

        hover_data = {"__gid__": False, "__name_norm__": False}
        for col in ["Skor_Akhir", "P0", "P1", "P2"]:
            if col in dfx_f.columns:
                hover_data[col] = ":.3f"

        # Peta di browser tetap pakai open-street-map (tampilan lebih bagus)
        fig = px.choropleth_mapbox(
            dfx_f,
            geojson=geojson,
            locations="__gid__",
            color="Prioritas",
            category_orders={"Prioritas": ["Tinggi", "Sedang", "Rendah", "Tidak Ada Data"]},
            hover_name="Nama",
            hover_data=hover_data,
            mapbox_style="open-street-map",
            zoom=zoom,
            center={"lat": 2.8, "lon": 99.2},
            opacity=0.75,
            color_discrete_map={
                "Tinggi": "#d7191c",
                "Sedang": "#fdae61",
                "Rendah": "#1a9641",
                "Tidak Ada Data": "#bdbdbd",
            },
        )
        fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), legend_title_text="Prioritas")
        fig.update_traces(marker_line_width=1, marker_line_color="black")
        fig.add_trace(
            dict(
                type="scattermapbox",
                lon=cent["lon"],
                lat=cent["lat"],
                mode="text",
                text=cent["Nama"],
                textfont=dict(size=11, color="black"),
                showlegend=False,
                hoverinfo="skip",
            )
        )

        base_cols = ["Nama", "P0", "P1", "P2", "Skor_Akhir", "Prioritas"]
        table_cols = [c for c in base_cols if c in dfx_f.columns]
        table_df = dfx_f[table_cols].copy()

        if "Skor_Akhir" in table_df.columns:
            table_df = table_df.sort_values(by="Skor_Akhir", ascending=False, na_position="last")

        columns = []
        for c in table_df.columns:
            coldef = {"name": c.replace("_", " "), "id": c}
            if c in ["P0", "P1", "P2", "Skor_Akhir"]:
                coldef["type"] = "numeric"
                coldef["format"] = Format(precision=3, scheme=Scheme.fixed)
            elif c in ["Nama", "Prioritas"]:
                coldef["type"] = "text"
            columns.append(coldef)

        data = table_df.round(3).to_dict("records")

        # ← PERBAIKAN: simpan filtered rows + zoom ke store, bukan path file
        pdf_state = {
            "filtered_rows": dfx_f.to_dict("records"),
            "zoom": zoom,
        }

        return str(total), str(tinggi), str(sedang), str(rendah), fig, data, columns, zoom, pdf_state

    # ===============
    # Download Excel
    # ===============
    @app.callback(
        Output("download-data", "data"),
        Input("dl-all", "n_clicks"),
        Input("dl-tinggi", "n_clicks"),
        Input("dl-sedang", "n_clicks"),
        Input("dl-rendah", "n_clicks"),
        State("store-data", "data"),
        prevent_initial_call=True,
    )
    def download_data(n_all, n_tinggi, n_sedang, n_rendah, rows):
        if not rows:
            return no_update

        prop_id = dash.callback_context.triggered[0]["prop_id"]
        triggered_id = prop_id.split(".")[0]

        df = pd.DataFrame(rows)

        if "Ranking" not in df.columns and "Skor_Akhir" in df.columns:
            df["Ranking"] = df["Skor_Akhir"].rank(method="min", ascending=False).astype("Int64")

        if "kode_kk" not in df.columns and "kode_kk_x" in df.columns:
            df = df.rename(columns={"kode_kk_x": "kode_kk"})
        if "kode_kk" not in df.columns and "kode_kk_y" in df.columns:
            df = df.rename(columns={"kode_kk_y": "kode_kk"})

        label = "Semua"
        if triggered_id == "dl-tinggi":
            df = df[df["Prioritas"] == "Tinggi"].copy()
            label = "Tinggi"
        elif triggered_id == "dl-sedang":
            df = df[df["Prioritas"] == "Sedang"].copy()
            label = "Sedang"
        elif triggered_id == "dl-rendah":
            df = df[df["Prioritas"] == "Rendah"].copy()
            label = "Rendah"

        if "Ranking" in df.columns:
            df = df.sort_values("Ranking")

        wanted = [
            "kode_kk", "Nama", "P0", "P1", "P2",
            "p0_persen_miskin_norm", "p1_kedalaman_norm", "p2_keparahan_norm",
            "Skor_Akhir", "Ranking", "Prioritas",
        ]
        cols_exist = [c for c in wanted if c in df.columns]
        df_out = df[cols_exist].copy()

        rename_map = {
            "Nama": "nama", "P0": "p0", "P1": "p1", "P2": "p2",
            "Skor_Akhir": "skor akhir", "Ranking": "peringkat", "Prioritas": "prioritas",
        }
        df_out = df_out.rename(columns=rename_map)

        filename = f"Hasil_Prioritas_{label}.xlsx"
        return dcc.send_data_frame(df_out.to_excel, filename, index=False, sheet_name="Hasil")

    # ===============
    # Download PDF
    # ← PERBAIKAN UTAMA:
    #   1. Terima store-pdf-state (berisi filtered rows + zoom), bukan path file
    #   2. Render ulang gambar peta setiap kali PDF diminta → tidak bergantung file temp
    #   3. Gunakan carto-positron di _render_map_image() → tidak ada "Access blocked"
    # ===============
    @app.callback(
        Output("download-pdf", "data"),
        Input("dl-pdf", "n_clicks"),
        State("store-pdf-state", "data"),
        State("store-geojson", "data"),
        State("kpi-total", "children"),
        State("kpi-tinggi", "children"),
        State("kpi-sedang", "children"),
        State("kpi-rendah", "children"),
        prevent_initial_call=True,
    )
    def download_pdf(n_clicks, pdf_state, geojson, total, tinggi, sedang, rendah):
        if not pdf_state or not geojson:
            return no_update

        filtered_rows = pdf_state.get("filtered_rows", [])
        zoom = pdf_state.get("zoom", 5.8)

        if not filtered_rows:
            return no_update

        dfx_f = pd.DataFrame(filtered_rows)
        cent = cent_init[cent_init["__gid__"].isin(dfx_f["__gid__"])]

        # ← Render ulang peta dengan carto-positron (tidak ada Access Blocked)
        map_image_path = _render_map_image(dfx_f, geojson, cent, zoom)

        # Siapkan tabel PDF
        wanted = ["Nama", "P0", "P1", "P2", "Skor_Akhir", "Prioritas"]
        cols_exist = [c for c in wanted if c in dfx_f.columns]
        df_pdf = dfx_f[cols_exist].copy()
        if "Skor_Akhir" in df_pdf.columns:
            df_pdf = df_pdf.sort_values("Skor_Akhir", ascending=False, na_position="last")

        # Build PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph("Hasil Prioritas Kemiskinan Kabupaten/Kota Sumatera Utara", styles["Title"]))
        story.append(Spacer(1, 12))

        summary_text = (
            f"Berdasarkan hasil perhitungan indeks kemiskinan menggunakan indikator BPS (P0, P1, dan P2), "
            f"dari total {total} kabupaten/kota di Provinsi Sumatera Utara, diperoleh {tinggi} daerah prioritas tinggi, "
            f"{sedang} daerah prioritas sedang, dan {rendah} daerah prioritas rendah untuk penerima Program MBG."
        )
        story.append(Paragraph(summary_text, styles["Normal"]))
        story.append(Spacer(1, 8))

        kpi_text = f"Total Kabupaten/Kota: {total} | Tinggi: {tinggi} | Sedang: {sedang} | Rendah: {rendah}"
        story.append(Paragraph(kpi_text, styles["Normal"]))
        story.append(Spacer(1, 12))

        # Gambar peta
        if os.path.exists(map_image_path):
            story.append(Image(map_image_path, width=460, height=290))
            story.append(Spacer(1, 12))

        # Tabel data
        if not df_pdf.empty:
            table_data = [df_pdf.columns.tolist()] + df_pdf.round(3).values.tolist()
            tbl = Table(table_data, repeatRows=1)
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 11),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ]))
            story.append(tbl)

        doc.build(story)
        buffer.seek(0)

        # Hapus file temp setelah PDF selesai dibuild
        try:
            os.unlink(map_image_path)
        except Exception:
            pass

        return dcc.send_bytes(buffer.getvalue(), "Hasil_Prioritas.pdf")

    return app