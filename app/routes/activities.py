"""
Blueprint de actividades extraescolares. Permite registrar salidas y actividades
con los grupos participantes y los profesores acompañantes. Envía emails
automáticos a los acompañantes solicitando las tareas para los alumnos.
"""
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from app.extensions import db, mail
from app.models.activity import ExtraActivity, ExtraActivityGroup, ExtraActivityTeacher
from app.models.absence import Absence
from app.models.guard import Guard
from app.models.user import User
from app.models.group import Group
from app.models.schedule import TeacherSchedule
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
    from datetime import date as _date
    from app.utils.school_year import get_current_school_year
    year = get_current_school_year()
    activities = ExtraActivity.query.filter_by(school_year_id=year.id).order_by(ExtraActivity.date.desc()).all()
    slots = current_app.config["TIME_SLOTS"]
    return render_template("activities/index.html", activities=activities, slots=slots, today=_date.today())


@activities_bp.route("/nueva", methods=["GET", "POST"])
@login_required
def create():
    if not _require_extracurricular():
        return redirect(url_for("dashboard.index"))

    from app.utils.school_year import get_current_school_year, get_year_groups
    year = get_current_school_year()
    teachers = (User.query.filter_by(active=True)
                .filter(User.school_year_id == year.id)
                .order_by(User.surname).all())
    groups = get_year_groups(year.id)
    slots = current_app.config["TIME_SLOTS"]

    if request.method == "POST":
        activity_date = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
        slot_ids = request.form.getlist("slot_ids")
        name = request.form["name"]
        description = request.form.get("description", "")
        group_ids = request.form.getlist("group_ids")
        teacher_ids = request.form.getlist("teacher_ids")

        if activity_date < datetime.now().date():
            flash("La fecha de la actividad no puede ser anterior a la fecha actual.", "danger")
            return redirect(url_for("activities.create"))
        if not (year.start_date <= activity_date <= year.end_date):
            flash(f"La fecha debe estar dentro del curso {year.name} "
                  f"({year.start_date.strftime('%d/%m/%Y')} - {year.end_date.strftime('%d/%m/%Y')}).", "danger")
            return redirect(url_for("activities.create"))
        if not group_ids:
            flash("Debes seleccionar al menos un grupo participante.", "danger")
            return redirect(url_for("activities.create"))
        if not teacher_ids:
            flash("Debes seleccionar al menos un profesor acompañante.", "danger")
            return redirect(url_for("activities.create"))

        activity = ExtraActivity(
            school_year_id=year.id,
            name=name,
            date=activity_date,
            slot_ids=",".join(slot_ids),
            description=description,
            created_by_id=current_user.id,
        )
        db.session.add(activity)
        db.session.flush()

        # Grupos afectados
        for gid in group_ids:
            whole = request.form.get(f"whole_group_{gid}") == "on"
            ag = ExtraActivityGroup(activity_id=activity.id, group_id=int(gid), whole_group=whole)
            db.session.add(ag)

        # Profesores acompañantes → ausencias automáticas
        from app.routes.admin import _read_mail_config, GENERAL_DEFAULTS
        _gcfg = {**GENERAL_DEFAULTS, **_read_mail_config().get("GENERAL", {})}
        auto_justify = _gcfg.get("auto_justify_extracurricular", False)

        day_idx = activity_date.weekday()
        for tid in teacher_ids:
            at = ExtraActivityTeacher(activity_id=activity.id, teacher_id=int(tid))
            db.session.add(at)
            for slot_id in slot_ids:
                # Grupo que tiene el profesor en su horario para este tramo.
                # Si coincide con un grupo que sale completo en la actividad,
                # el mecanismo de "Grupo en actividad EX" del dashboard marcará
                # la guardia como cubierta sin necesidad de asignar sustituto.
                schedule_entry = TeacherSchedule.query.filter_by(
                    teacher_id=int(tid), day_of_week=day_idx, slot_id=int(slot_id),
                    is_guard_slot=False, school_year_id=year.id,
                ).first()
                group_id = schedule_entry.group_id if schedule_entry else None

                existing = Absence.query.filter_by(
                    teacher_id=int(tid), date=activity_date, slot_id=int(slot_id)
                ).filter(Absence.status != "returned").first()
                if not existing:
                    absence = Absence(
                        teacher_id=int(tid),
                        date=activity_date,
                        slot_id=int(slot_id),
                        reason=f"Actividad extraescolar: {name}",
                        reported_by_role="extracurricular",
                        reported_by_id=current_user.id,
                        justified=auto_justify,
                        penalty_points=0.0,
                    )
                    db.session.add(absence)
                    db.session.flush()
                    guard = Guard(
                        absence_id=absence.id,
                        date=activity_date,
                        slot_id=int(slot_id),
                        group_id=group_id,
                        status="pending",
                    )
                    db.session.add(guard)


        db.session.commit()

        # Email a acompañantes
        _send_task_request_emails(activity)

        flash("Actividad registrada y emails enviados a los acompañantes.", "success")
        return redirect(url_for("activities.index"))

    return render_template("activities/create.html", teachers=teachers,
                           groups=groups, slots=slots, today=datetime.now().date(), current_year=year)


