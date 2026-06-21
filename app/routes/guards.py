"""
Blueprint de guardias. Gestiona la asignación manual y automática de profesores
a guardias, la eliminación de asignaciones con reversión de puntos, la página
personal 'Mi guardia' y el historial de puntos con exportación CSV.
El helper _can_manage_slot() permite que profesores de guardia actúen sobre
su propio tramo sin necesidad de rol directivo.
"""
from datetime import date
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify
from flask_login import login_required, current_user
from app.extensions import db
from app.models.guard import Guard, GuardRecord
from app.models.absence import Absence
from app.models.user import User
from app.models.group import Group
from app.models.schedule import TeacherSchedule
from app.utils.points import award_guard_points
from app.utils.guards import get_available_teachers_for_slot, auto_assign_pending_guards
from app.utils import points_system_enabled, guard_assign_mode

guards_bp = Blueprint("guards", __name__, url_prefix="/guardias")


def _slot_duration(slot):
    """Duración del tramo en minutos."""
    sh, sm = map(int, slot["start"].split(":"))
    eh, em = map(int, slot["end"].split(":"))
    return (eh * 60 + em) - (sh * 60 + sm)


def _recalculate_guard_records(guard, slot):
    """Redistribuye los minutos del tramo a partes iguales entre todos los records.
    Si no divide exactamente, los primeros profesores reciben un minuto extra.
    Si el sistema de puntuación está desactivado, no recalcula ni toca puntos."""
    records = guard.records.order_by(GuardRecord.id).all()
    if not records:
        return
    total_min = _slot_duration(slot)
    n = len(records)
    base = total_min // n
    extra = total_min % n

    points_on = points_system_enabled()
    if points_on:
        group = db.session.get(Group, guard.group_id)
        multiplier = group.difficulty_multiplier if group else 1.0
        pph = current_app.config.get("POINTS_PER_HOUR", 1.0)

    for i, rec in enumerate(records):
        new_minutes = base + (1 if i < extra else 0)
        rec.effective_minutes = new_minutes
        if points_on:
            teacher = db.session.get(User, rec.teacher_id)
            new_points = round((new_minutes / 60) * multiplier * pph, 2) if teacher and teacher.scores_points else 0.0
            if teacher:
                teacher.points = round(teacher.points - rec.points_awarded + new_points, 2)
            rec.points_awarded = new_points


def _can_manage_slot(slot_id):
    """True si el usuario puede gestionar guardias en este tramo:
    equipo directivo, pantalla, o profesor con guardia asignada en ese tramo hoy."""
    if current_user.role in ("management", "display"):
        return True
    from app.utils.school_year import get_current_school_year
    return TeacherSchedule.query.filter_by(
        teacher_id=current_user.id,
        day_of_week=date.today().weekday(),
        slot_id=slot_id,
        is_guard_slot=True,
        school_year_id=get_current_school_year().id,
    ).first() is not None


@guards_bp.route("/")
@login_required
def index():
    target_date = request.args.get("date", date.today().isoformat())
    guards = Guard.query.filter_by(date=target_date).order_by(Guard.slot_id).all()
    slots = current_app.config["TIME_SLOTS"]
    return render_template("guards/index.html", guards=guards, slots=slots,
                           target_date=target_date)


