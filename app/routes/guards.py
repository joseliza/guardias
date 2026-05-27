"""
Blueprint de guardias. Gestiona la asignación manual y automática de profesores
a guardias, la eliminación de asignaciones con reversión de puntos, la página
personal 'Mi guardia' y el historial de puntos con exportación CSV.
El helper _can_manage_slot() permite que profesores de guardia actúen sobre
su propio tramo sin necesidad de rol directivo.
"""
from datetime import date
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from app.extensions import db
from app.models.guard import Guard, GuardRecord
from app.models.absence import Absence
from app.models.user import User
from app.models.group import Group
from app.models.schedule import TeacherSchedule
from app.utils.points import award_guard_points
from app.utils.guards import get_available_teachers_for_slot, auto_assign_pending_guards

guards_bp = Blueprint("guards", __name__, url_prefix="/guardias")


def _can_manage_slot(slot_id):
    """True si el usuario puede gestionar guardias en este tramo:
    equipo directivo, pantalla, o profesor con guardia asignada en ese tramo hoy."""
    if current_user.role in ("management", "display"):
        return True
    return TeacherSchedule.query.filter_by(
        teacher_id=current_user.id,
        day_of_week=date.today().weekday(),
        slot_id=slot_id,
        is_guard_slot=True,
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

    primary, secondary = get_available_teachers_for_slot(guard.date, guard.slot_id)

    # Destino de vuelta: "my_guard" desde la página del profesor, "dashboard" por defecto
    back = request.args.get("back") or request.form.get("back", "dashboard")

    if request.method == "POST":
        teacher_id = int(request.form["teacher_id"])
        effective_minutes = int(request.form.get("effective_minutes", 60))
        notes = request.form.get("notes", "")

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
        points = round((effective_minutes / 60) * multiplier, 2)

        db.session.add(GuardRecord(
            guard_id=guard.id,
            teacher_id=teacher_id,
            effective_minutes=effective_minutes,
            notes=notes,
            points_awarded=points,
        ))
        guard.status = "covered"
        award_guard_points(teacher_id, points)
        db.session.commit()
        flash("Guardia registrada correctamente.", "success")

        if back == "my_guard":
            return redirect(url_for("guards.my_guard") + f"#slot-{guard.slot_id}")
        return redirect(url_for("dashboard.index") + f"#slot-{guard.slot_id}")

    return render_template("guards/assign.html", guard=guard, slot=slot,
                           primary=primary, secondary=secondary, back=back)


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
        points = round((effective_minutes / 60) * multiplier, 2)

        record = GuardRecord(
            guard_id=guard.id,
            teacher_id=current_user.id,
            effective_minutes=effective_minutes,
            notes=notes,
            points_awarded=points,
        )
        db.session.add(record)
        guard.status = "covered"
        award_guard_points(current_user.id, points)
        db.session.commit()
        flash("Guardia registrada.", "success")
        return redirect(url_for("dashboard.index") + f"#slot-{guard.slot_id}")

    return render_template("guards/self_register.html", guard=guard, slot=slot)


@guards_bp.route("/asignar-rapido", methods=["POST"])
@login_required
def quick_assign():
    if not current_user.is_management:
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

    group = Group.query.get(guard.group_id)
    multiplier = group.difficulty_multiplier if group else 1.0
    points = round(multiplier, 2)

    db.session.add(GuardRecord(
        guard_id=guard.id,
        teacher_id=teacher_id,
        effective_minutes=60,
        notes="Asignación rápida",
        points_awarded=points,
    ))
    guard.status = "covered"
    award_guard_points(teacher_id, points)
    db.session.commit()
    flash("Guardia asignada.", "success")
    return redirect(url_for("dashboard.index") + f"#slot-{guard.slot_id}")


@guards_bp.route("/auto-asignar/<date_str>/<int:slot_id>", methods=["POST"])
@login_required
def auto_assign_slot(date_str, slot_id):
    if not current_user.is_management:
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
    return redirect(url_for("dashboard.index") + f"#slot-{slot_id}")


@guards_bp.route("/registro/<int:record_id>/eliminar", methods=["POST"])
@login_required
def remove_record(record_id):
    record = GuardRecord.query.get_or_404(record_id)
    guard = record.guard

    if not _can_manage_slot(guard.slot_id):
        flash("Sin permiso.", "danger")
        return redirect(url_for("dashboard.index"))

    teacher = User.query.get(record.teacher_id)
    if teacher:
        teacher.points = round(teacher.points - record.points_awarded, 2)

    db.session.delete(record)
    db.session.flush()

    if guard.records.count() == 0:
        guard.status = "pending"

    db.session.commit()
    flash("Asignación eliminada.", "success")

    back = request.form.get("back", "dashboard")
    if back == "my_guard":
        return redirect(url_for("guards.my_guard") + f"#slot-{guard.slot_id}")
    return redirect(url_for("dashboard.index") + f"#slot-{guard.slot_id}")


@guards_bp.route("/mi-guardia")
@login_required
def my_guard():
    today = date.today()
    day_idx = today.weekday()

    slots_cfg = current_app.config["TIME_SLOTS"]
    slot_map = {s["id"]: s for s in slots_cfg}

    guard_slots = []
    if day_idx <= 4:
        entries = TeacherSchedule.query.filter_by(
            teacher_id=current_user.id,
            day_of_week=day_idx,
            is_guard_slot=True,
        ).all()

        for entry in sorted(entries, key=lambda e: e.slot_id):
            slot_cfg = slot_map.get(entry.slot_id)
            guards_in_slot = Guard.query.filter_by(date=today, slot_id=entry.slot_id).all()
            absences_in_slot = Absence.query.filter_by(date=today, slot_id=entry.slot_id).all()
            primary, secondary = get_available_teachers_for_slot(today, entry.slot_id)
            guard_slots.append({
                "slot": slot_cfg,
                "guards": guards_in_slot,
                "absences": absences_in_slot,
                "primary": primary,
                "secondary": secondary,
            })

    return render_template("guards/my_guard.html",
                           guard_slots=guard_slots,
                           today=today)


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
    slots_cfg = current_app.config["TIME_SLOTS"]
    slot_map = {s["id"]: s for s in slots_cfg}

    if current_user.is_management:
        teachers = (User.query
                    .filter_by(active=True)
                    .filter(User.role.notin_(["management", "display"]))
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
                        .filter(User.role.notin_(["management", "display"]))
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
    from sqlalchemy import func
    records = (
        db.session.query(User.id, User.name, User.surname,
                         func.sum(GuardRecord.points_awarded),
                         func.sum(GuardRecord.effective_minutes))
        .join(GuardRecord, GuardRecord.teacher_id == User.id)
        .filter(User.role != "management")
        .group_by(User.id)
        .order_by(func.sum(GuardRecord.points_awarded).desc())
        .all()
    )
    return render_template("guards/report.html", records=records)
