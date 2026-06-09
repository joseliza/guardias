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

    school_year = db.relationship("SchoolYear", foreign_keys=[school_year_id])

    schedule_entries = db.relationship("TeacherSchedule", backref="group", lazy="dynamic")

    def __repr__(self):
        return f"<Group {self.name}>"
