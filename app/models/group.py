"""
Modelo Group. Representa un grupo de alumnos (1º ESO A, 1º CFGS ASIR…).
Incluye el multiplicador de dificultad que pondera los puntos de guardia
y la referencia al aula habitual del grupo.
"""
from app.extensions import db


class Group(db.Model):
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    high_difficulty = db.Column(db.Boolean, default=False, nullable=False)
    difficulty_multiplier = db.Column(db.Float, default=1.0, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey("rooms.id"), nullable=True)

    schedule_entries = db.relationship("TeacherSchedule", backref="group", lazy="dynamic")

    def __repr__(self):
        return f"<Group {self.name}>"
