"""
Modelo Task. Tarea que el profesor ausente deja para su grupo.
Se asocia a una Absence y a un Group; se muestra en el panel y se incluye
en los PDFs de tareas por ausencia y por tramo horario.
"""
from datetime import datetime
from app.extensions import db


class Task(db.Model):
    """Tarea que deja el profesor ausente para su grupo."""
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    absence_id = db.Column(db.Integer, db.ForeignKey("absences.id"), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=False)
    description = db.Column(db.Text, nullable=False)
    attachment  = db.Column(db.String(200), nullable=True)   # nombre de fichero en uploads/tasks/
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    group = db.relationship("Group")
