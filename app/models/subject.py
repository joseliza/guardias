"""Modelo Subject (asignatura). Asignaturas asociadas a un curso escolar concreto."""
from app.extensions import db


class Subject(db.Model):
    __tablename__ = "subjects"

    id = db.Column(db.Integer, primary_key=True)
    school_year_id = db.Column(db.Integer, db.ForeignKey("school_years.id"), nullable=True, index=True)
    name = db.Column(db.String(100), nullable=False)
    abbreviation = db.Column(db.String(20), nullable=True, index=True)

    school_year = db.relationship("SchoolYear", foreign_keys=[school_year_id])
    schedule_entries = db.relationship("TeacherSchedule", backref="subject", lazy="dynamic")

    def __repr__(self):
        return f"<Subject {self.name}>"
