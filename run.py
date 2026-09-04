import eventlet
eventlet.monkey_patch()

from app import create_app
from app.extensions import socketio
from werkzeug.middleware.proxy_fix import ProxyFix

app = create_app()
# Confiar en las cabeceras X-Forwarded-* del proxy inverso (nginx/traefik)
# para que Flask genere URLs con https:// correctamente
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Si la tabla de usuarios está vacía (p.ej. tras un reset fallido), recrear el admin
# para que la app arranque en estado usable sin intervención manual.
with app.app_context():
    try:
        from app.models.user import User as _U
        from app.extensions import db as _db
        from werkzeug.security import generate_password_hash as _gph
        _admin_email = app.config["ADMIN_EMAIL"]
        if not _U.query.filter_by(email=_admin_email).first():
            _db.session.add(_U(
                email=_admin_email, name="Admin", surname="Sistema",
                role="management", dev_access=True,
                password_hash=_gph("admin1234"),
            ))
            _db.session.commit()
            import logging
            logging.getLogger(__name__).warning(
                f"Tabla users vacía al arrancar: {_admin_email} recreado con contraseña admin1234"
            )
    except Exception:
        pass

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
