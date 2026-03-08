from flask import Blueprint, render_template, request, redirect, url_for, session
from login.db import verify_user, create_user

bp = Blueprint("login", __name__)

@bp.get("/")
def root():
    return redirect(url_for("login.login_page"))

@bp.get("/login")
def login_page():
    success = request.args.get("registered")
    return render_template("login.html", error=None, success="Akun berhasil dibuat! Silakan login." if success else None)

@bp.post("/login")
def login_post():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    user = verify_user(username, password)
    if not user:
        return render_template("login.html", error="Username atau password salah.", success=None)

    # session CONSISTENT
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]

    return redirect("/dashboard/")  # penting pakai trailing slash

@bp.get("/logout")
def logout():
    session.clear()
    return redirect("/login")

@bp.get("/register")
def register_page():
    return render_template("register.html", error=None)

@bp.post("/register")
def register_post():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    confirm  = request.form.get("confirm_password", "").strip()

    if not username:
        return render_template("register.html", error="Username tidak boleh kosong.")
    if len(username) < 3:
        return render_template("register.html", error="Username minimal 3 karakter.")
    if len(password) < 4:
        return render_template("register.html", error="Password minimal 4 karakter.")
    if password != confirm:
        return render_template("register.html", error="Password dan konfirmasi password tidak sama.")

    ok = create_user(username, password)
    if not ok:
        return render_template("register.html", error="Username sudah digunakan, coba username lain.")

    return redirect(url_for("login.login_page") + "?registered=1")

@bp.get("/login/admin")
def register_admin_page():
    return render_template("register_admin.html", error=None)

@bp.post("/login/admin")
def register_admin_post():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    confirm  = request.form.get("confirm_password", "").strip()
    secret   = request.form.get("admin_secret", "").strip()

    # Kunci rahasia khusus admin — ubah sesuai kebutuhan
    ADMIN_SECRET = "mbgsuperadmin"

    if not username:
        return render_template("register_admin.html", error="Username tidak boleh kosong.")
    if len(username) < 3:
        return render_template("register_admin.html", error="Username minimal 3 karakter.")
    if len(password) < 4:
        return render_template("register_admin.html", error="Password minimal 4 karakter.")
    if password != confirm:
        return render_template("register_admin.html", error="Password dan konfirmasi password tidak sama.")
    if secret != ADMIN_SECRET:
        return render_template("register_admin.html", error="Kunci admin salah. Akses ditolak.")

    ok = create_user(username, password, role="admin")
    if not ok:
        return render_template("register_admin.html", error="Username sudah digunakan, coba username lain.")

    return redirect(url_for("login.login_page") + "?registered=1")
