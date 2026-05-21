from datetime import date, datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from app.extensions import db
from app.models.absence import Absence
from app.models.guard import Guard
from app.models.task import Task
from app.models.user import User
from app.models.group import Group
from app.utils.points import apply_absence_penalty

absences_bp = Blueprint("absences", __name__, url_prefix="/ausencias")


@absences_bp.route("/")
@login_required
def index():
    if current_user.is_management:
        absences = Absence.query.order_by(Absence.date.desc()).all()
    else:
        absences = Absence.query.filter_by(teacher_id=current_user.id).order_by(Absence.date.desc()).all()
    return render_template("absences/index.html", absences=absences,
                           slots=current_app.config["TIME_SLOTS"])


@absences_bp.route("/nueva", methods=["GET", "POST"])
@login_required
def create():
    teachers = User.query.filter_by(active=True).order_by(User.surname).all()
    slots = current_app.config["TIME_SLOTS"]

    if request.method == "POST":
        teacher_id = int(request.form.get("teacher_id", current_user.id))
        # Solo directivos pueden registrar ausencias de otros
        if teacher_id != current_user.id and not current_user.is_management:
            flash("No tienes permiso para registrar ausencias de otros profesores.", "danger")
            return redirect(url_for("absences.create"))

        absence_date = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
        slot_ids = request.form.getlist("slot_ids")
        reason = request.form.get("reason", "")

        for slot_id in slot_ids:
            existing = Absence.query.filter_by(
                teacher_id=teacher_id, date=absence_date, slot_id=int(slot_id)
            ).first()
            if existing:
                continue
            absence = Absence(
                teacher_id=teacher_id,
                date=absence_date,
                slot_id=int(slot_id),
                reason=reason,
                reported_by_role="self" if teacher_id == current_user.id else "management",
                reported_by_id=current_user.id,
            )
            db.session.add(absence)
            db.session.flush()

            # Genera la guardia pendiente
            guard = Guard(
                absence_id=absence.id,
                date=absence_date,
                slot_id=int(slot_id),
                status="pending",
            )
            db.session.add(guard)
            apply_absence_penalty(teacher_id)

        db.session.commit()
        flash("Ausencia registrada correctamente.", "success")
        return redirect(url_for("absences.index"))

    return render_template("absences/create.html", teachers=teachers, slots=slots,
                           today=date.today().isoformat())


@absences_bp.route("/<int:absence_id>/tareas", methods=["GET", "POST"])
@login_required
def tasks(absence_id):
    absence = Absence.query.get_or_404(absence_id)
    if absence.teacher_id != current_user.id and not current_user.is_management:
        flash("No tienes acceso a esta ausencia.", "danger")
        return redirect(url_for("absences.index"))

    groups = Group.query.filter_by(active=True).order_by(Group.name).all()

    if request.method == "POST":
        group_id = int(request.form["group_id"])
        description = request.form["description"]
        task = Task(absence_id=absence.id, group_id=group_id, description=description)
        db.session.add(task)
        db.session.commit()
        flash("Tarea añadida.", "success")
        return redirect(url_for("absences.tasks", absence_id=absence.id))

    return render_template("absences/tasks.html", absence=absence, groups=groups)


@absences_bp.route("/<int:absence_id>/reincorporar", methods=["POST"])
@login_required
def mark_returned(absence_id):
    absence = Absence.query.get_or_404(absence_id)
    absence.status = "returned"
    if absence.guard:
        absence.guard.status = "returned"
    db.session.commit()
    flash("Reincorporación registrada.", "success")
    return redirect(url_for("dashboard.index"))
