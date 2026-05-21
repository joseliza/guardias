from datetime import date
from app.models.user import User
from app.models.schedule import TeacherSchedule
from app.models.absence import Absence


def get_available_teachers_for_slot(target_date: date, slot_id: int):
    """Devuelve profesores con guardia asignada en ese tramo que no estén ausentes,
    ordenados por puntos ascendente (primero los que menos guardias acumulan)."""
    day_idx = target_date.weekday()

    guard_slot_ids = (
        TeacherSchedule.query
        .filter_by(day_of_week=day_idx, slot_id=slot_id, is_guard_slot=True)
        .with_entities(TeacherSchedule.teacher_id)
        .all()
    )
    guard_teacher_ids = {row[0] for row in guard_slot_ids}

    absent_ids = (
        Absence.query
        .filter_by(date=target_date, slot_id=slot_id)
        .with_entities(Absence.teacher_id)
        .all()
    )
    absent_teacher_ids = {row[0] for row in absent_ids}

    available_ids = guard_teacher_ids - absent_teacher_ids

    if not available_ids:
        return []

    return (
        User.query
        .filter(User.id.in_(available_ids), User.active == True)
        .order_by(User.points.asc())
        .all()
    )
