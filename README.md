# Dashboard Prioritas Kab/Kota MBG - Sumut

## Isi
- `app.py` : dashboard Dash/Plotly
- `requirements.txt`

## Jalankan
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate

pip install -r requirements.txt

python app.py --shp "path/ke/shapefile_kabkota_sumut.shp" --data "path/ke/data_hasil.xlsx" --key "NAMA_KABKOT"
```

## Format Data Minimal
Kolom minimal di Excel/CSV:
- Nama kab/kota (contoh: `Kabupaten_Kota` atau `Kabupaten/Kota`)
- Skor akhir (contoh: `Skor_Akhir` atau `Skor Akhir`)
- (opsional) `Prioritas` (Tinggi/Sedang/Rendah). Kalau tidak ada, otomatis dibuat dari quantile skor.

Opsional tambahan:
- `P0`, `P1`, `P2`
