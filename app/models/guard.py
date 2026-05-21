from datetime import datetime
from app.extensions import db


class Guard(db.Model):
    """Guardia generada a partir de una ausencia: qué grupo queda sin profesor."""
    __tablename__ = "guards"

    id = db.Column(db.Integer, primary_key=True)
    absence_id = db.Column(db.Integer, db.ForeignKey("absences.id"), nullable=True)
    date = db.Column(db.Date, nullable=False)
    slot_id = db.Column(db.Integer, nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=True)
    # pending / covered / uncovered
    status = db.Column(db.String(20), default="pending", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    group = db.relationship("Group")
    records = db.relationship("GuardRecord", backref="guard", lazy="dynamic")


class GuardRecord(db.Model):
    """Registro de tiempo efectivo de un profesor en una guardia.

    Varios profesores pueden cubrir el mismo grupo en el mismo tramo,
    cada uno con sus propios minutos efectivos.
    """
    __tablename__ = "guard_records"

    id = db.Column(db.Integer, primary_key=True)
    guard_id = db.Column(db.Integer, db.ForeignKey("guards.id"), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # Minutos efectivos cubiertos por este profesor en esta guardia
    effective_minutes = db.Column(db.Integer, nullable=False, default=60)
    notes = db.Column(db.String(300), nullable=True)
    # Puntos calculados: minutos * multiplicador_dificultad_grupo / 60
    points_awarded = db.Column(db.Float, default=0.0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
