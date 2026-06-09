"""
Modelo Room. Representa un aula física del centro.
El aula se asigna por tramo de horario (TeacherSchedule.room_id), no al grupo.
"""
from app.extensions import db


class Room(db.Model):
    __tablename__ = "rooms"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    description = db.Column(db.String(200), nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)

    def __repr__(self):
        return f"<Room {self.name}>"
