"""
Blueprint del panel principal (inicio). Agrega por tramo horario las ausencias,
guardias, profesores disponibles, asignaciones y mensajes del chat del día.
Calcula también qué tramos son de guardia del usuario actual (is_my_guard)
para mostrar los controles de gestión directamente en el panel.
"""
from datetime import date
from flask import Blueprint, render_template, current_app
from flask_login import login_required, current_user
from app.models.guard import Guard, GuardRecord
from app.models.absence import Absence
from app.models.schedule import TeacherSchedule
from app.models.user import User
from app.models.chat import ChatMessage, ChatClear
from app.utils.guards import get_available_teachers_for_slot

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    today = date.today()
    day_idx = today.weekday()
    slots_cfg = current_app.config["TIME_SLOTS"]

    today_absences = Absence.query.filter_by(date=today).all()
    today_guards = Guard.query.filter_by(date=today).all()

    absences_by_slot = {}
    guards_by_slot = {}
    for a in today_absences:
        absences_by_slot.setdefault(a.slot_id, []).append(a)
    for g in today_guards:
        guards_by_slot.setdefault(g.slot_id, []).append(g)

    # Grupo que tenía cada profesor en cada tramo hoy
    absence_groups = {}
    absence_rooms = {}
    for a in today_absences:
        entry = TeacherSchedule.query.filter_by(
            teacher_id=a.teacher_id,
            day_of_week=day_idx,
            slot_id=a.slot_id,
            is_guard_slot=False,
        ).first()
        group = entry.group if entry else None
        absence_groups[a.id] = group.name if group else "—"
        absence_rooms[a.id] = group.room.name if group and group.room else None

    # Tramos de guardia del usuario actual
    my_guard_slot_ids = set()
    if not current_user.is_management:
        my_guard_slot_ids = {
            e.slot_id for e in TeacherSchedule.query.filter_by(
                teacher_id=current_user.id,
                day_of_week=day_idx,
                is_guard_slot=True,
            ).all()
        }

    # Profesores de guardia por tramo: todos los disponibles + cuáles ya asignados
    guard_info_by_slot = {}
    for s in slots_cfg:
        if s["is_break"]:
            continue
        sid = s["id"]

        # IDs de profesores con guardia en este tramo (no ausentes)
        guard_entry_ids = {
            e.teacher_id for e in TeacherSchedule.query.filter_by(
                day_of_week=day_idx, slot_id=sid, is_guard_slot=True
            ).all()
        }
        absent_ids = {a.teacher_id for a in absences_by_slot.get(sid, [])}
        available_ids = guard_entry_ids - absent_ids

        primary_teachers, secondary_teachers = get_available_teachers_for_slot(today, sid)

        # Registros de guardias cubiertas en este tramo hoy
        guard_ids_slot = [g.id for g in guards_by_slot.get(sid, [])]
        assigned_teacher_ids = set()
        multi_assigned = []
        if guard_ids_slot:
            from collections import Counter
            slot_records = GuardRecord.query.filter(
                GuardRecord.guard_id.in_(guard_ids_slot)
            ).all()
            cnt = Counter(r.teacher_id for r in slot_records)
            assigned_teacher_ids = set(cnt.keys())
            multi_ids = {tid for tid, n in cnt.items() if n > 1}
            multi_assigned = sorted(
                [User.query.get(tid) for tid in multi_ids if User.query.get(tid)],
                key=lambda t: t.surname,
            )

        primary_ids = {t.id for t in primary_teachers}
        extra_teachers = sorted(
            [User.query.get(tid) for tid in assigned_teacher_ids - primary_ids
             if User.query.get(tid)],
            key=lambda t: t.points,
        )

        guard_info_by_slot[sid] = {
            "primary": primary_teachers,
            "secondary": secondary_teachers,
            "assigned_ids": assigned_teacher_ids,
            "multi_assigned": multi_assigned,
            "extra": extra_teachers,
        }

    # Estructura final por tramo
    slots_data = []
    for s in slots_cfg:
        sid = s["id"]
        absences = absences_by_slot.get(sid, [])
        guards = guards_by_slot.get(sid, [])
        gi = guard_info_by_slot.get(sid, {"primary": [], "secondary": [], "assigned_ids": set(), "extra": [], "multi_assigned": []})
        n_guard_teachers = len(gi["primary"])
        n_absences = len(absences)

        pending_guards_info = [
            {
                "id": g.id,
                "label": (
                    (g.absence.teacher.full_name if g.absence else "—")
                    + " / "
                    + (g.group.name if g.group else "—")
                ),
            }
            for g in guards if g.status == "pending"
        ]

        slots_data.append({
            "slot": s,
            "absences": absences,
            "guards": guards,
            "guard_teachers": gi["primary"],
            "secondary_teachers": gi["secondary"],
            "extra_teachers": gi["extra"],
            "assigned_teacher_ids": gi["assigned_ids"],
            "multi_assigned": gi["multi_assigned"],
            "is_my_guard": sid in my_guard_slot_ids,
            "overload": n_absences > 0 and n_absences > n_guard_teachers,
            "pending_guards_info": pending_guards_info,
        })

    from datetime import datetime
    today_midnight = datetime.combine(today, datetime.min.time())
    last_clear = (ChatClear.query
                  .filter(ChatClear.cleared_at >= today_midnight)
                  .order_by(ChatClear.cleared_at.desc())
                  .first())
    chat_cutoff = last_clear.cleared_at if last_clear else today_midnight

    chat_messages = (
        ChatMessage.query
        .filter(ChatMessage.channel == "general",
                ChatMessage.created_at >= chat_cutoff)
        .order_by(ChatMessage.created_at)
        .limit(50)
        .all()
    )

    # Tareas del día por tramo (solo para management)
    from collections import defaultdict
    tasks_by_slot = defaultdict(list)
    if current_user.is_management:
        for a in today_absences:
            for task in a.tasks:
                tasks_by_slot[a.slot_id].append({
                    "teacher": a.teacher.full_name,
                    "group": task.group.name,
                    "description": task.description,
                    "attachment": task.attachment,
                    "task_id": task.id,
                })

    return render_template(
        "dashboard/index.html",
        today=today,
        slots_data=slots_data,
        my_guard_slot_ids=my_guard_slot_ids,
        absence_groups=absence_groups,
        absence_rooms=absence_rooms,
        chat_messages=chat_messages,
        tasks_by_slot=tasks_by_slot,
    )
