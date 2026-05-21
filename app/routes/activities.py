from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from app.extensions import db, mail
from app.models.activity import ExtraActivity, ExtraActivityGroup, ExtraActivityTeacher
from app.models.absence import Absence
from app.models.guard import Guard
from app.models.user import User
from app.models.group import Group
from app.utils.points import apply_absence_penalty
from flask_mail import Message

activities_bp = Blueprint("activities", __name__, url_prefix="/extraescolares")


def _require_extracurricular():
    if not current_user.is_extracurricular:
        flash("No tienes permiso para gestionar actividades extraescolares.", "danger")
        return False
    return True


@activities_bp.route("/")
@login_required
def index():
    activities = ExtraActivity.query.order_by(ExtraActivity.date.desc()).all()
    return render_template("activities/index.html", activities=activities)


@activities_bp.route("/nueva", methods=["GET", "POST"])
@login_required
def create():
    if not _require_extracurricular():
        return redirect(url_for("dashboard.index"))

    teachers = User.query.filter_by(active=True).order_by(User.surname).all()
    groups = Group.query.filter_by(active=True).order_by(Group.name).all()
    slots = current_app.config["TIME_SLOTS"]

    if request.method == "POST":
        activity_date = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
        slot_ids = request.form.getlist("slot_ids")
        name = request.form["name"]
        description = request.form.get("description", "")

        activity = ExtraActivity(
            name=name,
            date=activity_date,
            slot_ids=",".join(slot_ids),
            description=description,
            created_by_id=current_user.id,
        )
        db.session.add(activity)
        db.session.flush()

        # Grupos afectados
        group_ids = request.form.getlist("group_ids")
        for gid in group_ids:
            whole = request.form.get(f"whole_group_{gid}") == "on"
            ag = ExtraActivityGroup(activity_id=activity.id, group_id=int(gid), whole_group=whole)
            db.session.add(ag)

        # Profesores acompañantes → ausencias automáticas
        teacher_ids = request.form.getlist("teacher_ids")
        for tid in teacher_ids:
            at = ExtraActivityTeacher(activity_id=activity.id, teacher_id=int(tid))
            db.session.add(at)
            for slot_id in slot_ids:
                existing = Absence.query.filter_by(
                    teacher_id=int(tid), date=activity_date, slot_id=int(slot_id)
                ).first()
                if not existing:
                    absence = Absence(
                        teacher_id=int(tid),
                        date=activity_date,
                        slot_id=int(slot_id),
                        reason=f"Actividad extraescolar: {name}",
                        reported_by_role="extracurricular",
                        reported_by_id=current_user.id,
                    )
                    db.session.add(absence)
                    db.session.flush()
                    guard = Guard(
                        absence_id=absence.id,
                        date=activity_date,
                        slot_id=int(slot_id),
                        status="pending",
                    )
                    db.session.add(guard)
                    apply_absence_penalty(int(tid))

        db.session.commit()

        # Email a acompañantes
        _send_task_request_emails(activity)

        flash("Actividad registrada y emails enviados a los acompañantes.", "success")
        return redirect(url_for("activities.index"))

    return render_template("activities/create.html", teachers=teachers,
                           groups=groups, slots=slots)


def _send_task_request_emails(activity: ExtraActivity):
    for at in activity.accompanying_teachers:
        teacher = at.teacher
        try:
            msg = Message(
                subject=f"Deja tareas para tu clase – {activity.name}",
                recipients=[teacher.email],
                html=render_template(
                    "activities/email_task_request.html",
                    teacher=teacher,
                    activity=activity,
                ),
            )
            mail.send(msg)
            at.email_sent = True
        except Exception:
            pass
    db.session.commit()
