from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Optional

import pandas as pd
import geopandas as gpd

from dash import Dash, html, dcc, dash_table, Input, Output
import dash_bootstrap_components as dbc
import plotly.express as px


# ----------------------------
# Helpers
# ----------------------------
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
        df["Prioritas"] = df["Prioritas"].replace(
            {
                "High": "Tinggi",
                "Medium": "Sedang",
                "Low": "Rendah",
                "tinggi": "Tinggi",
                "sedang": "Sedang",
                "rendah": "Rendah",
            }
        )

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


# ----------------------------
# App
# ----------------------------
def make_app(shp_path: Path, data_path: Path, key_col: str, name_col_data: Optional[str]) -> Dash:
    gdf = build_geo(shp_path, key_col)

    df_raw = read_tabular(data_path)
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
        raise ValueError("Tidak menemukan kolom nama kab/kota pada file data. Tambahkan kolom mis. 'kab_kota' atau pakai --namecol.")

    df = df_raw.copy()
    df["__name_norm__"] = df[name_col_data].apply(norm_name)
    df = ensure_prioritas(df)
    df = add_poverty_fields(df)

    mg = gdf.merge(df, on="__name_norm__", how="left")

    # geojson untuk peta (pakai mg agar __gid__ sesuai)
    geojson_init = mg[["__gid__", "geometry"]].set_index("__gid__").__geo_interface__

    category_orders = {"Prioritas": ["Tinggi", "Sedang", "Rendah", "Tidak Ada Data"]}
    prioritas_opts = [{"label": p, "value": p} for p in ["Tinggi", "Sedang", "Rendah"]]

    app = Dash(__name__, external_stylesheets=[dbc.themes.FLATLY])
    app.title = "Dashboard Prioritas MBG - Sumut"

    app.layout = dbc.Container(
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
                        width=10,
                    ),
                    dbc.Col(dbc.Badge("Demo", color="primary", className="mt-3"), width=2, className="text-end"),
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
            dbc.Row(
                [
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
                                dbc.CardBody(dcc.Graph(id="map", config={"displayModeBar": False}, style={"height": "520px"})),
                                dbc.CardFooter(html.Small("Tip: hover untuk lihat detail.")),
                            ]
                        ),
                        md=7,
                    ),
                    dbc.Col(
                        dbc.Card(
                            [
                                dbc.CardHeader(html.B("Tabel Hasil Perhitungan Indeks Kemiskinan Prioritas Kab/Kota")),
                                dbc.CardBody(
                                    dash_table.DataTable(
                                        id="table",
                                        page_size=10,
                                        sort_action="native",
                                        filter_action="native",
                                        style_table={"overflowX": "auto"},
                                        style_cell={"fontFamily": "Arial", "fontSize": "13px", "padding": "6px"},
                                        style_header={"fontWeight": "bold"},
                                    )
                                ),
                            ]
                        ),
                        md=5,
                    ),
                ],
                className="g-3 mt-1",
            ),
            # store langsung diisi
            dcc.Store(id="store-data", data=mg.drop(columns="geometry").to_dict("records")),
            dcc.Store(id="store-geojson", data=geojson_init),
        ],
        style={"backgroundColor": "#f6f8fb"},
        className="p-3",
    )

    @app.callback(
        Output("kpi-total", "children"),
        Output("kpi-tinggi", "children"),
        Output("kpi-sedang", "children"),
        Output("kpi-rendah", "children"),
        Output("map", "figure"),
        Output("table", "data"),
        Output("table", "columns"),
        Input("filter-prioritas", "value"),
        Input("store-data", "data"),
        Input("store-geojson", "data"),
    )
    def update(prioritas_selected, rows, geojson):
        if not rows or not geojson:
            empty_fig = px.scatter()
            return "0", "0", "0", "0", empty_fig, [], []

        dfx = pd.DataFrame(rows)
        dfx_f = dfx[dfx["Prioritas"].isin(prioritas_selected)].copy() if prioritas_selected else dfx.copy()

        total = len(dfx_f)
        tinggi = int((dfx_f["Prioritas"] == "Tinggi").sum())
        sedang = int((dfx_f["Prioritas"] == "Sedang").sum())
        rendah = int((dfx_f["Prioritas"] == "Rendah").sum())

        hover_data = {"__gid__": False, "__name_norm__": False}
        for col in ["Skor_Akhir", "P0", "P1", "P2"]:
            if col in dfx_f.columns:
                hover_data[col] = ":.6f"

        fig = px.choropleth(
            dfx_f,
            geojson=geojson,
            locations="__gid__",
            color="Prioritas",
            category_orders={"Prioritas": ["Tinggi", "Sedang", "Rendah", "Tidak Ada Data"]},
            hover_name="Nama",
            hover_data=hover_data,
        )
        fig.update_geos(fitbounds="locations", visible=False)
        fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), legend_title_text="Prioritas")

        base_cols = ["Nama", "P0", "P1", "P2", "Skor_Akhir", "Prioritas"]
        table_cols = [c for c in base_cols if c in dfx_f.columns]
        table_df = dfx_f[table_cols].copy()

        if "Skor_Akhir" in table_df.columns:
            table_df = table_df.sort_values(by="Skor_Akhir", ascending=False, na_position="last")

        columns = [{"name": c.replace("_", " "), "id": c} for c in table_df.columns]
        data = table_df.to_dict("records")

        return str(total), str(tinggi), str(sedang), str(rendah), fig, data, columns

    return app


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--shp", required=True)
    p.add_argument("--data", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--namecol", default=None)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", default=8050, type=int)
    args = p.parse_args()

    app = make_app(Path(args.shp), Path(args.data), args.key, args.namecol)
    app.run_server(debug=True, host=args.host, port=args.port)


if __name__ == "__main__":
    main()