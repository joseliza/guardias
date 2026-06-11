"""
Modelo TeacherSchedule. Horario semanal fijo de cada profesor por curso escolar.
Una fila por (profesor, día, tramo, curso): si is_guard_slot=True el tramo es de guardia;
si False tiene clase con el grupo indicado; sin fila significa hora libre.
"""
from app.extensions import db


class TeacherSchedule(db.Model):
    """Horario semanal fijo de cada profesor: en qué tramo y día tiene clase con qué grupo."""
    __tablename__ = "teacher_schedules"

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    school_year_id = db.Column(db.Integer, db.ForeignKey("school_years.id"), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=True)
    # 0=Lunes … 4=Viernes
    day_of_week = db.Column(db.Integer, nullable=False)
    # 1..7 según CONFIG TIME_SLOTS
    slot_id = db.Column(db.Integer, nullable=False)
    # true → el profesor está de guardia en ese tramo (libre / sin clase)
    is_guard_slot = db.Column(db.Boolean, default=False, nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey("rooms.id"), nullable=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=True)
    notes = db.Column(db.String(120), nullable=True)

    room = db.relationship("Room", foreign_keys=[room_id])
    school_year = db.relationship("SchoolYear", foreign_keys=[school_year_id])

    __table_args__ = (
        db.UniqueConstraint("teacher_id", "day_of_week", "slot_id", "school_year_id", name="uq_schedule"),
    )
