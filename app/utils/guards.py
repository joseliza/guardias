"""
Utilidades de asignación de guardias.
get_available_teachers_for_slot: devuelve tres pools (guardia asignada, guardia EX,
  libres sin clase) y un diccionario de restricciones de grupo, para un tramo dado,
  excluyendo ausentes.
auto_assign_pending_guards: asigna automáticamente profesores del pool primario
  y, si no son suficientes, del pool EX a cada guardia pendiente del tramo.
"""
from datetime import date
from app.models.user import User
from app.models.schedule import TeacherSchedule
from app.models.absence import Absence
from app.utils import points_system_enabled
from app.utils.school_year import get_current_school_year


def fairness_sort_key(teachers):
    """Clave de ordenación para repartir guardias de forma equitativa: por puntos
    acumulados si el sistema de puntuación está activo (asc.), o por nº de guardias
    cubiertas históricamente si está desactivado (asc.) — evita depender de un
    valor de puntos congelado y mantiene un reparto justo."""
    if points_system_enabled():
        return lambda t: t.points
    from app.extensions import db
    from app.models.guard import GuardRecord
    from sqlalchemy import func
    teacher_ids = [t.id for t in teachers]
    counts = dict(
        db.session.query(GuardRecord.teacher_id, func.count(GuardRecord.id))
        .filter(GuardRecord.teacher_id.in_(teacher_ids))
        .group_by(GuardRecord.teacher_id)
        .all()
    ) if teacher_ids else {}
    return lambda t: counts.get(t.id, 0)


def get_available_teachers_for_slot(target_date: date, slot_id: int):
    """Devuelve (primary, ex_guard, secondary, availability_restrictions):
    - primary:   profesores con tramo de guardia oficial, no ausentes, ordenados de forma
                 equitativa (ver fairness_sort_key).
    - ex_guard:  profesores cuyo grupo sale completo en actividad extraescolar ese tramo,
                 o que tienen un periodo de disponibilidad para guardia activo y clase
                 asignada en este tramo; no ausentes, no en primary, ordenados igual que
                 primary.
    - secondary: profesores sin ninguna entrada en ese tramo (libres totales), no ausentes,
                 ordenados igual que primary.
    - availability_restrictions: {teacher_id: set(group_id) | None} — grupos que un
                 profesor con periodo de disponibilidad puede cubrir; None si no hay
                 restricción (puede cubrir cualquier grupo). Solo incluye profesores con
                 periodo de disponibilidad activo.
    """
    from app.models.activity import ExtraActivity
    from app.models.availability import AvailabilityPeriod

    day_idx = target_date.weekday()
    year_id = get_current_school_year().id

    absent_ids = {
        row[0] for row in Absence.query
        .filter_by(date=target_date, slot_id=slot_id)
        .with_entities(Absence.teacher_id)
        .all()
    }

    all_teachers = User.query.filter_by(active=True).filter(
        User.school_year_id == year_id, User.role != "display"
    ).all()

    # IDs con entrada en ese tramo (clase o guardia)
    scheduled_ids = {
        row[0] for row in TeacherSchedule.query
        .filter_by(day_of_week=day_idx, slot_id=slot_id, school_year_id=year_id)
        .with_entities(TeacherSchedule.teacher_id)
        .all()
    }

    # Tramos de guardia de mayores de 55 (materia guard_55): no cuentan como
    # ocupados — el profesor debe aparecer en el pool de libres a esa hora
    from app.models.subject import Subject
    guard55_ids = {
        row[0] for row in TeacherSchedule.query
        .join(Subject, TeacherSchedule.subject_id == Subject.id)
        .filter(
            TeacherSchedule.day_of_week == day_idx,
            TeacherSchedule.slot_id == slot_id,
            TeacherSchedule.school_year_id == year_id,
            Subject.guard_type == "guard_55",
        )
        .with_entities(TeacherSchedule.teacher_id)
        .all()
    }
    scheduled_ids -= guard55_ids

    # IDs con tramo de guardia oficial asignado
    guard_slot_ids = {
        row[0] for row in TeacherSchedule.query
        .filter_by(day_of_week=day_idx, slot_id=slot_id, is_guard_slot=True, school_year_id=year_id)
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
                school_year_id=year_id,
            ).all():
                if entry.teacher_id not in absent_ids and entry.teacher_id not in guard_slot_ids:
                    ex_guard_ids.add(entry.teacher_id)

    # Pool de disponibilidad: profesores con periodo activo y clase asignada en este tramo
    availability_restrictions = {}
    active_periods = AvailabilityPeriod.query.filter(
        AvailabilityPeriod.start_date <= target_date,
        AvailabilityPeriod.end_date >= target_date,
    ).all()
    for period in active_periods:
        tid = period.teacher_id
        if tid in absent_ids or tid in guard_slot_ids or tid in ex_guard_ids:
            continue
        has_class = TeacherSchedule.query.filter(
            TeacherSchedule.teacher_id == tid,
            TeacherSchedule.day_of_week == day_idx,
            TeacherSchedule.slot_id == slot_id,
            TeacherSchedule.is_guard_slot.is_(False),
            TeacherSchedule.group_id.isnot(None),
            TeacherSchedule.school_year_id == year_id,
        ).first()
        if not has_class:
            continue
        ex_guard_ids.add(tid)
        allowed = {pg.group_id for pg in period.groups}
        availability_restrictions[tid] = allowed or None

    primary_ids   = guard_slot_ids - absent_ids
    secondary_ids = {t.id for t in all_teachers} - scheduled_ids - absent_ids - guard_slot_ids

    sort_key = fairness_sort_key(all_teachers)
    primary = sorted(
        [t for t in all_teachers if t.id in primary_ids],
        key=sort_key
    )
    ex_guard = sorted(
        [t for t in all_teachers if t.id in ex_guard_ids],
        key=sort_key
    )
    secondary = sorted(
        [t for t in all_teachers if t.id in secondary_ids],
        key=sort_key
    )
    return primary, ex_guard, secondary, availability_restrictions


