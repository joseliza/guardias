"""Modelo Subject (asignatura). Catálogo de asignaturas del centro con su abreviatura."""
from app.extensions import db


class Subject(db.Model):
    __tablename__ = "subjects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    abbreviation = db.Column(db.String(20), nullable=True, index=True)

    schedule_entries = db.relationship("TeacherSchedule", backref="subject", lazy="dynamic")

    def __repr__(self):
        return f"<Subject {self.name}>"