@guards_bp.route("/<int:guard_id>/asignar", methods=["GET", "POST"])
@login_required
def assign(guard_id):
    guard = Guard.query.get_or_404(guard_id)
    slots = current_app.config["TIME_SLOTS"]
    slot = next((s for s in slots if s["id"] == guard.slot_id), None)

    primary, ex_guard, secondary, _avail_restrictions = get_available_teachers_for_slot(guard.date, guard.slot_id)

    # Destino de vuelta: "my_guard" desde la página del profesor, "dashboard" por defecto
    back = request.args.get("back") or request.form.get("back", "dashboard")

    total_min = _slot_duration(slot) if slot else 60
    existing_records = guard.records.order_by(GuardRecord.id).all()
    existing_minutes = sum(r.effective_minutes for r in existing_records)
    remaining_minutes = max(0, total_min - existing_minutes)
    suggested_minutes = total_min // (len(existing_records) + 1)

    if request.method == "POST":
        teacher_id = int(request.form["teacher_id"])
        effective_minutes = int(request.form.get("effective_minutes", suggested_minutes))
        notes = request.form.get("notes", "")

        if effective_minutes > remaining_minutes:
            flash(f"Los minutos se han ajustado al máximo disponible: {remaining_minutes} min.", "warning")
            effective_minutes = remaining_minutes

        clash = (GuardRecord.query
                 .join(Guard)
                 .filter(Guard.date == guard.date,
                         Guard.slot_id == guard.slot_id,
                         Guard.id != guard.id,
                         GuardRecord.teacher_id == teacher_id)
                 .first())
        if clash:
            teacher_name = User.query.get(teacher_id).full_name
            flash(f"Aviso: {teacher_name} ya está asignado/a a otra guardia en este tramo "
                  f"(grupos juntos). La asignación se ha registrado igualmente.", "warning")

        group = Group.query.get(guard.group_id)
        multiplier = group.difficulty_multiplier if group else 1.0
        pph = current_app.config.get("POINTS_PER_HOUR", 1.0)
        teacher = User.query.get(teacher_id)
        points = round((effective_minutes / 60) * multiplier * pph, 2) \
            if (points_system_enabled() and teacher and teacher.scores_points) else 0.0

        db.session.add(GuardRecord(
            guard_id=guard.id,
            teacher_id=teacher_id,
            effective_minutes=effective_minutes,
            notes=notes,
            points_awarded=points,
        ))
        guard.status = "covered"
        if teacher and teacher.scores_points:
            award_guard_points(teacher_id, points)
        db.session.commit()
        flash("Guardia registrada correctamente.", "success")

        if back == "my_guard":
            return redirect(url_for("guards.my_guard") + f"#slot-{guard.slot_id}")
        return redirect(url_for("dashboard.index", fecha=guard.date.isoformat()) + f"#slot-{guard.slot_id}")

    return render_template("guards/assign.html", guard=guard, slot=slot,
                           primary=primary, ex_guard=ex_guard,
                           secondary=secondary, back=back,
                           slot_total=total_min,
                           suggested_minutes=suggested_minutes,
                           remaining_minutes=remaining_minutes,
                           existing_minutes=existing_minutes)


@guards_bp.route("/<int:guard_id>/registrar", methods=["GET", "POST"])
@login_required
def self_register(guard_id):
    guard = Guard.query.get_or_404(guard_id)
    slots = current_app.config["TIME_SLOTS"]
    slot = next((s for s in slots if s["id"] == guard.slot_id), None)

    if request.method == "POST":
        effective_minutes = int(request.form.get("effective_minutes", 60))
        notes = request.form.get("notes", "")

        group = Group.query.get(guard.group_id)
        multiplier = group.difficulty_multiplier if group else 1.0
        pph = current_app.config.get("POINTS_PER_HOUR", 1.0)
        points = round((effective_minutes / 60) * multiplier * pph, 2) \
            if (points_system_enabled() and current_user.scores_points) else 0

        record = GuardRecord(
            guard_id=guard.id,
            teacher_id=current_user.id,
            effective_minutes=effective_minutes,
            notes=notes,
            points_awarded=points,
        )
        db.session.add(record)
        guard.status = "covered"
        if current_user.scores_points:
            award_guard_points(current_user.id, points)
        db.session.commit()
        flash("Guardia registrada.", "success")
        return redirect(url_for("dashboard.index", fecha=guard.date.isoformat()) + f"#slot-{guard.slot_id}")

    return render_template("guards/self_register.html", guard=guard, slot=slot)


