"""
Modelo Room. Representa un aula física del centro (Aula 1 – Aula 40).
Se asigna a grupos para mostrar la ubicación en el panel y en los PDFs.
"""
from app.extensions import db


class Room(db.Model):
    __tablename__ = "rooms"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    active = db.Column(db.Boolean, default=True, nullable=False)

    groups = db.relationship("Group", backref="room", lazy=True)

    def __repr__(self):
        return f"<Room {self.name}>"
