from flask import Flask
from app.config import Config
from app.extensions import db, login_manager, migrate, mail, socketio


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    mail.init_app(app)
    socketio.init_app(app, async_mode="eventlet", cors_allowed_origins="*")

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Por favor, inicia sesión para acceder."
    login_manager.login_message_category = "warning"

    from app.models.user import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.absences import absences_bp
    from app.routes.guards import guards_bp
    from app.routes.activities import activities_bp
    from app.routes.admin import admin_bp
    from app.routes.chat import chat_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(absences_bp)
    app.register_blueprint(guards_bp)
    app.register_blueprint(activities_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(chat_bp)

    return app
