"""
Utilidades de puntuación. award_guard_points suma puntos al profesor que cubre
una guardia; apply_absence_penalty resta puntos por ausencia. Ambas funciones
ignoran los roles management y display, que no participan en el carnet de puntos,
y no hacen nada si el sistema de puntuación está desactivado en Configuración.

recalculate_points_for_year recalcula user.points para todos los profesores
sumando GuardRecord.points_awarded y Absence.penalty_points dentro del rango
de fechas del curso dado.
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


def recalculate_points_for_year(school_year):
    """Recalcula user.points para todos los profesores sumando
    GuardRecord.points_awarded y Absence.penalty_points dentro del
    rango de fechas del curso. Llama a db.session.commit() el llamador."""
    from app.models.guard import GuardRecord, Guard
    from app.models.absence import Absence
    from sqlalchemy import func

    # Suma de puntos de guardia por profesor en ese curso
    guard_pts = dict(
        db.session.query(GuardRecord.teacher_id, func.sum(GuardRecord.points_awarded))
        .join(Guard, GuardRecord.guard_id == Guard.id)
        .filter(
            Guard.date >= school_year.start_date,
            Guard.date <= school_year.end_date,
        )
        .group_by(GuardRecord.teacher_id)
        .all()
    )

    # Suma de penalizaciones por ausencia por profesor en ese curso
    absence_pts = dict(
        db.session.query(Absence.teacher_id, func.sum(Absence.penalty_points))
        .filter(
            Absence.date >= school_year.start_date,
            Absence.date <= school_year.end_date,
        )
        .group_by(Absence.teacher_id)
        .all()
    )

    # Reset a 0 solo para activos; los sustituidos (inactivos) conservan sus puntos congelados
    User.query.filter_by(active=True).update({"points": 0.0})
    for teacher in User.query.filter_by(active=True).all():
        pts = float(guard_pts.get(teacher.id) or 0) + float(absence_pts.get(teacher.id) or 0)
        if pts != 0:
            teacher.points = round(pts, 2)