@guards_bp.route("/asignar-rapido", methods=["POST"])
@login_required
def quick_assign():
    if current_user.role not in ("management", "display"):
        flash("Sin permiso.", "danger")
        return redirect(url_for("dashboard.index"))

    guard_id = int(request.form["guard_id"])
    teacher_id = int(request.form["teacher_id"])

    guard = Guard.query.get_or_404(guard_id)

    clash = (GuardRecord.query
             .join(Guard)
             .filter(Guard.date == guard.date,
                     Guard.slot_id == guard.slot_id,
                     Guard.id != guard.id,
                     GuardRecord.teacher_id == teacher_id)
             .first())
    if clash:
        teacher_name = User.query.get(teacher_id).full_name
        flash(f"Aviso: {teacher_name} ya está asignado/a a otra guardia en este tramo "
              f"(grupos juntos). La asignación se ha registrado igualmente.", "warning")

    db.session.add(GuardRecord(
        guard_id=guard.id,
        teacher_id=teacher_id,
        effective_minutes=0,
        notes="Asignación rápida",
        points_awarded=0.0,
    ))
    guard.status = "covered"
    db.session.flush()
    slots_cfg = current_app.config["TIME_SLOTS"]
    slot = next((s for s in slots_cfg if s["id"] == guard.slot_id), None)
    if slot:
        _recalculate_guard_records(guard, slot)
    db.session.commit()
    flash("Guardia asignada.", "success")
    return redirect(url_for("dashboard.index", fecha=guard.date.isoformat()) + f"#slot-{guard.slot_id}")


@guards_bp.route("/asignar-ajax", methods=["POST"])
@login_required
def quick_assign_ajax():
    guard_id   = request.form.get("guard_id",   type=int)
    teacher_id = request.form.get("teacher_id", type=int)
    if not guard_id or not teacher_id:
        return jsonify(ok=False, error="Datos incompletos.")

    guard = Guard.query.get(guard_id)
    if not guard:
        return jsonify(ok=False, error="Guardia no encontrada.")

    if not _can_manage_slot(guard.slot_id):
        return jsonify(ok=False, error="Sin permiso.")

    warning = None
    clash = (GuardRecord.query
             .join(Guard)
             .filter(Guard.date == guard.date,
                     Guard.slot_id == guard.slot_id,
                     Guard.id != guard.id,
                     GuardRecord.teacher_id == teacher_id)
             .first())
    if clash:
        teacher_name = User.query.get(teacher_id).full_name
        warning = (f"Aviso: {teacher_name} ya cubre otra guardia en este tramo. "
                   f"La asignación se ha registrado igualmente.")

    db.session.add(GuardRecord(
        guard_id=guard.id,
        teacher_id=teacher_id,
        effective_minutes=0,
        notes="Asignación rápida",
        points_awarded=0.0,
    ))
    guard.status = "covered"
    db.session.flush()
    slots_cfg = current_app.config["TIME_SLOTS"]
    slot = next((s for s in slots_cfg if s["id"] == guard.slot_id), None)
    if slot:
        _recalculate_guard_records(guard, slot)
    db.session.commit()
    return jsonify(ok=True, slot_id=guard.slot_id, warning=warning)


@guards_bp.route("/<int:guard_id>/reordenar", methods=["POST"])
@login_required
def reorder_records(guard_id):
    guard = Guard.query.get_or_404(guard_id)

    if not _can_manage_slot(guard.slot_id):
        return jsonify(ok=False, error="Sin permiso.")

    record_ids_str = request.form.get("record_ids", "")
    try:
        new_order_ids = [int(x) for x in record_ids_str.split(",") if x.strip()]
    except ValueError:
        return jsonify(ok=False, error="Orden inválido.")

    records_by_id = guard.records.order_by(GuardRecord.id).all()
    if len(new_order_ids) != len(records_by_id):
        return jsonify(ok=False, error="Número de registros incorrecto.")

    records_map = {r.id: r for r in records_by_id}
    if any(rid not in records_map for rid in new_order_ids):
        return jsonify(ok=False, error="Registro no encontrado.")

    # Revertir puntos actuales antes del swap para no desajustar contadores
    points_on = points_system_enabled()
    for rec in records_by_id:
        if points_on:
            teacher = db.session.get(User, rec.teacher_id)
            if teacher:
                teacher.points = round(teacher.points - rec.points_awarded, 2)
        rec.points_awarded = 0.0

    # Asignar teacher_ids en el nuevo orden sobre los registros ordenados por id
    new_teacher_ids = [records_map[rid].teacher_id for rid in new_order_ids]
    for rec, new_tid in zip(records_by_id, new_teacher_ids):
        rec.teacher_id = new_tid

    db.session.flush()
    slots_cfg = current_app.config["TIME_SLOTS"]
    slot = next((s for s in slots_cfg if s["id"] == guard.slot_id), None)
    if slot:
        _recalculate_guard_records(guard, slot)
    db.session.commit()
    return jsonify(ok=True, slot_id=guard.slot_id)


