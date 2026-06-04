"""
Factoría de la aplicación Flask. Inicializa extensiones, registra blueprints
y define el filtro Jinja2 `fecha_es` para formatear fechas en castellano.
"""
from flask import Flask
from flask_login import current_user
from app.config import Config
from app.extensions import db, login_manager, migrate, mail, socketio, oauth


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    mail.init_app(app)
    socketio.init_app(app, async_mode="eventlet", cors_allowed_origins="*")
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

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    @app.after_request
    def no_cache(response):
        if current_user.is_authenticated:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.absences import absences_bp
    from app.routes.guards import guards_bp
    from app.routes.activities import activities_bp
    from app.routes.admin import admin_bp
    from app.routes.chat import chat_bp
    from app.routes.display import display_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(absences_bp)
    app.register_blueprint(guards_bp)
    app.register_blueprint(activities_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(display_bp)

    return app
