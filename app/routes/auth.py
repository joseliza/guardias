"""
Blueprint de autenticación. Gestiona el inicio y cierre de sesión con
email/contraseña y con Google Workspace OAuth (solo dominio del instituto).
El rol `display` redirige directamente a la pantalla de sala de profesores;
el resto de roles van al panel principal.
"""
import urllib.parse
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, session
from flask_login import login_user, logout_user, login_required, current_user
from app.extensions import db, oauth
from app.models.user import User

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email, active=True).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            if user.role == "display":
                return redirect(url_for("dashboard.index"))
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard.index"))
        flash("Email o contraseña incorrectos.", "danger")
    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    via_google = session.get("via_google", False)
    session.pop("task_prompt_ids", None)
    logout_user()
    if via_google:
        return redirect("https://accounts.google.com/Logout")
    return redirect(url_for("auth.login"))


@auth_bp.route("/auth/google")
def google_login():
    redirect_uri = url_for("auth.google_callback", _external=True, _scheme="https")
    allowed_domain = current_app.config["GOOGLE_ALLOWED_DOMAIN"]
    return oauth.google.authorize_redirect(redirect_uri, hd=allowed_domain, prompt="select_account login", max_age=0)


@auth_bp.route("/auth/google/callback")
def google_callback():
    token = oauth.google.authorize_access_token()
    userinfo = token.get("userinfo") or {}

    email = (userinfo.get("email") or "").lower()
    hd = userinfo.get("hd", "")
    allowed_domain = current_app.config["GOOGLE_ALLOWED_DOMAIN"]

    if hd != allowed_domain:
        flash("Solo se permiten cuentas del instituto (@" + allowed_domain + ").", "danger")
        return redirect(url_for("auth.login"))

    user = User.query.filter_by(email=email, active=True).first()
    if not user:
        flash("No tienes cuenta en esta aplicación. Contacta con el equipo directivo.", "danger")
        return redirect(url_for("auth.login"))

    session["via_google"] = True
    login_user(user, remember=True)
    if user.role == "display":
        return redirect(url_for("display.index"))
    next_page = request.args.get("next")
    return redirect(next_page or url_for("dashboard.index"))
