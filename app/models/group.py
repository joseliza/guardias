"""
Modelo Group. Representa un grupo de alumnos (1º ESO A, 1º CFGS ASIR…).
Incluye el multiplicador de dificultad que pondera los puntos de guardia.
El aula se asigna por tramo de horario en TeacherSchedule, no al grupo.
"""
from app.extensions import db


class Group(db.Model):
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key=True)
    school_year_id = db.Column(db.Integer, db.ForeignKey("school_years.id"), nullable=True, index=True)
    name = db.Column(db.String(100), nullable=False)
    abbreviation = db.Column(db.String(20), nullable=True, index=True)
    high_difficulty = db.Column(db.Boolean, default=False, nullable=False)
    difficulty_multiplier = db.Column(db.Float, default=1.0, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    # None = grupo normal de alumnos; 'guard' = guardia oficial (pool primario);
    # 'guard_55' = guardia de mayores de 55: no se cubre si falta el profesor
    # y en ese tramo el profesor aparece solo en el pool de libres.
    guard_type = db.Column(db.String(20), nullable=True)

    school_year = db.relationship("SchoolYear", foreign_keys=[school_year_id])

    schedule_entries = db.relationship("TeacherSchedule", backref="group", lazy="dynamic")

    @staticmethod
    def detect_guard_type(abbreviation):
        """Tipo de guardia según la abreviatura usada en los CSV del centro."""
        abbr = (abbreviation or "").strip().upper()
        if abbr == "GUARD":
            return "guard"
        if abbr in ("GUA-2", "GUA2"):
            return "guard_55"
        return None

    def __repr__(self):
        return f"<Group {self.name}>"
