"""
Blueprint del panel principal (inicio). Agrega por tramo horario las ausencias,
guardias, profesores disponibles, asignaciones y mensajes del chat del día.
Calcula también qué tramos son de guardia del usuario actual (is_my_guard)
para mostrar los controles de gestión directamente en el panel.
"""
from datetime import date, timedelta
from flask import Blueprint, render_template, current_app, request
from flask_login import login_required, current_user
from app.routes.admin import _read_mail_config, GENERAL_DEFAULTS
from app.models.guard import Guard, GuardRecord
from app.models.absence import Absence
from app.models.schedule import TeacherSchedule
from app.models.user import User
from app.models.chat import ChatMessage, ChatClear
from app.utils.guards import get_available_teachers_for_slot, get_support_teachers, fairness_sort_key
from app.utils.school_year import get_current_school_year

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/ayuda")
@login_required
def help():
    import os
    path = os.path.join(current_app.root_path, "..", "instance", "help_content.md")
    try:
        with open(path, encoding="utf-8") as f:
            help_content = f.read()
    except FileNotFoundError:
        help_content = ""
    return render_template("help.html", help_content=help_content)


@dashboard_bp.route("/")
@login_required
def index():
    today = date.today()

    is_display_user = current_user.role == "display"

    fecha_str = request.args.get("fecha")
    try:
        target_date = date.fromisoformat(fecha_str) if fecha_str else today
    except ValueError:
        target_date = today

    is_today = target_date == today
    is_editable = target_date >= today
    prev_date = target_date - timedelta(days=1)
    next_date = target_date + timedelta(days=1)

    day_idx = target_date.weekday()
    slots_cfg = current_app.config["TIME_SLOTS"]
    year_id = get_current_school_year().id

    from app.models.activity import ExtraActivity

    # Solo ausencias de profesores del curso vigente: las filas archivadas de
    # cursos anteriores pueden conservar ausencias antiguas que duplicarían
    # al profesor en el tramo.
    day_absences = (
        Absence.query.join(User, Absence.teacher_id == User.id)
        .filter(Absence.date == target_date, User.school_year_id == year_id)
        .all()
    )
    day_guards = Guard.query.filter_by(date=target_date).all()

    _day_activities = ExtraActivity.query.filter_by(date=target_date).all()
    def _activity_group_ids(slot_id):
        ids = set()
        for act in _day_activities:
            if slot_id in act.slot_id_list:
                for ag in act.groups:
                    if ag.whole_group:
                        ids.add(ag.group_id)
        return ids

    # Profesores ausentes en algún tramo de hoy (aunque su ausencia no cubra
    # el tramo de guardia oficial): se muestran en gris en el pool de guardia.
    absent_teacher_ids_today = {a.teacher_id for a in day_absences if a.status != "returned"}

    absences_by_slot = {}
    guards_by_slot = {}
    for a in day_absences:
        absences_by_slot.setdefault(a.slot_id, []).append(a)
    for g in day_guards:
        guards_by_slot.setdefault(g.slot_id, []).append(g)

    absence_groups = {}
    absence_rooms = {}
    absence_support = {}  # absence.id → lista de profesores de apoyo
    for a in day_absences:
        entry = TeacherSchedule.query.filter_by(
            teacher_id=a.teacher_id,
            day_of_week=day_idx,
            slot_id=a.slot_id,
            is_guard_slot=False,
            school_year_id=year_id,
        ).first()
        group = entry.group if entry else None
        absence_groups[a.id] = group.name if group else "—"
        absence_rooms[a.id] = entry.room.name if entry and entry.room else None
        absence_support[a.id] = get_support_teachers(
            group.id if group else None, a.slot_id, target_date, a.teacher_id
        )

    my_guard_slot_ids = set()
    if not current_user.is_management:
        my_guard_slot_ids = {
            e.slot_id for e in TeacherSchedule.query.filter_by(
                teacher_id=current_user.id,
                day_of_week=day_idx,
                is_guard_slot=True,
                school_year_id=year_id,
            ).all()
        }

    guard_info_by_slot = {}
    for s in slots_cfg:
        if s["is_break"]:
            continue
        sid = s["id"]

        guard_entry_ids = {
            e.teacher_id for e in TeacherSchedule.query.filter_by(
                day_of_week=day_idx, slot_id=sid, is_guard_slot=True,
                school_year_id=year_id,
            ).all()
        }
        primary_teachers, ex_guard_teachers, secondary_teachers, _avail_restrictions = get_available_teachers_for_slot(target_date, sid)

        guard_ids_slot = [g.id for g in guards_by_slot.get(sid, []) if g.status != "returned"]
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

        primary_ids  = {t.id for t in primary_teachers}
        ex_guard_ids = {t.id for t in ex_guard_teachers}
        extra_candidates = [User.query.get(tid) for tid in assigned_teacher_ids - primary_ids - ex_guard_ids
                            if User.query.get(tid)]
        extra_teachers = sorted(extra_candidates, key=fairness_sort_key(extra_candidates))

        # Profesores con guardia oficial en este tramo pero con alguna
        # ausencia activa hoy (p. ej. ausencia en su propio tramo de guardia
        # GUARD, o en cualquier otro tramo del día sin reincorporación): se
        # muestran en gris en el pool de guardia, no como disponibles.
        absent_duty_teachers = sorted(
            [User.query.get(tid) for tid in (guard_entry_ids & absent_teacher_ids_today)
             if User.query.get(tid)],
            key=lambda t: t.surname,
        )

        guard_info_by_slot[sid] = {
            "primary": primary_teachers,
            "ex_guard": ex_guard_teachers,
            "secondary": secondary_teachers,
            "assigned_ids": assigned_teacher_ids,
            "multi_assigned": multi_assigned,
            "extra": extra_teachers,
            "absent_duty": absent_duty_teachers,
        }

    slots_data = []
    for s in slots_cfg:
        sid = s["id"]
        absences = absences_by_slot.get(sid, [])
        guards = guards_by_slot.get(sid, [])
        gi = guard_info_by_slot.get(sid, {"primary": [], "ex_guard": [], "secondary": [], "assigned_ids": set(), "extra": [], "multi_assigned": [], "absent_duty": []})
        n_guard_teachers = len(gi["primary"])

        activity_gids = _activity_group_ids(sid)

        real_pending = [
            g for g in guards
            if g.status == "pending"
            and g.group_id not in activity_gids
            and not absence_support.get(g.absence_id, [])
        ]

        pending_guards_info = [
            {
                "id": g.id,
                "label": (
                    (g.absence.teacher.full_name if g.absence else "—")
                    + " / "
                    + (g.group.name if g.group else "—")
                ),
            }
            for g in real_pending
        ]

        all_guards_info = [
            {
                "id": g.id,
                "label": (
                    (g.absence.teacher.full_name if g.absence else "—")
                    + " / "
                    + (g.group.name if g.group else "—")
                ),
            }
            for g in guards_by_slot.get(sid, [])
            if g.status != "returned"
        ]

        slots_data.append({
            "slot": s,
            "absences": absences,
            "guards": guards,
            "real_pending": real_pending,
            "guard_teachers": gi["primary"],
            "absent_duty_teachers": gi["absent_duty"],
            "ex_guard_teachers": gi["ex_guard"],
            "secondary_teachers": gi["secondary"],
            "extra_teachers": gi["extra"],
            "assigned_teacher_ids": gi["assigned_ids"],
            "multi_assigned": gi["multi_assigned"],
            "is_my_guard": sid in my_guard_slot_ids,
            "overload": len(real_pending) > 0 and len(real_pending) > n_guard_teachers,
            "pending_guards_info": pending_guards_info,
            "all_guards_info": all_guards_info,
            "activity_group_ids": activity_gids,
        })

    # Chat: mensajes del día visualizado (tras el último borrado de ese día)
    from datetime import datetime
    day_midnight = datetime.combine(target_date, datetime.min.time())
    day_end = datetime.combine(target_date + timedelta(days=1), datetime.min.time())
    last_clear = (ChatClear.query
                  .filter(ChatClear.cleared_at >= day_midnight,
                          ChatClear.cleared_at < day_end)
                  .order_by(ChatClear.cleared_at.desc())
                  .first())
    chat_cutoff = last_clear.cleared_at if last_clear else day_midnight
    chat_messages = (
        ChatMessage.query
        .filter(ChatMessage.channel == "general",
                ChatMessage.created_at >= chat_cutoff,
                ChatMessage.created_at < day_end)
        .order_by(ChatMessage.created_at)
        .limit(50)
        .all()
    )

    from collections import defaultdict
    tasks_by_slot = defaultdict(list)
    if current_user.is_management or is_display_user:
        for a in day_absences:
            task_list = list(a.tasks)
            if task_list:
                tasks_by_slot[a.slot_id].append({
                    "absence_id": a.id,
                    "teacher": a.teacher.full_name,
                    "group": absence_groups.get(a.id, "—"),
                    "tasks": [
                        {
                            "description": t.description,
                            "attachment": t.attachment,
                            "task_id": t.id,
                        }
                        for t in task_list
                    ],
                })

    gcfg = {**GENERAL_DEFAULTS, **_read_mail_config().get("GENERAL", {})}

    from datetime import datetime as _dt
    unmarkable_slot_ids = set()
    past_slot_ids = set()
    if target_date > today:
        unmarkable_slot_ids = {s["id"] for s in slots_cfg if not s.get("is_break")}
    elif is_today:
        now_t = _dt.now().time()
        for s in slots_cfg:
            if s.get("is_break"):
                continue
            try:
                s_start = _dt.strptime(s["start"], "%H:%M").time()
                s_end = _dt.strptime(s["end"], "%H:%M").time()
                if s_start <= now_t <= s_end:
                    unmarkable_slot_ids.add(s["id"])
                if now_t >= s_end:
                    past_slot_ids.add(s["id"])
            except (KeyError, ValueError):
                pass
    # target_date < today → unmarkable_slot_ids y past_slot_ids quedan vacíos

    return render_template(
        "dashboard/index.html",
        today=today,
        target_date=target_date,
        is_today=is_today,
        is_editable=is_editable,
        prev_date=prev_date,
        next_date=next_date,
        slots_data=slots_data,
        my_guard_slot_ids=my_guard_slot_ids,
        absence_groups=absence_groups,
        absence_rooms=absence_rooms,
        absence_support=absence_support,
        chat_messages=chat_messages,
        tasks_by_slot=tasks_by_slot,
        blink_guard_alert=gcfg.get("blink_guard_alert", False),
        unmarkable_slot_ids=unmarkable_slot_ids,
        past_slot_ids=past_slot_ids,
        is_display_user=is_display_user,
        absent_teacher_ids_today=absent_teacher_ids_today,
    )
