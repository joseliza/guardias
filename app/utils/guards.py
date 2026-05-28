"""
Utilidades de asignación de guardias.
get_available_teachers_for_slot: devuelve tres pools (guardia asignada, guardia EX,
  libres sin clase) para un tramo dado, excluyendo ausentes.
auto_assign_pending_guards: asigna automáticamente profesores del pool primario
  y, si no son suficientes, del pool EX a cada guardia pendiente del tramo.
"""
from datetime import date
from app.models.user import User
from app.models.schedule import TeacherSchedule
from app.models.absence import Absence


def get_available_teachers_for_slot(target_date: date, slot_id: int):
    """Devuelve (primary, ex_guard, secondary):
    - primary:   profesores con tramo de guardia oficial, no ausentes, ordenados por puntos asc.
    - ex_guard:  profesores cuyo grupo sale completo en actividad extraescolar ese tramo,
                 no ausentes, no en primary, ordenados por puntos asc.
    - secondary: profesores sin ninguna entrada en ese tramo (libres totales), no ausentes,
                 ordenados por puntos asc.
    """
    from app.models.activity import ExtraActivity

    day_idx = target_date.weekday()

    absent_ids = {
        row[0] for row in Absence.query
        .filter_by(date=target_date, slot_id=slot_id)
        .with_entities(Absence.teacher_id)
        .all()
    }

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

    # IDs con tramo de guardia oficial asignado
    guard_slot_ids = {
        row[0] for row in TeacherSchedule.query
        .filter_by(day_of_week=day_idx, slot_id=slot_id, is_guard_slot=True)
        .with_entities(TeacherSchedule.teacher_id)
        .all()
    }

    # Pool EX: profesores cuya clase queda vacía porque el grupo sale completo
    ex_guard_ids = set()
    for act in ExtraActivity.query.filter_by(date=target_date).all():
        if slot_id not in act.slot_id_list:
            continue
        for ag in act.groups:
            if not ag.whole_group:
                continue
            for entry in TeacherSchedule.query.filter_by(
                day_of_week=day_idx,
                slot_id=slot_id,
                group_id=ag.group_id,
                is_guard_slot=False,
            ).all():
                if entry.teacher_id not in absent_ids and entry.teacher_id not in guard_slot_ids:
                    ex_guard_ids.add(entry.teacher_id)

    primary_ids   = guard_slot_ids - absent_ids
    secondary_ids = {t.id for t in all_teachers} - scheduled_ids - absent_ids - guard_slot_ids

    primary = sorted(
        [t for t in all_teachers if t.id in primary_ids],
        key=lambda t: t.points
    )
    ex_guard = sorted(
        [t for t in all_teachers if t.id in ex_guard_ids],
        key=lambda t: t.points
    )
    secondary = sorted(
        [t for t in all_teachers if t.id in secondary_ids],
        key=lambda t: t.points
    )
    return primary, ex_guard, secondary


def auto_assign_pending_guards(target_date: date, slot_id: int) -> dict:
    """Asigna una guardia por profesor disponible. Nunca repite profesor.
    Devuelve {'assigned': N, 'pending': N}."""
    from app.extensions import db
    from app.models.guard import Guard, GuardRecord
    from app.models.group import Group
    from app.utils.points import award_guard_points

    from app.models.activity import ExtraActivity

    all_pending = Guard.query.filter_by(
        date=target_date, slot_id=slot_id, status="pending"
    ).all()
    if not all_pending:
        return {"assigned": 0, "pending": 0}

    # Excluir guardias de grupos que salen completos en actividad extraescolar
    activity_group_ids = set()
    for act in ExtraActivity.query.filter_by(date=target_date).all():
        if slot_id in act.slot_id_list:
            for ag in act.groups:
                if ag.whole_group:
                    activity_group_ids.add(ag.group_id)

    pending = [g for g in all_pending if g.group_id not in activity_group_ids]
    if not pending:
        return {"assigned": 0, "pending": 0}

    # Grupos de alta dificultad primero
    pending.sort(key=lambda g: -(
        Group.query.get(g.group_id).difficulty_multiplier if g.group_id else 0
    ))

    primary, ex_guard, _secondary = get_available_teachers_for_slot(target_date, slot_id)
    pool = primary + ex_guard  # primero guardia oficial, luego guardia EX

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
