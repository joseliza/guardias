"""
Modelo User. Representa a cualquier usuario del sistema (profesor, directivo,
pantalla, extraescolar). Almacena credenciales, rol, estado activo y puntos
acumulados de guardia. El rol determina qué partes de la app son accesibles.
"""
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    surname = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    # roles: teacher, guard_manager, extracurricular, management
    role = db.Column(db.String(30), nullable=False, default="teacher")
    active = db.Column(db.Boolean, default=True, nullable=False)
    # Puntos acumulados en el curso actual
    points = db.Column(db.Float, default=0.0, nullable=False)

    schedule_entries = db.relationship("TeacherSchedule", backref="teacher", lazy="dynamic")
    absences = db.relationship("Absence", foreign_keys="Absence.teacher_id", backref="teacher", lazy="dynamic")
    guard_records = db.relationship("GuardRecord", backref="teacher", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def full_name(self):
        return f"{self.surname}, {self.name}"

    @property
    def is_management(self):
        return self.role == "management"

    @property
    def is_extracurricular(self):
        return self.role in ("extracurricular", "management")

    def __repr__(self):
        return f"<User {self.email}>"
