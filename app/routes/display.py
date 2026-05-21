from datetime import date
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, abort
from flask_login import login_required, current_user
from app.extensions import db, socketio
from app.models.guard import Guard, GuardRecord
from app.models.absence import Absence
from app.models.task import Task
from app.models.group import Group
from app.models.user import User
from app.utils.points import award_guard_points
from app.utils.guards import get_available_teachers_for_slot
from flask_socketio import emit

display_bp = Blueprint("display", __name__, url_prefix="/pantalla")


def _require_display():
    if current_user.role not in ("display", "management"):
        abort(403)


@display_bp.route("/")
@login_required
def index():
    _require_display()
    today = date.today()
    day_idx = today.weekday()
    slots = [s for s in current_app.config["TIME_SLOTS"] if not s["is_break"]]
    all_slots = current_app.config["TIME_SLOTS"]

    guards = Guard.query.filter_by(date=today).order_by(Guard.slot_id).all()
    absences = Absence.query.filter_by(date=today).all()
    available_by_slot = {
        s["id"]: get_available_teachers_for_slot(today, s["id"]) for s in slots
    }

    return render_template(
        "display/index.html",
        today=today,
        slots=slots,
        all_slots=all_slots,
        guards=guards,
        absences=absences,
        available_by_slot=available_by_slot,
    )


@display_bp.route("/asignar/<int:guard_id>", methods=["POST"])
@login_required
def assign(guard_id):
    _require_display()
    guard = Guard.query.get_or_404(guard_id)
    teacher_id = int(request.form["teacher_id"])
    effective_minutes = int(request.form.get("effective_minutes", 60))
    notes = request.form.get("notes", "")

    group = Group.query.get(guard.group_id)
    multiplier = group.difficulty_multiplier if group else 1.0
    points = round((effective_minutes / 60) * multiplier, 2)

    record = GuardRecord(
        guard_id=guard.id,
        teacher_id=teacher_id,
        effective_minutes=effective_minutes,
        notes=notes,
        points_awarded=points,
    )
    db.session.add(record)
    guard.status = "covered"
    award_guard_points(teacher_id, points)
    db.session.commit()

    # Notifica al panel en tiempo real
    teacher = User.query.get(teacher_id)
    socketio.emit("guard_updated", {
        "guard_id": guard.id,
        "slot_id": guard.slot_id,
        "teacher": teacher.full_name,
        "status": "covered",
    }, room="display")

    return redirect(url_for("display.index"))


@display_bp.route("/tarea/<int:absence_id>", methods=["POST"])
@login_required
def add_task(absence_id):
    _require_display()
    absence = Absence.query.get_or_404(absence_id)
    group_id = int(request.form["group_id"])
    description = request.form["description"]
    task = Task(absence_id=absence.id, group_id=group_id, description=description)
    db.session.add(task)
    db.session.commit()
    return redirect(url_for("display.index"))


@display_bp.route("/imprimir/<date_str>/<int:slot_id>")
@login_required
def print_slot(date_str, slot_id):
    _require_display()
    target_date = date.fromisoformat(date_str)
    all_slots = current_app.config["TIME_SLOTS"]
    slot = next((s for s in all_slots if s["id"] == slot_id), None)

    absences = Absence.query.filter_by(date=target_date, slot_id=slot_id).all()
    groups = Group.query.filter_by(active=True).order_by(Group.name).all()

    # Para cada ausencia, recoger sus tareas
    tasks_by_group = {}
    for absence in absences:
        for task in absence.tasks:
            tasks_by_group.setdefault(task.group_id, []).append(task)

    return render_template(
        "display/print_slot.html",
        target_date=target_date,
        slot=slot,
        absences=absences,
        groups=groups,
        tasks_by_group=tasks_by_group,
    )
