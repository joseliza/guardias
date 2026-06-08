"""
Utilidades de puntuación. award_guard_points suma puntos al profesor que cubre
una guardia; apply_absence_penalty resta puntos por ausencia. Ambas funciones
ignoran los roles management y display, que no participan en el carnet de puntos,
y no hacen nada si el sistema de puntuación está desactivado en Configuración.
"""
from app.extensions import db
from app.models.user import User
from app.utils import points_system_enabled

EXCLUDED_ROLES = ("management", "display")


def award_guard_points(teacher_id: int, points: float):
    if not points_system_enabled():
        return
    teacher = User.query.get(teacher_id)
    if teacher and teacher.role not in EXCLUDED_ROLES:
        teacher.points = round(teacher.points + points, 2)
        db.session.add(teacher)


def apply_absence_penalty(teacher_id: int, penalty: float = None):
    if not points_system_enabled():
        return
    if penalty is None:
        from flask import current_app
        penalty = current_app.config.get("ABSENCE_PENALTY", -1.0)
    teacher = User.query.get(teacher_id)
    if teacher and teacher.role not in EXCLUDED_ROLES:
        teacher.points = round(teacher.points + penalty, 2)
        db.session.add(teacher)
