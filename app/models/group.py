"""
Modelo Group. Representa un grupo de alumnos (1º ESO A, 1º CFGS ASIR…).
Incluye el multiplicador de dificultad que pondera los puntos de guardia.
El aula se asigna por tramo de horario en TeacherSchedule, no al grupo.
"""
from app.extensions import db


class Group(db.Model):
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    high_difficulty = db.Column(db.Boolean, default=False, nullable=False)
    difficulty_multiplier = db.Column(db.Float, default=1.0, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)

    schedule_entries = db.relationship("TeacherSchedule", backref="group", lazy="dynamic")

    def __repr__(self):
        return f"<Group {self.name}>"
