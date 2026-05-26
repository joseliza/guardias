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

    if request.method == "POST":
        teacher_id = int(request.form["teacher_id"])
        effective_minutes = int(request.form.get("effective_minutes", 60))
        notes = request.form.get("notes", "")

        # Aviso si el profesor ya cubre otra guardia en el mismo tramo
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
        return redirect(url_for("dashboard.index") + f"#slot-{guard.slot_id}")

    return render_template("guards/assign.html", guard=guard, slot=slot,
                           primary=primary, secondary=secondary)


@guards_bp.route("/<int:guard_id>/registrar", methods=["GET", "POST"])
@login_required
def self_register(guard_id):
    """El propio profesor de guardia registra su presencia."""
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

    # Aviso si el profesor ya cubre otra guardia en el mismo tramo
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
    if not current_user.is_management:
        flash("Sin permiso.", "danger")
        return redirect(url_for("dashboard.index"))

    record = GuardRecord.query.get_or_404(record_id)
    guard = record.guard

    teacher = User.query.get(record.teacher_id)
    if teacher:
        teacher.points = round(teacher.points - record.points_awarded, 2)

    db.session.delete(record)
    db.session.flush()

    if guard.records.count() == 0:
        guard.status = "pending"

    db.session.commit()
    flash("Asignación eliminada.", "success")
    return redirect(url_for("dashboard.index") + f"#slot-{guard.slot_id}")


@guards_bp.route("/informe")
@login_required
def report():
    from sqlalchemy import func
    records = (
        db.session.query(User.name, User.surname, func.sum(GuardRecord.points_awarded),
                         func.sum(GuardRecord.effective_minutes))
        .join(GuardRecord, GuardRecord.teacher_id == User.id)
        .filter(User.role != "management")
        .group_by(User.id)
        .order_by(func.sum(GuardRecord.points_awarded).desc())
        .all()
    )
    return render_template("guards/report.html", records=records)
