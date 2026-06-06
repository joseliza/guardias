from flask import Blueprint, session, redirect, url_for, flash
from flask_login import login_required, current_user, login_user
from app.models.user import User

impersonate_bp = Blueprint("impersonate", __name__, url_prefix="/admin/simular")


@impersonate_bp.route("/<int:uid>", methods=["POST"])
@login_required
def start(uid):
    if not current_user.is_management or session.get("impersonate_real_id") or session.get("pantalla_real_id"):
        flash("Sin permiso.", "danger")
        return redirect(url_for("dashboard.index"))
    target = User.query.get_or_404(uid)
    if target.is_management:
        flash("Solo se puede simular usuarios no directivos.", "warning")
        return redirect(url_for("admin.teachers"))
    session["impersonate_real_id"] = current_user.id
    login_user(target)
    flash(f"Estás viendo la app como {target.full_name} (solo lectura).", "info")
    return redirect(url_for("dashboard.index"))


@impersonate_bp.route("/salir", methods=["POST"])
@login_required
def stop():
    real_id = session.pop("impersonate_real_id", None)
    if real_id:
        real_user = User.query.get(real_id)
        if real_user:
            login_user(real_user)
            flash(f"Has vuelto a tu sesión como {real_user.full_name}.", "success")
    return redirect(url_for("dashboard.index"))


@impersonate_bp.route("/pantalla", methods=["POST"])
@login_required
def go_display():
    """Entra en modo pantalla como el usuario display (escritura completa, navbar display)."""
    if not current_user.is_management or session.get("impersonate_real_id") or session.get("pantalla_real_id"):
        flash("Sin permiso.", "danger")
        return redirect(url_for("dashboard.index"))
    display_user = User.query.filter_by(active=True, role="display").first()
    if not display_user:
        flash("No hay ningún usuario con rol display configurado.", "warning")
        return redirect(url_for("dashboard.index"))
    session["pantalla_real_id"] = current_user.id
    login_user(display_user)
    return redirect(url_for("dashboard.index"))


@impersonate_bp.route("/pantalla/salir", methods=["POST"])
@login_required
def leave_display():
    real_id = session.pop("pantalla_real_id", None)
    if real_id:
        real_user = User.query.get(real_id)
        if real_user:
            login_user(real_user)
            flash(f"Has vuelto a tu sesión como {real_user.full_name}.", "success")
    return redirect(url_for("dashboard.index"))
