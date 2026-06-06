"""
Modelo Absence. Registra cada tramo en que un profesor está ausente:
motivo, quién lo notificó, estado (pending/confirmed/returned) y la
penalización de puntos aplicada. Cada ausencia puede generar una Guard.
"""
from datetime import datetime
from app.extensions import db


class Absence(db.Model):
    __tablename__ = "absences"

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    slot_id = db.Column(db.Integer, nullable=False)   # tramo horario afectado
    reason = db.Column(db.String(200), nullable=True)
    # self / guard / extracurricular
    reported_by_role = db.Column(db.String(30), default="self")
    reported_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    # pending / confirmed / returned
    status = db.Column(db.String(20), default="pending", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    returned_at = db.Column(db.DateTime, nullable=True)

    # Puntos negativos aplicados al profesor ausente
    penalty_points = db.Column(db.Float, default=-1.0, nullable=False)

    # Justificación
    justified = db.Column(db.Boolean, default=False, nullable=False)
    justification_email_sent = db.Column(db.Boolean, default=False, nullable=False)

    tasks = db.relationship("Task", backref="absence", lazy="dynamic")
    guard = db.relationship("Guard", backref="absence", uselist=False)

    reported_by = db.relationship("User", foreign_keys=[reported_by_id])