@guards_bp.route("/auto-asignar/<date_str>/<int:slot_id>", methods=["POST"])
@login_required
def auto_assign_slot(date_str, slot_id):
    if current_user.role not in ("management", "display"):
        flash("Sin permiso.", "danger")
        return redirect(url_for("dashboard.index"))
    target_date = date.fromisoformat(date_str)
    result = auto_assign_pending_guards(target_date, slot_id)
    if result["assigned"]:
        flash(f"{result['assigned']} guardia(s) asignada(s) automáticamente.", "success")
    if result["pending"]:
        flash(f"{result['pending']} guardia(s) sin cubrir: no hay profesores disponibles suficientes.", "danger")
    if not result["assigned"] and not result["pending"]:
        flash("No hay guardias pendientes en este tramo.", "info")
    return redirect(url_for("dashboard.index", fecha=target_date.isoformat()) + f"#slot-{slot_id}")


@guards_bp.route("/<int:guard_id>/sin-cobertura", methods=["POST"])
@login_required
def mark_no_cover(guard_id):
    """Marca la guardia como cubierta sin asignar profesor (no necesita cobertura)."""
    if current_user.role not in ("management", "display"):
        flash("Sin permiso.", "danger")
        return redirect(url_for("dashboard.index"))
    guard = Guard.query.get_or_404(guard_id)
    guard.status = "covered"
    db.session.commit()
    flash("Guardia marcada como no necesita cobertura.", "success")
    return redirect(url_for("dashboard.index", fecha=guard.date.isoformat()) + f"#slot-{guard.slot_id}")


@guards_bp.route("/registro/<int:record_id>/eliminar", methods=["POST"])
@login_required
def remove_record(record_id):
    record = GuardRecord.query.get_or_404(record_id)
    guard = record.guard

    if not _can_manage_slot(guard.slot_id):
        flash("Sin permiso.", "danger")
        return redirect(url_for("dashboard.index", fecha=guard.date.isoformat()))

    if points_system_enabled():
        teacher = db.session.get(User, record.teacher_id)
        if teacher:
            teacher.points = round(teacher.points - record.points_awarded, 2)

    db.session.delete(record)
    db.session.flush()

    if guard.records.count() == 0:
        guard.status = "pending"
    else:
        slots_cfg = current_app.config["TIME_SLOTS"]
        slot = next((s for s in slots_cfg if s["id"] == guard.slot_id), None)
        if slot:
            _recalculate_guard_records(guard, slot)

    db.session.commit()
    flash("Asignación eliminada.", "success")

    back = request.form.get("back", "dashboard")
    if back == "my_guard":
        return redirect(url_for("guards.my_guard") + f"#slot-{guard.slot_id}")
    return redirect(url_for("dashboard.index", fecha=guard.date.isoformat()) + f"#slot-{guard.slot_id}")


