from app import create_app
from app.extensions import socketio
from werkzeug.middleware.proxy_fix import ProxyFix

app = create_app()
# Confiar en las cabeceras X-Forwarded-* del proxy inverso (nginx/traefik)
# para que Flask genere URLs con https:// correctamente
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
