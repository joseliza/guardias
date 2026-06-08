"""
Modelos de periodos de disponibilidad para guardia: AvailabilityPeriod y AvailabilityPeriodGroup.
AvailabilityPeriod: rango de fechas en que un profesor pone sus horas de clase
  (con grupo asignado, sin tocar el horario) a disposición para cubrir guardias.
AvailabilityPeriodGroup: restricción opcional a grupos concretos que puede cubrir;
  sin restricciones registradas, puede cubrir cualquier grupo.
"""
from datetime import datetime
from app.extensions import db


class AvailabilityPeriod(db.Model):
    """Periodo en que un profesor está disponible para guardias durante sus horas de clase."""
    __tablename__ = "availability_periods"

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    teacher = db.relationship("User", foreign_keys=[teacher_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    groups = db.relationship("AvailabilityPeriodGroup", backref="period", lazy="dynamic")


class AvailabilityPeriodGroup(db.Model):
    """Grupo concreto que el profesor puede cubrir durante el periodo (restricción opcional)."""
    __tablename__ = "availability_period_groups"

    id = db.Column(db.Integer, primary_key=True)
    period_id = db.Column(db.Integer, db.ForeignKey("availability_periods.id"), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=False)

    group = db.relationship("Group")
