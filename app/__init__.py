"""
Factoría de la aplicación Flask. Inicializa extensiones, registra blueprints
y define el filtro Jinja2 `fecha_es` para formatear fechas en castellano.
"""
from urllib.parse import urlparse

from flask import Flask, request, url_for
from flask_login import current_user
from app.config import Config
from app.extensions import db, login_manager, migrate, mail, socketio, oauth, scheduler


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    mail.init_app(app)
    socketio.init_app(app, async_mode="eventlet", cors_allowed_origins="*")

    from app.utils.realtime import register_display_notifications
    register_display_notifications()
    oauth.init_app(app)
    oauth.register(
        name="google",
        client_id=app.config["GOOGLE_CLIENT_ID"],
        client_secret=app.config["GOOGLE_CLIENT_SECRET"],
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Por favor, inicia sesión para acceder."
    login_manager.login_message_category = "warning"

    _DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    _MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
              "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

    @app.template_filter("mins_to_hhmm")
    def mins_to_hhmm(m):
        return f"{m // 60:02d}:{m % 60:02d}"

    @app.template_global("back_url")
    def back_url(fallback_endpoint, anchor=None, **kwargs):
        """URL para los botones 'Atrás': vuelve a la página que llamó (referrer)
        si pertenece a esta app y no es la página actual; en caso contrario usa
        el endpoint de respaldo. El ancla (#...) se aplica en ambos casos."""
        ref = request.referrer
        if ref:
            parsed = urlparse(ref)
            same_host = not parsed.netloc or parsed.netloc == request.host
            if same_host and parsed.path != request.path:
                return ref + (f"#{anchor}" if anchor else "")
        if anchor:
            kwargs["_anchor"] = anchor
        return url_for(fallback_endpoint, **kwargs)

    @app.template_filter("fecha_es")
    def fecha_es(d, fmt="%A, %d de %B de %Y"):
        return (fmt
                .replace("%A", _DIAS[d.weekday()])
                .replace("%B", _MESES[d.month - 1])
                .replace("%m", f"{d.month:02d}")
                .replace("%d", f"{d.day:02d}")
                .replace("%Y", str(d.year)))

    from app.models.user import User
    from app.models.room import Room  # noqa
    from app.models.chat import ChatMessage, ChatClear  # noqa
    from app.models.presence import UserPresence  # noqa

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.get(int(user_id))
        # Invalida sesiones de filas desactivadas o archivadas (email marcador
        # _..._@pendiente.local): una cookie antigua no debe seguir operando
        # como la fila de un curso anterior.
        if user is None or not user.active:
            return None
        email = user.email or ""
        if email.startswith("_") and email.endswith("@pendiente.local"):
            return None
        return user

    @app.after_request
    def no_cache(response):
        if current_user.is_authenticated:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.context_processor
    def inject_justification_count():
        try:
            if current_user.is_authenticated and current_user.is_management:
                from app.models.absence import Absence
                count = Absence.query.filter_by(justified=False).count()
                return {"pending_justification": count}
        except Exception:
            pass
        return {"pending_justification": 0}

    @app.context_processor
    def inject_points_cfg():
        try:
            if current_user.is_authenticated:
                from app.utils import points_system_enabled
                return {"points_enabled": points_system_enabled()}
        except Exception:
            pass
        return {"points_enabled": True}

    @app.context_processor
    def inject_school_year():
        try:
            if current_user.is_authenticated:
                from app.utils.school_year import get_current_school_year
                return {"current_school_year": get_current_school_year()}
        except Exception:
            pass
        return {"current_school_year": None}

    @app.context_processor
    def inject_presence_cfg():
        try:
            if current_user.is_authenticated:
                from app.routes.admin import _read_mail_config, GENERAL_DEFAULTS
                gcfg = {**GENERAL_DEFAULTS, **_read_mail_config().get("GENERAL", {})}
                visible_to = gcfg.get("presence_visible_to", "none")
                can_see = (
                    visible_to == "all" or
                    (visible_to == "management" and current_user.is_management)
                )
                return {"presence_cfg": {
                    "can_see": can_see,
                    "detail": gcfg.get("presence_detail", "count"),
                }}
        except Exception:
            pass
        return {"presence_cfg": {"can_see": False, "detail": "count"}}

    from flask import session, request as _request, redirect as _redirect, url_for as _url_for, flash as _flash

    @app.before_request
    def block_writes_during_impersonation():
        if not session.get("impersonate_real_id"):
            return
        if _request.method in ("GET", "HEAD", "OPTIONS"):
            return
        if _request.endpoint == "impersonate.stop":
            return
        _flash("Modo solo lectura: no se pueden realizar cambios mientras se simula otro usuario.", "warning")
        return _redirect(_request.referrer or _url_for("dashboard.index"))

    @app.context_processor
    def inject_impersonation():
        try:
            real_id = session.get("impersonate_real_id")
            pantalla_real_id = session.get("pantalla_real_id")
            if real_id and current_user.is_authenticated:
                from app.models.user import User as _User
                real_user = _User.query.get(real_id)
                return {
                    "impersonating": True,
                    "impersonating_as": current_user,
                    "real_user": real_user,
                    "impersonate_teachers": [],
                    "pantalla_mode": False,
                }
            if pantalla_real_id and current_user.is_authenticated:
                from app.models.user import User as _User
                real_user = _User.query.get(pantalla_real_id)
                return {
                    "impersonating": False,
                    "impersonating_as": None,
                    "real_user": real_user,
                    "impersonate_teachers": [],
                    "pantalla_mode": True,
                }
            if current_user.is_authenticated and current_user.is_management:
                from app.models.user import User as _User
                teachers = _User.query.filter(
                    _User.active == True,
                    _User.role.in_(["teacher", "display"]),
                ).order_by(_User.surname, _User.name).all()
                return {
                    "impersonating": False,
                    "impersonating_as": None,
                    "real_user": None,
                    "impersonate_teachers": teachers,
                    "pantalla_mode": False,
                }
        except Exception:
            pass
        return {
            "impersonating": False,
            "impersonating_as": None,
            "real_user": None,
            "impersonate_teachers": [],
            "pantalla_mode": False,
        }

    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.absences import absences_bp
    from app.routes.guards import guards_bp
    from app.routes.activities import activities_bp
    from app.routes.admin import admin_bp
    from app.routes.chat import chat_bp
    from app.routes.display import display_bp
    from app.routes.presence import presence_bp
    from app.routes.impersonate import impersonate_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(absences_bp)
    app.register_blueprint(guards_bp)
    app.register_blueprint(activities_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(display_bp)
    app.register_blueprint(presence_bp)
    app.register_blueprint(impersonate_bp)

    if not scheduler.running:
        scheduler.start()
    from app.utils.mail_digest import reload_schedule
    reload_schedule(app)

    return app
