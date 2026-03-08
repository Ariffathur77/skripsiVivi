from flask import Flask, session, redirect, request
from login import register_login
from login.db import init_db
from dashboard.dash_app import create_dash_app
from admin import register_admin

def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "GANTI_SECRET_KEY_YANG_RAHASIA"

    init_db()
    register_login(app)
    register_admin(app)

    print(app.url_map)

    @app.before_request
    def protect_dashboard():
        if request.path.startswith("/dashboard") and ("user_id" not in session):
            return redirect("/login")

    return app

if __name__ == "__main__":
    server = create_app()

    server.config["SESSION_ROLE"] = lambda: session.get("role")

    SHP_PATH = "data/Provinsi Sumatera Utara-KAB_KOTA_PL.shp"
    XLSX_PATH = "data/HASIL_WLC_MBG.xlsx"
    KEY_SHP = "kab_kota"
    NAMECOL_DATA = "kab_kota"

    create_dash_app(
        server=server,
        shp_path=SHP_PATH,
        data_path=XLSX_PATH,
        key_col=KEY_SHP,
        name_col_data=NAMECOL_DATA,
        url_base_pathname="/dashboard/",
    )

server.run(debug=True, use_reloader=False, host="127.0.0.1", port=8050)