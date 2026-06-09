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
    # roles: teacher, extracurricular, management, display
    role = db.Column(db.String(30), nullable=False, default="teacher")
    active = db.Column(db.Boolean, default=True, nullable=False)
    # Puntos acumulados en el curso actual
    points = db.Column(db.Float, default=0.0, nullable=False)
    # Solo relevante para management: si True, acumula puntos como profesor normal
    track_points = db.Column(db.Boolean, default=False, nullable=False)
    # Si False, el usuario no recibe correos del sistema (resúmenes, notificaciones)
    receive_emails = db.Column(db.Boolean, default=True, nullable=False)
    # Abreviatura usada en los ficheros de horarios (ej: "EncLo")
    abbreviation = db.Column(db.String(20), nullable=True, index=True)
    # Curso escolar al que pertenece este profesor
    school_year_id = db.Column(db.Integer, db.ForeignKey("school_years.id"), nullable=True, index=True)
    # Profesor al que sustituye (se copia su horario y se pone inactivo al original)
    substitutes_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    # Si True, el dashboard y la pantalla de sala muestran "(Sustituye a X)" delante del nombre
    show_substitute_public = db.Column(db.Boolean, default=True, nullable=False)
    # Integración Google Drive: refresh_token OAuth y último file_id utilizado
    google_drive_token = db.Column(db.Text, nullable=True)
    google_drive_file_id = db.Column(db.String(200), nullable=True)
    substitutes = db.relationship(
        "User",
        primaryjoin="foreign(User.substitutes_id) == User.id",
        uselist=False,
        lazy="select",
    )

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
    def substitution_chain(self):
        """Lista de titulares en cadena: [B, C] si self→B→C."""
        chain, current, visited = [], self.substitutes, {self.id}
        while current and current.id not in visited:
            chain.append(current)
            visited.add(current.id)
            current = current.substitutes
        return chain

    @property
    def display_name(self):
        if self.substitutes_id and self.substitutes and self.show_substitute_public:
            chain = self.substitution_chain
            names = ' → '.join(t.full_name for t in chain)
            return f"{self.full_name} (Sustituye a {names})"
        return self.full_name

    @property
    def is_management(self):
        return self.role == "management"

    @property
    def is_extracurricular(self):
        return self.role in ("extracurricular", "management")

    @property
    def scores_points(self):
        """True si este usuario acumula puntos de guardia."""
        return self.role not in ("management", "display") or (self.is_management and self.track_points)

    def __repr__(self):
        return f"<User {self.email}>"