@guards_bp.route("/mi-guardia")
@login_required
def my_guard():
    from datetime import timedelta
    today = date.today()

    fecha_str = request.args.get("fecha")
    try:
        target_date = date.fromisoformat(fecha_str) if fecha_str else today
    except ValueError:
        target_date = today

    is_today = target_date == today
    prev_date = target_date - timedelta(days=1)
    next_date = target_date + timedelta(days=1)
    day_idx = target_date.weekday()

    slots_cfg = current_app.config["TIME_SLOTS"]
    slot_map = {s["id"]: s for s in slots_cfg}

    guard_slots = []
    if day_idx <= 4:
        from app.models.activity import ExtraActivity
        _day_acts = ExtraActivity.query.filter_by(date=target_date).all()

        def _activity_gids(slot_id):
            ids = set()
            for act in _day_acts:
                if slot_id in act.slot_id_list:
                    for ag in act.groups:
                        if ag.whole_group:
                            ids.add(ag.group_id)
            return ids

        from app.utils.school_year import get_current_school_year
        entries = TeacherSchedule.query.filter_by(
            teacher_id=current_user.id,
            day_of_week=day_idx,
            is_guard_slot=True,
            school_year_id=get_current_school_year().id,
        ).all()

        for entry in sorted(entries, key=lambda e: e.slot_id):
            slot_cfg = slot_map.get(entry.slot_id)
            guards_in_slot = Guard.query.filter_by(date=target_date, slot_id=entry.slot_id).all()
            absences_in_slot = Absence.query.filter_by(date=target_date, slot_id=entry.slot_id).all()
            primary, ex_guard, secondary, _avail_restrictions = get_available_teachers_for_slot(target_date, entry.slot_id)
            guard_slots.append({
                "slot": slot_cfg,
                "guards": guards_in_slot,
                "absences": absences_in_slot,
                "primary": primary,
                "ex_guard": ex_guard,
                "secondary": secondary,
                "activity_group_ids": _activity_gids(entry.slot_id),
            })

    past_slot_ids = set()
    if is_today:
        from datetime import datetime as _dt
        now_t = _dt.now().time()
        for s in slots_cfg:
            if s.get("is_break"):
                continue
            try:
                if now_t >= _dt.strptime(s["end"], "%H:%M").time():
                    past_slot_ids.add(s["id"])
            except (KeyError, ValueError):
                pass

    return render_template("guards/my_guard.html",
                           guard_slots=guard_slots,
                           today=today,
                           target_date=target_date,
                           prev_date=prev_date,
                           next_date=next_date,
                           is_today=is_today,
                           past_slot_ids=past_slot_ids)


def _build_events(teacher_id, slot_map):
    records = (GuardRecord.query
               .filter_by(teacher_id=teacher_id)
               .join(Guard, GuardRecord.guard_id == Guard.id)
               .all())
    absences = Absence.query.filter_by(teacher_id=teacher_id).all()

    events = []
    for r in records:
        g = r.guard
        group = Group.query.get(g.group_id) if g.group_id else None
        multiplier = group.difficulty_multiplier if group else 1.0
        events.append({
            "date": g.date,
            "slot": slot_map.get(g.slot_id),
            "type": "guard",
            "group": group,
            "special": multiplier > 1.0,
            "multiplier": multiplier,
            "minutes": r.effective_minutes,
            "points": r.points_awarded,
            "detail": r.notes or "",
        })
    for a in absences:
        group = a.guard.group if a.guard else None
        events.append({
            "date": a.date,
            "slot": slot_map.get(a.slot_id),
            "type": "absence",
            "group": group,
            "special": False,
            "multiplier": None,
            "minutes": None,
            "points": a.penalty_points,
            "detail": a.reason or "",
        })
    events.sort(key=lambda e: (e["date"], e["slot"]["id"] if e["slot"] else 0), reverse=True)
    return events


