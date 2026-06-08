"""
Modelo SchoolYear. Representa un curso escolar (ej. 2025/2026).
Solo un registro puede tener is_current=True a la vez.
"""
from datetime import date
from app.extensions import db


class SchoolYear(db.Model):
    """Curso escolar: comienza el 1 de septiembre y termina el 30 de junio del año siguiente."""
    __tablename__ = "school_years"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), nullable=False, unique=True)  # "2025/2026"
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    is_current = db.Column(db.Boolean, default=False, nullable=False)

    @property
    def is_active_today(self):
        today = date.today()
        return self.start_date <= today <= self.end_date

    def __repr__(self):
        return f"<SchoolYear {self.name}>"
