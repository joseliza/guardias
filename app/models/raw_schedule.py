"""
Modelo RawScheduleRow. Almacena las filas del CSV de horarios tal como vienen
(con abreviaturas sin resolver) para el curso escolar indicado. Actúa como
staging area: una vez resueltas todas las abreviaturas contra la BD, se
generan los TeacherSchedule definitivos y estas filas pueden borrarse.
"""
from app.extensions import db


class RawScheduleRow(db.Model):
    __tablename__ = "raw_schedule_rows"

    id = db.Column(db.Integer, primary_key=True)
    school_year_id = db.Column(db.Integer, db.ForeignKey("school_years.id"), nullable=False)
    teacher_abbr = db.Column(db.String(20), nullable=False)
    subject_abbr = db.Column(db.String(20), nullable=True)
    group_abbr = db.Column(db.String(20), nullable=False)
    room_abbr = db.Column(db.String(20), nullable=True)
    day_of_week = db.Column(db.Integer, nullable=False)   # 1=lunes … 5=viernes
    slot_number = db.Column(db.Integer, nullable=False)   # 1–7

    school_year = db.relationship("SchoolYear")

    def __repr__(self):
        return (f"<RawScheduleRow {self.teacher_abbr} "
                f"{self.group_abbr} d{self.day_of_week}t{self.slot_number}>")