def get_support_teachers(group_id, slot_id: int, target_date: date, absent_teacher_id: int):
    """Devuelve la lista de profesores que pueden actuar como apoyo:
    comparten tramo, grupo y aula con el ausente (apoyo automático en ambos
    sentidos, p. ej. desdobles) y no están ausentes ese día."""
    if not group_id:
        return []
    day_idx = target_date.weekday()
    year_id = get_current_school_year().id
    absent_entry = TeacherSchedule.query.filter_by(
        teacher_id=absent_teacher_id, group_id=group_id,
        day_of_week=day_idx, slot_id=slot_id, is_guard_slot=False,
        school_year_id=year_id,
    ).first()
    others = TeacherSchedule.query.filter_by(
        group_id=group_id, day_of_week=day_idx,
        slot_id=slot_id, is_guard_slot=False,
        school_year_id=year_id,
        room_id=absent_entry.room_id if absent_entry else None,
    ).filter(TeacherSchedule.teacher_id != absent_teacher_id).all()
    if not others:
        return []
    absent_ids = {
        row[0] for row in Absence.query
        .filter_by(date=target_date, slot_id=slot_id)
        .with_entities(Absence.teacher_id).all()
    }
    return [User.query.get(e.teacher_id) for e in others
            if e.teacher_id not in absent_ids and User.query.get(e.teacher_id)]


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

    pending = [g for g in all_pending
               if g.group_id not in activity_group_ids
               and not get_support_teachers(
                   g.group_id, slot_id, target_date,
                   g.absence.teacher_id if g.absence else -1)]
    if not pending:
        return {"assigned": 0, "pending": 0}

    # Grupos de alta dificultad primero
    pending.sort(key=lambda g: -(
        Group.query.get(g.group_id).difficulty_multiplier if g.group_id else 0
    ))

    primary, ex_guard, _secondary, availability_restrictions = get_available_teachers_for_slot(target_date, slot_id)
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
        teacher = next(
            (t for t in pool if t.id not in used_teacher_ids
             and (availability_restrictions.get(t.id) is None
                  or guard.group_id in availability_restrictions[t.id])),
            None
        )
        if teacher is None:
            unassigned += 1
            continue

        group = Group.query.get(guard.group_id)
        multiplier = group.difficulty_multiplier if group else 1.0
        from flask import current_app
        pph = current_app.config.get("POINTS_PER_HOUR", 1.0)
        points = round(multiplier * pph, 2) \
            if (points_system_enabled() and teacher.scores_points) else 0

        db.session.add(GuardRecord(
            guard_id=guard.id,
            teacher_id=teacher.id,
            effective_minutes=60,
            notes="Asignación automática",
            points_awarded=points,
        ))
        guard.status = "covered"
        if teacher.scores_points:
            award_guard_points(teacher.id, points)
        used_teacher_ids.add(teacher.id)
        assigned += 1

    db.session.commit()
    return {"assigned": assigned, "pending": unassigned}
