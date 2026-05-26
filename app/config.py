import os
from dotenv import load_dotenv

load_dotenv(encoding="utf-8")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "mysql+pymysql://guardias:password@db/guardias")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    MAIL_SERVER = os.getenv("MAIL_SERVER", "localhost")
    MAIL_PORT = int(os.getenv("MAIL_PORT", 587))
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER")

    INSTITUTE_NAME = os.getenv("INSTITUTE_NAME", "IES")
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@instituto.es")

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