@activities_bp.route("/<int:aid>/editar", methods=["GET", "POST"])
@login_required
def edit(aid):
    if not _require_extracurricular():
        return redirect(url_for("dashboard.index"))

    activity = ExtraActivity.query.get_or_404(aid)
    from app.utils.school_year import get_year_groups, get_current_school_year
    current_year = get_current_school_year()
    year_id = activity.school_year_id
    teachers_q = User.query.filter_by(active=True)
    if year_id:
        teachers_q = teachers_q.filter(User.school_year_id == year_id)
    teachers = teachers_q.order_by(User.surname).all()
    groups = get_year_groups(year_id) if year_id else Group.query.filter_by(active=True).order_by(Group.name).all()
    slots = current_app.config["TIME_SLOTS"]

    from datetime import date as _date
    is_past = activity.date < _date.today()

    if request.method == "POST" and is_past:
        flash("No se puede editar una actividad pasada.", "warning")
        return redirect(url_for("activities.edit", aid=aid))

    if request.method == "POST":
        new_date = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
        new_slot_ids = request.form.getlist("slot_ids")
        new_group_ids = request.form.getlist("group_ids")
        new_teacher_ids_list = request.form.getlist("teacher_ids")

        if new_date < _date.today():
            flash("La fecha de la actividad no puede ser anterior a la fecha actual.", "danger")
            return redirect(url_for("activities.edit", aid=aid))
        from app.utils.school_year import get_current_school_year as _get_year
        _year = _get_year()
        if not (_year.start_date <= new_date <= _year.end_date):
            flash(f"La fecha debe estar dentro del curso {_year.name} "
                  f"({_year.start_date.strftime('%d/%m/%Y')} - {_year.end_date.strftime('%d/%m/%Y')}).", "danger")
            return redirect(url_for("activities.edit", aid=aid))
        if not new_group_ids:
            flash("Debes seleccionar al menos un grupo participante.", "danger")
            return redirect(url_for("activities.edit", aid=aid))
        if not new_teacher_ids_list:
            flash("Debes seleccionar al menos un profesor acompañante.", "danger")
            return redirect(url_for("activities.edit", aid=aid))

        old_teacher_ids = {at.teacher_id for at in activity.accompanying_teachers}
        new_teacher_ids = {int(tid) for tid in new_teacher_ids_list}

        # Borrar ausencias anteriores de todos los acompañantes (se recrearán)
        from app.models.guard import Guard, GuardRecord
        for tid in old_teacher_ids:
            absences = Absence.query.filter_by(
                teacher_id=tid, date=activity.date,
                reported_by_role="extracurricular"
            ).filter(Absence.reason.contains(activity.name)).all()
            for ab in absences:
                guard = Guard.query.filter_by(absence_id=ab.id).first()
                if guard:
                    GuardRecord.query.filter_by(guard_id=guard.id).delete(synchronize_session=False)
                    db.session.delete(guard)
                db.session.delete(ab)

        # Actualizar campos
        activity.name        = request.form["name"]
        activity.date        = new_date
        activity.slot_ids    = ",".join(new_slot_ids)
        activity.description = request.form.get("description", "")

        # Grupos
        ExtraActivityGroup.query.filter_by(activity_id=aid).delete(synchronize_session=False)
        for gid in new_group_ids:
            whole = request.form.get(f"whole_group_{gid}") == "on"
            db.session.add(ExtraActivityGroup(activity_id=aid, group_id=int(gid), whole_group=whole))

        # Profesores: borrar todos y recrear
        from app.routes.admin import _read_mail_config, GENERAL_DEFAULTS
        _gcfg = {**GENERAL_DEFAULTS, **_read_mail_config().get("GENERAL", {})}
        auto_justify = _gcfg.get("auto_justify_extracurricular", False)

        new_day_idx = new_date.weekday()
        ExtraActivityTeacher.query.filter_by(activity_id=aid).delete(synchronize_session=False)
        for tid in new_teacher_ids:
            db.session.add(ExtraActivityTeacher(activity_id=aid, teacher_id=tid))
            for slot_id in new_slot_ids:
                # Grupo que tiene el profesor en su horario para este tramo.
                # Si coincide con un grupo que sale completo en la actividad,
                # el mecanismo de "Grupo en actividad EX" del dashboard marcará
                # la guardia como cubierta sin necesidad de asignar sustituto.
                schedule_entry = TeacherSchedule.query.filter_by(
                    teacher_id=tid, day_of_week=new_day_idx, slot_id=int(slot_id),
                    is_guard_slot=False, school_year_id=year_id,
                ).first()
                group_id = schedule_entry.group_id if schedule_entry else None

                existing = Absence.query.filter_by(
                    teacher_id=tid, date=new_date, slot_id=int(slot_id)
                ).filter(Absence.status != "returned").first()
                if not existing:
                    absence = Absence(
                        teacher_id=tid, date=new_date, slot_id=int(slot_id),
                        reason=f"Actividad extraescolar: {request.form['name']}",
                        reported_by_role="extracurricular",
                        reported_by_id=current_user.id,
                        justified=auto_justify,
                        penalty_points=0.0,
                    )
                    db.session.add(absence)
                    db.session.flush()
                    db.session.add(Guard(
                        absence_id=absence.id, date=new_date,
                        slot_id=int(slot_id), group_id=group_id, status="pending"
                    ))

        db.session.commit()
        flash("Actividad actualizada.", "success")
        return redirect(url_for("activities.index"))

    return render_template("activities/edit.html", activity=activity,
                           teachers=teachers, groups=groups, slots=slots,
                           is_past=is_past, today=_date.today(), current_year=current_year)


@activities_bp.route("/<int:aid>/eliminar", methods=["POST"])
@login_required
def delete(aid):
    if not _require_extracurricular():
        return redirect(url_for("dashboard.index"))

    activity = ExtraActivity.query.get_or_404(aid)
    from app.models.guard import Guard, GuardRecord

    for at in activity.accompanying_teachers:
        absences = Absence.query.filter_by(
            teacher_id=at.teacher_id, date=activity.date,
            reported_by_role="extracurricular"
        ).filter(Absence.reason.contains(activity.name)).all()
        for ab in absences:
            guard = Guard.query.filter_by(absence_id=ab.id).first()
            if guard:
                GuardRecord.query.filter_by(guard_id=guard.id).delete(synchronize_session=False)
                db.session.delete(guard)
            db.session.delete(ab)

    ExtraActivityGroup.query.filter_by(activity_id=aid).delete(synchronize_session=False)
    ExtraActivityTeacher.query.filter_by(activity_id=aid).delete(synchronize_session=False)
    name = activity.name
    db.session.delete(activity)
    db.session.commit()
    flash(f"Actividad '{name}' eliminada.", "success")
    return redirect(url_for("activities.index"))


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