@guards_bp.route("/mis-puntos")
@login_required
def my_points():
    if not points_system_enabled():
        flash("El sistema de puntuación está desactivado actualmente.", "info")
        return redirect(url_for("dashboard.index"))
    if not current_user.is_management and guard_assign_mode() != "scoring":
        flash("El sistema de puntuación está activo solo en modo consulta para dirección.", "info")
        return redirect(url_for("dashboard.index"))

    slots_cfg = current_app.config["TIME_SLOTS"]
    slot_map = {s["id"]: s for s in slots_cfg}

    if current_user.is_management:
        teachers = (User.query
                    .filter_by(active=True)
                    .filter(User.role != "display")
                    .filter(db.or_(User.role != "management", User.track_points == True))
                    .order_by(User.surname, User.name)
                    .all())
        teacher_id = request.args.get("teacher_id", type=int)
        selected = User.query.get(teacher_id) if teacher_id else None
        events = _build_events(teacher_id, slot_map) if selected else []
        return render_template("guards/my_points.html",
                               events=events,
                               total_points=selected.points if selected else None,
                               teachers=teachers,
                               selected_teacher=selected)

    events = _build_events(current_user.id, slot_map)
    return render_template("guards/my_points.html",
                           events=events,
                           total_points=current_user.points,
                           teachers=None,
                           selected_teacher=None)


@guards_bp.route("/mis-puntos/csv")
@login_required
def my_points_csv():
    if not current_user.is_management:
        flash("Sin permiso.", "danger")
        return redirect(url_for("guards.my_points"))

    import csv
    import io
    from flask import Response

    slots_cfg = current_app.config["TIME_SLOTS"]
    slot_map = {s["id"]: s for s in slots_cfg}

    teacher_id = request.args.get("teacher_id", type=int)
    if teacher_id:
        t = User.query.get_or_404(teacher_id)
        pairs = [(t, _build_events(t.id, slot_map))]
        filename = f"puntos_{t.surname}_{t.name}.csv"
    else:
        all_teachers = (User.query
                        .filter_by(active=True)
                        .filter(User.role != "display")
                        .filter(db.or_(User.role != "management", User.track_points == True))
                        .order_by(User.surname, User.name)
                        .all())
        pairs = [(t, _build_events(t.id, slot_map)) for t in all_teachers]
        filename = "puntos_profesores.csv"

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Profesor", "Fecha", "Tramo", "Tipo", "Grupo",
                     "Dificultad", "Multiplicador", "Minutos", "Puntos", "Detalle"])
    for teacher, events in pairs:
        for e in events:
            writer.writerow([
                teacher.full_name,
                e["date"].strftime("%d/%m/%Y"),
                e["slot"]["label"] if e["slot"] else "",
                "Guardia cubierta" if e["type"] == "guard" else "Ausencia propia",
                e["group"].name if e["group"] else "",
                "Especial" if e["special"] else "Normal",
                e["multiplier"] if e["multiplier"] is not None else "",
                e["minutes"] if e["minutes"] is not None else "",
                e["points"],
                e["detail"],
            ])

    output.seek(0)
    return Response(
        "﻿" + output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@guards_bp.route("/informe")
@login_required
def report():
    if not points_system_enabled():
        flash("El sistema de puntuación está desactivado actualmente.", "info")
        return redirect(url_for("dashboard.index"))
    if not current_user.is_management and guard_assign_mode() != "scoring":
        flash("El informe de puntuación solo está disponible para dirección.", "info")
        return redirect(url_for("dashboard.index"))

    from sqlalchemy import func
    from app.models.school_year import SchoolYear
    from app.utils.school_year import get_current_school_year

    years = SchoolYear.query.order_by(SchoolYear.start_date.desc()).all()
    selected_year = get_current_school_year()
    year_id = request.args.get("year_id", type=int)
    if year_id:
        selected_year = next((y for y in years if y.id == year_id), selected_year)

    records = (
        db.session.query(User.id, User.name, User.surname,
                         func.sum(GuardRecord.points_awarded),
                         func.sum(GuardRecord.effective_minutes))
        .join(GuardRecord, GuardRecord.teacher_id == User.id)
        .join(Guard, GuardRecord.guard_id == Guard.id)
        .filter(db.or_(User.role != "management", User.track_points == True))
        .filter(User.role != "display")
        .filter(User.school_year_id == selected_year.id)
        .filter(Guard.date >= selected_year.start_date, Guard.date <= selected_year.end_date)
        .group_by(User.id)
        .order_by(func.sum(GuardRecord.points_awarded).desc())
        .all()
    )
    return render_template("guards/report.html", records=records,
                           years=years, selected_year=selected_year)
