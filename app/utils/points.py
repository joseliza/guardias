"""
Utilidades de puntuación. award_guard_points suma puntos al profesor que cubre
una guardia; apply_absence_penalty resta puntos por ausencia. Ambas funciones
ignoran los roles management y display, que no participan en el carnet de puntos.
"""
from app.extensions import db
from app.models.user import User

EXCLUDED_ROLES = ("management", "display")


def award_guard_points(teacher_id: int, points: float):
    teacher = User.query.get(teacher_id)
    if teacher and teacher.role not in EXCLUDED_ROLES:
        teacher.points = round(teacher.points + points, 2)
        db.session.add(teacher)


def apply_absence_penalty(teacher_id: int, penalty: float = None):
    if penalty is None:
        from flask import current_app
        penalty = current_app.config.get("ABSENCE_PENALTY", -1.0)
    teacher = User.query.get(teacher_id)
    if teacher and teacher.role not in EXCLUDED_ROLES:
        teacher.points = round(teacher.points + penalty, 2)
        db.session.add(teacher)
