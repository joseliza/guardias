"""
Modelos RecreoZone y RecreoAssignment.
RecreoZone: zonas del patio supervisadas durante el recreo (catálogo fijo del instituto).
RecreoAssignment: asignación semanal zona→profesor con rotación automática o manual.
"""
from app.extensions import db


class RecreoZone(db.Model):
    __tablename__ = "recreo_zones"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    display_order = db.Column(db.Integer, nullable=False, default=0)
    active = db.Column(db.Boolean, nullable=False, default=True)
    # Coordenadas (% sobre plano del IES) — reservadas para modo gráfico futuro
    map_x = db.Column(db.Float, nullable=True)
    map_y = db.Column(db.Float, nullable=True)

    assignments = db.relationship("RecreoAssignment", backref="zone", lazy="dynamic",
                                  cascade="all, delete-orphan")

    def __repr__(self):
        return f"<RecreoZone {self.name}>"


class RecreoAssignment(db.Model):
    __tablename__ = "recreo_assignments"

    id = db.Column(db.Integer, primary_key=True)
    # Lunes de la semana a la que corresponde esta asignación
    week_start = db.Column(db.Date, nullable=False)
    zone_id = db.Column(db.Integer, db.ForeignKey("recreo_zones.id"), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    school_year_id = db.Column(db.Integer, db.ForeignKey("school_years.id"), nullable=False)
    # True si fue editada manualmente sobre la rotación automática
    is_manual = db.Column(db.Boolean, nullable=False, default=False)

    teacher = db.relationship("User", foreign_keys=[teacher_id])
    school_year = db.relationship("SchoolYear", foreign_keys=[school_year_id])

    __table_args__ = (
        db.UniqueConstraint("week_start", "zone_id", name="uq_recreo_week_zone"),
        db.UniqueConstraint("week_start", "teacher_id", name="uq_recreo_week_teacher"),
    )
