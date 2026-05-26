from datetime import date
from app.models.user import User
from app.models.schedule import TeacherSchedule
from app.models.absence import Absence


def get_available_teachers_for_slot(target_date: date, slot_id: int):
    """Devuelve (primary, secondary):
    - primary: profesores con tramo de guardia asignado, no ausentes, ordenados por puntos asc.
    - secondary: profesores sin ninguna entrada en ese tramo (libres), no ausentes, ordenados por puntos asc.
    """
    day_idx = target_date.weekday()

    absent_ids = {
        row[0] for row in Absence.query
        .filter_by(date=target_date, slot_id=slot_id)
        .with_entities(Absence.teacher_id)
        .all()
    }

    # Todos los profesores activos
    all_teachers = User.query.filter_by(active=True).filter(
        User.role.notin_(["management", "display"])
    ).all()

    # IDs con entrada en ese tramo (clase o guardia)
    scheduled_ids = {
        row[0] for row in TeacherSchedule.query
        .filter_by(day_of_week=day_idx, slot_id=slot_id)
        .with_entities(TeacherSchedule.teacher_id)
        .all()
    }

    # IDs con tramo de guardia asignado
    guard_slot_ids = {
        row[0] for row in TeacherSchedule.query
        .filter_by(day_of_week=day_idx, slot_id=slot_id, is_guard_slot=True)
        .with_entities(TeacherSchedule.teacher_id)
        .all()
    }

    primary_ids   = guard_slot_ids - absent_ids
    secondary_ids = {t.id for t in all_teachers} - scheduled_ids - absent_ids - guard_slot_ids

    primary = sorted(
        [t for t in all_teachers if t.id in primary_ids],
        key=lambda t: t.points
    )
    secondary = sorted(
        [t for t in all_teachers if t.id in secondary_ids],
        key=lambda t: t.points
    )
    return primary, secondary


def auto_assign_pending_guards(target_date: date, slot_id: int) -> dict:
    """Asigna una guardia por profesor disponible. Nunca repite profesor.
    Devuelve {'assigned': N, 'pending': N}."""
    from app.extensions import db
    from app.models.guard import Guard, GuardRecord
    from app.models.group import Group
    from app.utils.points import award_guard_points

    pending = Guard.query.filter_by(
        date=target_date, slot_id=slot_id, status="pending"
    ).all()
    if not pending:
        return {"assigned": 0, "pending": 0}

    # Grupos de alta dificultad primero
    pending.sort(key=lambda g: -(
        Group.query.get(g.group_id).difficulty_multiplier if g.group_id else 0
    ))

    primary, _secondary = get_available_teachers_for_slot(target_date, slot_id)
    pool = primary  # auto-asignación solo usa profesores con guardia asignada

    # Excluir profesores ya asignados manualmente en este tramo
    already_assigned = {
        r.teacher_id for r in GuardRecord.query
        .join(Guard, GuardRecord.guard_id == Guard.id)
        .filter(Guard.date == target_date, Guard.slot_id == slot_id)
        .all()
    }

    assigned = 0
    unassigned = 0
    used_teacher_ids = set(already_assigned)

    for guard in pending:
        teacher = next((t for t in pool if t.id not in used_teacher_ids), None)
        if teacher is None:
            unassigned += 1
            continue

        group = Group.query.get(guard.group_id)
        multiplier = group.difficulty_multiplier if group else 1.0
        points = round(multiplier, 2)

        db.session.add(GuardRecord(
            guard_id=guard.id,
            teacher_id=teacher.id,
            effective_minutes=60,
            notes="Asignación automática",
            points_awarded=points,
        ))
        guard.status = "covered"
        award_guard_points(teacher.id, points)
        used_teacher_ids.add(teacher.id)
        assigned += 1

    db.session.commit()
    return {"assigned": assigned, "pending": unassigned}
