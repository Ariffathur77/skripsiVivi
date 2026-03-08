from app import make_app
from pathlib import Path

app = make_app(
    Path(r"C:\Users\ASUS\Provinsi Sumatera Utara-KAB_KOTA\Provinsi Sumatera Utara-KAB_KOTA\Provinsi Sumatera Utara-KAB_KOTA_PL.shp"),
    Path(r"C:\Users\ASUS\HASIL_WLC_MBG.xlsx"),
    "kab_kota",
    "kab_kota",
)

app.run_server(debug=True)