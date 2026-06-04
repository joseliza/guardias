"""
Configuración de la aplicación leída desde variables de entorno (.env).
Define la cadena de conexión a MySQL, credenciales SMTP, nombre del centro
y la tabla de tramos horarios (TIME_SLOTS) usada en todo el sistema.
"""
import os
from dotenv import load_dotenv

import json

load_dotenv(encoding="utf-8")

_MAIL_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "instance", "mail_config.json")

def _load_mail_config():
    try:
        with open(_MAIL_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

_mail = _load_mail_config()
_points_cfg = _mail.get("POINTS", {})


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "mysql+pymysql://guardias:password@db/guardias")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    MAIL_SERVER = _mail.get("MAIL_SERVER") or os.getenv("MAIL_SERVER", "localhost")
    MAIL_PORT = int(_mail.get("MAIL_PORT") or os.getenv("MAIL_PORT", 587))
    MAIL_USE_TLS = str(_mail.get("MAIL_USE_TLS") or os.getenv("MAIL_USE_TLS", "true")).lower() == "true"
    MAIL_USERNAME = _mail.get("MAIL_USERNAME") or os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD = _mail.get("MAIL_PASSWORD") or os.getenv("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = _mail.get("MAIL_DEFAULT_SENDER") or os.getenv("MAIL_DEFAULT_SENDER")

    ABSENCE_PENALTY  = float(_points_cfg.get("absence_penalty",  -1.0))
    POINTS_PER_HOUR  = float(_points_cfg.get("points_per_hour",   1.0))
    COURSE_START     = _points_cfg.get("course_start", "")

    INSTITUTE_NAME = os.getenv("INSTITUTE_NAME", "IES")
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@instituto.es")

    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
    GOOGLE_ALLOWED_DOMAIN = os.getenv("GOOGLE_ALLOWED_DOMAIN", "iesciudadjardin.com")

    # Tramos horarios del instituto
    TIME_SLOTS = [
        {"id": 1, "label": "1ª hora",  "start": "08:15", "end": "09:15", "is_break": False},
        {"id": 2, "label": "2ª hora",  "start": "09:15", "end": "10:15", "is_break": False},
        {"id": 3, "label": "3ª hora",  "start": "10:15", "end": "11:15", "is_break": False},
        {"id": 4, "label": "Recreo",   "start": "11:15", "end": "11:45", "is_break": True},
        {"id": 5, "label": "4ª hora",  "start": "11:45", "end": "12:45", "is_break": False},
        {"id": 6, "label": "5ª hora",  "start": "12:45", "end": "13:45", "is_break": False},
        {"id": 7, "label": "6ª hora",  "start": "13:45", "end": "14:45", "is_break": False},
    ]

    DAYS_OF_WEEK = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
