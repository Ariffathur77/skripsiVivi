from pathlib import Path
from flask import Blueprint, render_template, request, redirect, session, abort, flash
from werkzeug.utils import secure_filename
import json
import time

bp = Blueprint("admin", __name__, url_prefix="/admin")

UPLOAD_DIR = Path("uploads")
EXCEL_DIR = UPLOAD_DIR / "excel"
SHP_DIR = UPLOAD_DIR / "shp"
CURRENT_CFG = UPLOAD_DIR / "current.json"

ALLOWED_EXCEL = {".xlsx", ".xls", ".csv"}
ALLOWED_SHP = {".shp", ".shx", ".dbf", ".prj"}


def require_admin():
    if session.get("role") != "admin":
        abort(403)


def ensure_dirs():
    EXCEL_DIR.mkdir(parents=True, exist_ok=True)
    SHP_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@bp.get("/upload")
def upload_page():
    require_admin()
    ensure_dirs()
    return render_template("admin_upload.html", error=None)


@bp.post("/upload")
def upload_post():
    require_admin()
    ensure_dirs()

    key_col = (request.form.get("key_col") or "kab_kota").strip()
    name_col_data = (request.form.get("name_col_data") or "kab_kota").strip()

    excel_file = request.files.get("excel")
    shp_files = request.files.getlist("shp_files")

    if not excel_file or excel_file.filename == "":
        return render_template("admin_upload.html", error="File Excel belum dipilih.")

    excel_ext = Path(excel_file.filename).suffix.lower()
    if excel_ext not in ALLOWED_EXCEL:
        return render_template("admin_upload.html", error="Excel harus .xlsx/.xls/.csv")

    # simpan excel
    excel_name = secure_filename(excel_file.filename)
    excel_save_path = EXCEL_DIR / excel_name
    excel_file.save(excel_save_path)

    # simpan shapefile paket
    if not shp_files or all(f.filename == "" for f in shp_files):
        return render_template("admin_upload.html", error="Paket shapefile belum dipilih (shp+shx+dbf+prj).")

    # buat folder dataset untuk shapefile
    dataset_dir = SHP_DIR / excel_save_path.stem
    dataset_dir.mkdir(parents=True, exist_ok=True)

    saved = {}
    for f in shp_files:
        if not f or f.filename == "":
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_SHP:
            continue
        fname = secure_filename(f.filename)
        out = dataset_dir / fname
        f.save(out)
        saved[ext] = out

    # cek file wajib
    for need in [".shp", ".shx", ".dbf", ".prj"]:
        if need not in saved:
            return render_template("admin_upload.html", error=f"Shapefile kurang file {need}. Harus upload .shp .shx .dbf .prj")

    shp_path = str(saved[".shp"]).replace("\\", "/")
    xlsx_path = str(excel_save_path).replace("\\", "/")

    cfg = {
        "xlsx_path": xlsx_path,
        "shp_path": shp_path,
        "key_col": key_col,
        "name_col_data": name_col_data,
        "last_updated": int(time.time()),  # Timestamp untuk auto-refresh
    }
    CURRENT_CFG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    # selesai -> balik ke dashboard
    return redirect("/dashboard/")