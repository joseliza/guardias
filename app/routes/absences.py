"""
Blueprint de ausencias. Permite registrar ausencias de profesores, añadir tareas
para el grupo que queda sin clase, marcar reincorporaciones y generar PDFs
(por ausencia individual o por tramo horario completo con grupos y aulas).
"""
import os
from datetime import date, datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify, session, abort
from flask_login import login_required, current_user
from app.extensions import db
from app.models.absence import Absence
from app.models.guard import Guard
from app.models.task import Task
from app.models.user import User
from app.models.group import Group
from app.models.schedule import TeacherSchedule
from app.models.activity import ExtraActivity, ExtraActivityTeacher
from app.utils.points import apply_absence_penalty
from app.utils.guards import auto_assign_pending_guards
from app.utils import auto_assign_guards_enabled
from app.utils.school_year import get_current_school_year
from app.routes.admin import _get_institute_name

absences_bp = Blueprint("absences", __name__, url_prefix="/ausencias")


@absences_bp.route("/")
@login_required
def index():
    from collections import defaultdict
    from datetime import timedelta

    today = date.today()
    fecha_str = request.args.get("fecha")
    try:
        target_date = date.fromisoformat(fecha_str) if fecha_str else today
    except ValueError:
        target_date = today

    is_today    = target_date == today
    is_editable = target_date >= today
    prev_date   = target_date - timedelta(days=1)
    next_date   = target_date + timedelta(days=1)

    # Modal de advertencia de tareas — persiste en sesión hasta descartar o completar
    if request.args.get("dismiss_tasks"):
        session.pop("task_prompt_ids", None)
        return redirect(url_for("absences.index", fecha=target_date.isoformat()))

    from app.routes.admin import _read_mail_config, GENERAL_DEFAULTS
    _gcfg = {**GENERAL_DEFAULTS, **_read_mail_config().get("GENERAL", {})}
    _see_all = _gcfg.get("teachers_see_all_absences", True)

    _q = (Absence.query.join(User, Absence.teacher_id == User.id)
          .filter(Absence.date == target_date,
                  User.school_year_id == get_current_school_year().id))
    if not _see_all and not current_user.is_management:
        _q = _q.filter(Absence.teacher_id == current_user.id)
    absences = _q.order_by(Absence.slot_id).all()

    slots_cfg = current_app.config["TIME_SLOTS"]
    slot_map = {s["id"]: s for s in slots_cfg}

    by_slot = defaultdict(list)
    for a in absences:
        by_slot[a.slot_id].append(a)

    slots_in_day = [
        {"slot": slot_map.get(sid), "absences": by_slot[sid]}
        for sid in sorted(by_slot.keys())
    ]

    prompt_ids = session.get("task_prompt_ids", [])
    prompt_absences = []
    if prompt_ids:
        _pq = Absence.query.filter(Absence.id.in_(prompt_ids))
        if not current_user.is_management:
            _pq = _pq.filter(Absence.teacher_id == current_user.id)
        for a in _pq.all():
            prompt_absences.append({"absence": a, "has_tasks": a.tasks.count() > 0})
        if not prompt_absences or all(p["has_tasks"] for p in prompt_absences):
            session.pop("task_prompt_ids", None)
            prompt_absences = []

    now_t = datetime.now().time()
    active_slot_ids = set()
    past_slot_ids = set()
    for s in slots_cfg:
        if s.get("is_break"):
            continue
        try:
            s_start = datetime.strptime(s["start"], "%H:%M").time()
            s_end = datetime.strptime(s["end"], "%H:%M").time()
            if s_start <= now_t <= s_end:
                active_slot_ids.add(s["id"])
            if now_t >= s_end:
                past_slot_ids.add(s["id"])
        except (KeyError, ValueError):
            pass

    return render_template("absences/index.html",
                           slots_in_day=slots_in_day, slots=slots_cfg,
                           slot_map=slot_map, today=today,
                           target_date=target_date, prev_date=prev_date,
                           next_date=next_date, is_today=is_today, is_editable=is_editable,
                           past_slot_ids=past_slot_ids,
                           prompt_absences=prompt_absences,
                           active_slot_ids=active_slot_ids)


@absences_bp.route("/nueva", methods=["GET", "POST"])
@login_required
def create():
    teachers = (User.query.filter_by(active=True)
                .filter(User.school_year_id == get_current_school_year().id)
                .order_by(User.surname).all())
    slots = current_app.config["TIME_SLOTS"]

    if request.method == "POST":
        teacher_id = int(request.form.get("teacher_id", current_user.id))
        # Solo directivos y pantalla pueden registrar ausencias de otros
        if teacher_id != current_user.id and not current_user.is_management and current_user.role != "display":
            flash("No tienes permiso para registrar ausencias de otros profesores.", "danger")
            return redirect(url_for("absences.create"))

        absence_date = datetime.strptime(request.form["date"], "%Y-%m-%d").date()
        slot_ids = request.form.getlist("slot_ids")
        reason = request.form.get("comment", "") or request.form.get("reason", "")

        today_d = date.today()
        now_t = datetime.now().time()

        if absence_date < today_d:
            flash("No se pueden registrar ausencias para fechas pasadas.", "danger")
            return redirect(url_for("absences.create"))

        year = get_current_school_year()
        if not (year.start_date <= absence_date <= year.end_date):
            flash(f"La fecha debe estar dentro del curso {year.name} "
                  f"({year.start_date.strftime('%d/%m/%Y')} - {year.end_date.strftime('%d/%m/%Y')}).", "danger")
            return redirect(url_for("absences.create"))

        day_idx = absence_date.weekday()
        year_id = year.id
        configured_penalty = current_app.config.get("ABSENCE_PENALTY", -1.0)
        slots_cfg = {s["id"]: s for s in current_app.config["TIME_SLOTS"]}
        skipped_free = []
        skipped_past = []
        skipped_duplicate = []
        normal_slot_ids = []  # tramos normales para auto-asignar después
        created_ids = []
        task_ids = []  # subconjunto de created_ids para los que tiene sentido pedir tareas

        for slot_id in slot_ids:
            slot_id = int(slot_id)
            slot_cfg = slots_cfg.get(slot_id, {})
            is_break = slot_cfg.get("is_break", False)
            label = slot_cfg.get("label", f"Tramo {slot_id}")

            # Tramo ya pasado (solo aplica si es hoy)
            if absence_date == today_d:
                try:
                    slot_end_t = datetime.strptime(slot_cfg["end"], "%H:%M").time()
                    if now_t > slot_end_t:
                        skipped_past.append(label)
                        continue
                except (KeyError, ValueError):
                    pass

            existing = Absence.query.filter_by(
                teacher_id=teacher_id, date=absence_date, slot_id=slot_id
            ).filter(Absence.status != "returned").first()
            if existing:
                skipped_duplicate.append(label)
                continue

            if is_break:
                # Recreo: solo registrar si el profesor tenía recreo asignado en su horario
                has_break_slot = TeacherSchedule.query.filter_by(
                    teacher_id=teacher_id,
                    day_of_week=day_idx,
                    slot_id=slot_id,
                    school_year_id=year_id,
                ).first()
                if not has_break_slot:
                    skipped_free.append(slot_cfg.get("label", f"Tramo {slot_id}"))
                    continue
                ab = Absence(
                    teacher_id=teacher_id,
                    date=absence_date,
                    slot_id=slot_id,
                    reason=reason,
                    reported_by_role="self" if teacher_id == current_user.id else "management",
                    reported_by_id=current_user.id,
                    penalty_points=0.0,
                )
                db.session.add(ab)
                db.session.flush()
                created_ids.append(ab.id)
                task_ids.append(ab.id)
                continue

            # Tramo normal: verificar que el profesor tiene algo asignado
            schedule_entry = TeacherSchedule.query.filter_by(
                teacher_id=teacher_id,
                day_of_week=day_idx,
                slot_id=slot_id,
                is_guard_slot=False,
                school_year_id=year_id,
            ).first()
            has_guard_slot = TeacherSchedule.query.filter_by(
                teacher_id=teacher_id,
                day_of_week=day_idx,
                slot_id=slot_id,
                is_guard_slot=True,
                school_year_id=year_id,
            ).first()

            if not schedule_entry and not has_guard_slot:
                skipped_free.append(slot_cfg.get("label", f"Tramo {slot_id}"))
                continue

            group_id = schedule_entry.group_id if schedule_entry else None

            absence = Absence(
                teacher_id=teacher_id,
                date=absence_date,
                slot_id=slot_id,
                reason=reason,
                reported_by_role="self" if teacher_id == current_user.id else "management",
                reported_by_id=current_user.id,
                penalty_points=0.0,
            )
            db.session.add(absence)
            db.session.flush()
            created_ids.append(absence.id)

            is_guard_55 = (schedule_entry and schedule_entry.group
                           and schedule_entry.group.guard_type == "guard_55")
            is_guard_duty = (not schedule_entry and has_guard_slot and has_guard_slot.group
                             and has_guard_slot.group.guard_type == "guard")
            is_permanencia = (schedule_entry and schedule_entry.subject
                              and schedule_entry.subject.guard_type == "permanencia")

            # Sin grupo que cubrir: no tiene sentido pedir tareas para el tramo
            # (permanencia sin docencia, guardia GUA-2 o guardia oficial GUARD).
            if not (is_guard_55 or is_guard_duty or is_permanencia):
                task_ids.append(absence.id)

            # Guardia de mayores de 55 (grupo GUA-2) o tramo de guardia oficial
            # (grupo GUARD): se registra la ausencia pero no hay nada que
            # cubrir ni penalización, porque el profesor no tenía clase
            # asignada en ese tramo.
            if is_guard_55 or is_guard_duty:
                continue

            db.session.add(Guard(
                absence_id=absence.id,
                date=absence_date,
                slot_id=slot_id,
                group_id=group_id,
                status="pending",
            ))

            if has_guard_slot:
                # Sin penalización si el profe está de actividad extraescolar ese día/tramo
                on_activities = (
                    ExtraActivityTeacher.query
                    .join(ExtraActivity, ExtraActivity.id == ExtraActivityTeacher.activity_id)
                    .filter(
                        ExtraActivityTeacher.teacher_id == teacher_id,
                        ExtraActivity.date == absence_date,
                    ).all()
                )
                is_on_activity = any(slot_id in eat.activity.slot_id_list
                                     for eat in on_activities)
                if not is_on_activity:
                    absence.penalty_points = configured_penalty
                    apply_absence_penalty(teacher_id, configured_penalty)

            normal_slot_ids.append(slot_id)

        db.session.commit()
        if task_ids and current_user.role != "display":
            session["task_prompt_ids"] = task_ids

        # Auto-asignación solo en tramos normales (no recreo) y si está habilitada
        if auto_assign_guards_enabled():
            for slot_id in normal_slot_ids:
                auto_assign_pending_guards(absence_date, slot_id)

        if skipped_past:
            flash(f"Tramos omitidos (ya han finalizado): {', '.join(skipped_past)}.", "warning")
        if skipped_duplicate:
            flash(f"Ausencia ya existente para: {', '.join(skipped_duplicate)}.", "warning")
        if skipped_free:
            flash(f"Tramos omitidos (el profesor no tiene clase ni guardia asignada): {', '.join(skipped_free)}.", "warning")
        if created_ids:
            flash("Ausencia registrada correctamente.", "success")
            return redirect(url_for("absences.index"))
        return redirect(url_for("absences.create"))

    year = get_current_school_year()
    return render_template("absences/create.html", teachers=teachers, slots=slots,
                           today=date.today().isoformat(), current_year=year)


@absences_bp.route("/horario-json/<int:tid>/<int:day_idx>")
@login_required
def schedule_json(tid, day_idx):
    """Devuelve los tramos que tiene el profesor en ese día de la semana."""
    if tid != current_user.id and not current_user.is_management and current_user.role != "display":
        abort(403)
    slots_cfg = {s["id"]: s for s in current_app.config["TIME_SLOTS"]}
    year_id = get_current_school_year().id
    entries = TeacherSchedule.query.filter_by(
        teacher_id=tid,
        day_of_week=day_idx,
        is_guard_slot=False,
        school_year_id=year_id,
    ).all()
    result = []
    for e in entries:
        s = slots_cfg.get(e.slot_id)
        if s:
            result.append({"id": s["id"], "label": s["label"],
                           "start": s["start"], "end": s["end"]})
    result.sort(key=lambda x: x["id"])
    return jsonify(result)



def _pdf_response(buf, filename):
    """Construye la respuesta de un PDF generado, inline si se pide ?inline=1
    (para verlo en una ventana emergente) o como descarga en caso contrario."""
    from flask import make_response
    response = make_response(buf.getvalue())
    response.headers["Content-Type"] = "application/pdf"
    disposition = "inline" if request.args.get("inline") else "attachment"
    response.headers["Content-Disposition"] = f"{disposition}; filename={filename}"
    return response


def _append_absence_pdf_page(writer, absence, slot_label, group_name, room_name, upload_dir, no_tasks_msg):
    """Genera la página de tareas de una ausencia y la añade (con sus adjuntos PDF) al writer."""
    import io
    from fpdf import FPDF
    from pypdf import PdfReader

    FONT   = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    tasks = absence.tasks.all()

    pdf = FPDF()
    pdf.add_font("dv", "", FONT)
    pdf.add_font("dv", "B", FONT_B)
    pdf.add_page()

    pdf.set_font("dv", "B", 16)
    pdf.cell(0, 10, _get_institute_name(), ln=True, align="C")
    pdf.set_font("dv", "", 11)
    pdf.cell(0, 7, "Tareas para guardia", ln=True, align="C")
    pdf.ln(4)
    pdf.set_draw_color(180, 180, 180)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)

    for label, value in [
        ("Fecha:",      absence.date.strftime("%d/%m/%Y")),
        ("Tramo:",      slot_label),
        ("Profesor/a:", absence.teacher.full_name),
        ("Grupo:",      group_name),
        ("Aula:",       room_name),
    ]:
        pdf.set_font("dv", "B", 11)
        pdf.cell(42, 7, label)
        pdf.set_font("dv", "", 11)
        pdf.cell(0, 7, value, ln=True)

    pdf.ln(4)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)
    pdf.set_font("dv", "B", 12)
    pdf.cell(0, 8, "Tareas:", ln=True)
    pdf.set_font("dv", "", 11)

    if tasks:
        for i, task in enumerate(tasks, 1):
            suffix = " [adjunto PDF]" if task.attachment else ""
            pdf.multi_cell(0, 7, f"{i}. {task.description}{suffix}")
            pdf.ln(1)
    else:
        pdf.cell(0, 7, no_tasks_msg, ln=True)

    writer.append(PdfReader(io.BytesIO(bytes(pdf.output()))))
    for task in tasks:
        if task.attachment:
            path = os.path.join(upload_dir, task.attachment)
            if os.path.exists(path):
                writer.append(PdfReader(path))


def _save_task_pdf(file):
    """Guarda el PDF adjunto en uploads/tasks/ y devuelve el nombre de fichero almacenado."""
    import uuid
    from werkzeug.utils import secure_filename
    from flask import current_app
    upload_dir = os.path.join(current_app.root_path, '..', 'uploads', 'tasks')
    os.makedirs(upload_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
    file.save(os.path.join(upload_dir, filename))
    return filename


@absences_bp.route("/tarea/<int:task_id>/adjunto")
@login_required
def task_attachment(task_id):
    """Descarga el PDF adjunto a una tarea."""
    from flask import send_from_directory
    task = Task.query.get_or_404(task_id)
    if not task.attachment:
        from flask import abort
        abort(404)
    upload_dir = os.path.join(current_app.root_path, '..', 'uploads', 'tasks')
    return send_from_directory(upload_dir, task.attachment,
                               download_name=task.attachment.split('_', 1)[-1],
                               as_attachment=False)


@absences_bp.route("/<int:absence_id>/tareas", methods=["GET", "POST"])
@login_required
def tasks(absence_id):
    absence = Absence.query.get_or_404(absence_id)
    can_access = (absence.teacher_id == current_user.id or current_user.is_management)
    if not can_access:
        _yid = get_current_school_year().id
        has_guard = TeacherSchedule.query.filter_by(
            teacher_id=current_user.id,
            day_of_week=absence.date.weekday(),
            slot_id=absence.slot_id,
            is_guard_slot=True,
            school_year_id=_yid,
        ).first()
        can_access = bool(has_guard)
    if not can_access:
        flash("No tienes acceso a esta ausencia.", "danger")
        return redirect(url_for("absences.index"))

    _yid = get_current_school_year().id
    from app.utils.school_year import get_year_groups
    groups = get_year_groups(_yid)
    schedule_entry = TeacherSchedule.query.filter_by(
        teacher_id=absence.teacher_id,
        day_of_week=absence.date.weekday(),
        slot_id=absence.slot_id,
        is_guard_slot=False,
        school_year_id=_yid,
    ).first()
    default_group_id = schedule_entry.group_id if schedule_entry else None

    if request.method == "POST":
        group_id = int(request.form["group_id"])
        description = request.form["description"]
        task = Task(absence_id=absence.id, group_id=group_id, description=description)

        file = request.files.get("attachment")
        if file and file.filename.lower().endswith(".pdf"):
            task.attachment = _save_task_pdf(file)
        elif file and file.filename:
            flash("Solo se permiten archivos PDF.", "warning")

        db.session.add(task)
        db.session.commit()
        flash("Tarea añadida.", "success")
        return redirect(url_for("absences.tasks", absence_id=absence.id))

    return render_template("absences/tasks.html", absence=absence, groups=groups,
                           default_group_id=default_group_id)


@absences_bp.route("/tarea/<int:task_id>/editar", methods=["POST"])
@login_required
def edit_task(task_id):
    task = Task.query.get_or_404(task_id)
    absence = task.absence
    if absence.teacher_id != current_user.id and not current_user.is_management:
        flash("No tienes acceso.", "danger")
        return redirect(url_for("absences.index"))

    task.group_id = int(request.form["group_id"])
    task.description = request.form["description"]

    file = request.files.get("attachment")
    if file and file.filename.lower().endswith(".pdf"):
        # Reemplaza el adjunto anterior si existía
        if task.attachment:
            import os
            old_path = os.path.join(current_app.root_path, '..', 'uploads', 'tasks', task.attachment)
            if os.path.exists(old_path):
                os.remove(old_path)
        task.attachment = _save_task_pdf(file)
    elif file and file.filename:
        flash("Solo se permiten archivos PDF.", "warning")

    db.session.commit()
    flash("Tarea actualizada.", "success")
    return redirect(url_for("absences.tasks", absence_id=absence.id))


@absences_bp.route("/tarea/<int:task_id>/eliminar-adjunto", methods=["POST"])
@login_required
def delete_task_attachment(task_id):
    task = Task.query.get_or_404(task_id)
    absence = task.absence
    if absence.teacher_id != current_user.id and not current_user.is_management:
        flash("No tienes acceso.", "danger")
        return redirect(url_for("absences.index"))

    if task.attachment:
        path = os.path.join(current_app.root_path, '..', 'uploads', 'tasks', task.attachment)
        if os.path.exists(path):
            os.remove(path)
        task.attachment = None
        db.session.commit()
        flash("Adjunto eliminado.", "success")
    return redirect(url_for("absences.tasks", absence_id=absence.id))


@absences_bp.route("/tarea/<int:task_id>/eliminar", methods=["POST"])
@login_required
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    absence = task.absence
    if absence.teacher_id != current_user.id and not current_user.is_management:
        flash("No tienes acceso.", "danger")
        return redirect(url_for("absences.index"))

    if task.attachment:
        path = os.path.join(current_app.root_path, '..', 'uploads', 'tasks', task.attachment)
        if os.path.exists(path):
            os.remove(path)
    db.session.delete(task)
    db.session.commit()
    flash("Tarea eliminada.", "success")
    return redirect(url_for("absences.tasks", absence_id=absence.id))


@absences_bp.route("/<int:absence_id>/tareas/imprimir")
@login_required
def tasks_pdf(absence_id):
    absence = Absence.query.get_or_404(absence_id)
    tasks = absence.tasks.all()

    slots = current_app.config["TIME_SLOTS"]
    slot = next((s for s in slots if s["id"] == absence.slot_id), None)
    slot_label = f"{slot['label']} ({slot['start']}-{slot['end']})" if slot else str(absence.slot_id)

    schedule_entry = TeacherSchedule.query.filter_by(
        teacher_id=absence.teacher_id,
        day_of_week=absence.date.weekday(),
        slot_id=absence.slot_id,
        is_guard_slot=False,
        school_year_id=get_current_school_year().id,
    ).first()
    group = schedule_entry.group if schedule_entry else None

    return render_template(
        "absences/print_tasks.html",
        absence=absence,
        tasks=tasks,
        slot_label=slot_label,
        group_name=group.name if group else "—",
        room_name=schedule_entry.room.name if schedule_entry and schedule_entry.room else "—",
        institute_name=_get_institute_name(),
        has_attachments=any(t.attachment for t in tasks),
    )


@absences_bp.route("/imprimir-tramo/<date_str>/<int:slot_id>")
@login_required
def slot_pdf(date_str, slot_id):
    from datetime import date as date_type

    target_date = date_type.fromisoformat(date_str)
    absences = Absence.query.filter_by(date=target_date, slot_id=slot_id).order_by(Absence.id).all()
    if not absences:
        flash("No hay ausencias en este tramo.", "warning")
        return redirect(url_for("dashboard.index"))

    slots = current_app.config["TIME_SLOTS"]
    slot = next((s for s in slots if s["id"] == slot_id), None)
    slot_label = f"{slot['label']} ({slot['start']}-{slot['end']})" if slot else str(slot_id)

    entries = []
    for absence in absences:
        tasks = absence.tasks.all()
        entry = TeacherSchedule.query.filter_by(
            teacher_id=absence.teacher_id,
            day_of_week=target_date.weekday(),
            slot_id=slot_id,
            is_guard_slot=False,
            school_year_id=get_current_school_year().id,
        ).first()
        group = entry.group if entry else None
        entries.append({
            "teacher_name": absence.teacher.full_name,
            "group_name": group.name if group else "—",
            "room_name": entry.room.name if entry and entry.room else "—",
            "tasks": tasks,
        })

    return render_template(
        "absences/print_slot.html",
        target_date=target_date,
        slot_label=slot_label,
        entries=entries,
        institute_name=_get_institute_name(),
        has_attachments=any(t.attachment for item in entries for t in item["tasks"]),
        date_str=date_str,
        slot_id=slot_id,
    )


@absences_bp.route("/<int:absence_id>/tareas/descargar")
@login_required
def tasks_download(absence_id):
    """Descarga PDF fusionado con adjuntos (solo cuando hay archivos adjuntos)."""
    import io
    from pypdf import PdfWriter

    absence = Absence.query.get_or_404(absence_id)

    slots = current_app.config["TIME_SLOTS"]
    slot = next((s for s in slots if s["id"] == absence.slot_id), None)
    slot_label = f"{slot['label']} ({slot['start']}-{slot['end']})" if slot else str(absence.slot_id)

    schedule_entry = TeacherSchedule.query.filter_by(
        teacher_id=absence.teacher_id,
        day_of_week=absence.date.weekday(),
        slot_id=absence.slot_id,
        is_guard_slot=False,
        school_year_id=get_current_school_year().id,
    ).first()
    group = schedule_entry.group if schedule_entry else None
    group_name = group.name if group else "-"
    room_name = schedule_entry.room.name if schedule_entry and schedule_entry.room else "-"

    upload_dir = os.path.join(current_app.root_path, '..', 'uploads', 'tasks')
    writer = PdfWriter()
    _append_absence_pdf_page(writer, absence, slot_label, group_name, room_name, upload_dir, "Sin tareas registradas.")

    buf = io.BytesIO()
    writer.write(buf)
    filename = f"tareas_{absence.date.isoformat()}_tramo{absence.slot_id}.pdf"
    return _pdf_response(buf, filename)


@absences_bp.route("/descargar-tramo/<date_str>/<int:slot_id>")
@login_required
def slot_download(date_str, slot_id):
    """Descarga PDF fusionado de todas las ausencias del tramo, con adjuntos."""
    import io
    from pypdf import PdfWriter
    from datetime import date as date_type

    target_date = date_type.fromisoformat(date_str)
    absences = Absence.query.filter_by(date=target_date, slot_id=slot_id).order_by(Absence.id).all()
    if not absences:
        flash("No hay ausencias en este tramo.", "warning")
        return redirect(url_for("dashboard.index"))

    slots = current_app.config["TIME_SLOTS"]
    slot = next((s for s in slots if s["id"] == slot_id), None)
    slot_label = f"{slot['label']} ({slot['start']}-{slot['end']})" if slot else str(slot_id)

    upload_dir = os.path.join(current_app.root_path, '..', 'uploads', 'tasks')
    writer = PdfWriter()

    for absence in absences:
        entry = TeacherSchedule.query.filter_by(
            teacher_id=absence.teacher_id,
            day_of_week=target_date.weekday(),
            slot_id=slot_id,
            is_guard_slot=False,
            school_year_id=get_current_school_year().id,
        ).first()
        group = entry.group if entry else None
        group_name = group.name if group else "-"
        room_name = entry.room.name if entry and entry.room else "-"
        _append_absence_pdf_page(writer, absence, slot_label, group_name, room_name, upload_dir, "El profesor/a no ha dejado tareas.")

    buf = io.BytesIO()
    writer.write(buf)
    filename = f"tareas_{date_str}_tramo{slot_id}.pdf"
    return _pdf_response(buf, filename)


@absences_bp.route("/descargar-dia/<date_str>")
@login_required
def day_download(date_str):
    """Descarga PDF fusionado de todas las ausencias del día (todos los tramos), con adjuntos."""
    import io
    from pypdf import PdfWriter
    from datetime import date as date_type

    target_date = date_type.fromisoformat(date_str)
    absences = Absence.query.filter_by(date=target_date).order_by(Absence.slot_id, Absence.id).all()
    if not absences:
        flash("No hay ausencias registradas para ese día.", "warning")
        return redirect(url_for("dashboard.index"))

    slots_cfg = {s["id"]: s for s in current_app.config["TIME_SLOTS"]}
    upload_dir = os.path.join(current_app.root_path, '..', 'uploads', 'tasks')
    writer = PdfWriter()

    for absence in absences:
        slot = slots_cfg.get(absence.slot_id)
        slot_label = f"{slot['label']} ({slot['start']}-{slot['end']})" if slot else str(absence.slot_id)
        entry = TeacherSchedule.query.filter_by(
            teacher_id=absence.teacher_id,
            day_of_week=target_date.weekday(),
            slot_id=absence.slot_id,
            is_guard_slot=False,
            school_year_id=get_current_school_year().id,
        ).first()
        group = entry.group if entry else None
        group_name = group.name if group else "-"
        room_name = entry.room.name if entry and entry.room else "-"
        _append_absence_pdf_page(writer, absence, slot_label, group_name, room_name, upload_dir, "El profesor/a no ha dejado tareas.")

    buf = io.BytesIO()
    writer.write(buf)
    filename = f"tareas_{date_str}.pdf"
    return _pdf_response(buf, filename)


@absences_bp.route("/<int:absence_id>/reincorporar", methods=["POST"])
@login_required
def mark_returned(absence_id):
    from app.models.schedule import TeacherSchedule
    from datetime import date as _date

    absence = Absence.query.get_or_404(absence_id)

    def _redirect_back():
        back = request.form.get("back", "dashboard")
        if back == "my_guard":
            return redirect(url_for("guards.my_guard") + f"#slot-{absence.slot_id}")
        if back == "display":
            return redirect(url_for("display.index"))
        if back == "absences":
            return redirect(url_for("absences.index"))
        fecha = request.form.get("back_fecha") or absence.date.isoformat()
        slot  = request.form.get("back_slot") or absence.slot_id
        return redirect(url_for("dashboard.index", fecha=fecha) + f"#slot-{slot}")

    # Nunca se puede reincorporar un tramo ya pasado
    today = _date.today()
    if absence.date < today:
        flash("No se puede registrar una reincorporación en un tramo pasado.", "danger")
        return _redirect_back()
    if absence.date == today:
        slots_cfg = current_app.config["TIME_SLOTS"]
        slot = next((s for s in slots_cfg if s["id"] == absence.slot_id), None)
        if slot:
            try:
                slot_end = datetime.strptime(slot["end"], "%H:%M").time()
                if datetime.now().time() >= slot_end:
                    flash("No se puede registrar una reincorporación en un tramo pasado.", "danger")
                    return _redirect_back()
            except (KeyError, ValueError):
                pass

    # Permitido a: directivos, pantalla, y profesor con guardia en ese tramo hoy
    if not current_user.is_management and current_user.role != "display":
        has_guard = TeacherSchedule.query.filter_by(
            teacher_id=current_user.id,
            day_of_week=_date.today().weekday(),
            slot_id=absence.slot_id,
            is_guard_slot=True,
            school_year_id=get_current_school_year().id,
        ).first()
        if not has_guard:
            flash("Sin permiso.", "danger")
            return _redirect_back()

    absence.status = "returned"
    absence.returned_at = datetime.now()
    if absence.guard:
        absence.guard.status = "returned"

    # La reincorporación afecta a todos los tramos siguientes del mismo día
    later_absences = Absence.query.filter(
        Absence.teacher_id == absence.teacher_id,
        Absence.date == absence.date,
        Absence.slot_id > absence.slot_id,
        Absence.status != "returned",
    ).all()
    for later in later_absences:
        later.status = "returned"
        later.returned_at = absence.returned_at
        if later.guard:
            later.guard.status = "returned"

    db.session.commit()
    flash("Reincorporación registrada.", "success")

    return _redirect_back()


@absences_bp.route("/eliminar", methods=["GET"])
@login_required
def delete_search():
    from app.routes.admin import _require_developer
    if not _require_developer():
        return redirect(url_for("dashboard.index"))
    year = get_current_school_year()
    teachers = (User.query.filter_by(active=True)
                .filter(User.school_year_id == year.id)
                .order_by(User.surname).all())
    return render_template("absences/delete.html", teachers=teachers)


@absences_bp.route("/eliminar/buscar")
@login_required
def delete_search_json():
    from app.routes.admin import _require_developer
    if not _require_developer():
        return jsonify([]), 403

    slots_cfg = {s["id"]: s for s in current_app.config["TIME_SLOTS"]}
    year_id = get_current_school_year().id

    teacher_id = request.args.get("teacher_id", type=int)
    fecha_str = request.args.get("fecha", "").strip()

    if not teacher_id and not fecha_str:
        return jsonify([])

    q = Absence.query.join(User, Absence.teacher_id == User.id).filter(
        User.school_year_id == year_id
    )
    if teacher_id:
        q = q.filter(Absence.teacher_id == teacher_id)
    if fecha_str:
        try:
            from datetime import date as _date
            q = q.filter(Absence.date == _date.fromisoformat(fecha_str))
        except ValueError:
            return jsonify([])

    absences = q.order_by(Absence.date.desc(), Absence.slot_id).limit(200).all()

    result = []
    for a in absences:
        slot = slots_cfg.get(a.slot_id, {})
        result.append({
            "id": a.id,
            "date": a.date.strftime("%d/%m/%Y"),
            "date_iso": a.date.isoformat(),
            "slot_label": slot.get("label", f"Tramo {a.slot_id}"),
            "slot_time": f"{slot.get('start','')}-{slot.get('end','')}",
            "teacher": a.teacher.full_name,
            "status": a.status,
            "reason": a.reason or "",
            "has_guard": a.guard is not None,
        })
    return jsonify(result)


@absences_bp.route("/eliminar", methods=["POST"])
@login_required
def delete_bulk():
    from app.routes.admin import _require_developer, _delete_task_attachments
    if not _require_developer():
        return jsonify({"ok": False, "error": "Sin acceso"}), 403

    from app.models.guard import Guard, GuardRecord
    from app.utils.points import points_system_enabled

    data = request.get_json(silent=True) or {}
    ids = [int(i) for i in data.get("ids", []) if str(i).isdigit()]
    if not ids:
        return jsonify({"ok": False, "error": "Sin selección"}), 400

    deleted = 0
    task_attachments = []
    for aid in ids:
        absence = Absence.query.get(aid)
        if not absence:
            continue

        if points_system_enabled():
            teacher = User.query.get(absence.teacher_id)
            if teacher and teacher.role not in ("management", "display"):
                teacher.points = round(teacher.points - absence.penalty_points, 2)

        task_attachments += [
            t.attachment for t in Task.query.filter_by(absence_id=aid)
            .with_entities(Task.attachment).all()
        ]
        Task.query.filter_by(absence_id=aid).delete(synchronize_session=False)

        guards = Guard.query.filter_by(absence_id=aid).all()
        for guard in guards:
            if points_system_enabled():
                for rec in guard.records.all():
                    covering = User.query.get(rec.teacher_id)
                    if covering and covering.role not in ("management", "display"):
                        covering.points = round(covering.points - rec.points_awarded, 2)
            GuardRecord.query.filter_by(guard_id=guard.id).delete(synchronize_session=False)
        Guard.query.filter_by(absence_id=aid).delete(synchronize_session=False)

        db.session.delete(absence)
        deleted += 1

    db.session.commit()
    _delete_task_attachments(task_attachments)

    msg = "Ausencia eliminada" if deleted == 1 else f"{deleted} ausencias eliminadas"
    return jsonify({"ok": True, "deleted": deleted, "message": msg})


@absences_bp.route("/<int:absence_id>/comentario", methods=["POST"])
@login_required
def edit_comment(absence_id):
    """Edición rápida del motivo/comentario de una ausencia (AJAX, solo management/display)."""
    from flask import jsonify
    if current_user.role not in ("management", "display"):
        return jsonify({"ok": False}), 403
    absence = Absence.query.get_or_404(absence_id)
    data = request.get_json(silent=True) or {}
    absence.reason = data.get("text", "").strip() or None
    db.session.commit()
    return jsonify({"ok": True})


@absences_bp.route("/<int:absence_id>/deshacer-reincorporacion", methods=["POST"])
@login_required
def unmark_returned(absence_id):
    from datetime import datetime as _dt

    absence = Absence.query.get_or_404(absence_id)

    today = date.today()
    # Fecha pasada: nunca se puede deshacer
    if absence.date < today:
        flash("No se puede deshacer una reincorporación de un día pasado.", "danger")
        return redirect(url_for("dashboard.index", fecha=absence.date.isoformat()))

    # Hoy: solo dentro del tramo horario
    if absence.date == today:
        slots_cfg = current_app.config["TIME_SLOTS"]
        slot = next((s for s in slots_cfg if s["id"] == absence.slot_id), None)
        now_t = _dt.now().time()
        if slot:
            try:
                slot_start = _dt.strptime(slot["start"], "%H:%M").time()
                slot_end   = _dt.strptime(slot["end"],   "%H:%M").time()
                if not (slot_start <= now_t <= slot_end):
                    flash("Solo se puede deshacer la reincorporación dentro del tramo horario.", "danger")
                    return redirect(url_for("dashboard.index", fecha=absence.date.isoformat()))
            except (KeyError, ValueError):
                pass
    # Fecha futura: siempre permitido (el tramo aún no ha ocurrido)

    # Permitido a: directivos, pantalla, y profesor con guardia en ese tramo
    if not current_user.is_management and current_user.role != "display":
        has_guard = TeacherSchedule.query.filter_by(
            teacher_id=current_user.id,
            day_of_week=today.weekday(),
            slot_id=absence.slot_id,
            is_guard_slot=True,
            school_year_id=get_current_school_year().id,
        ).first()
        if not has_guard:
            flash("Sin permiso.", "danger")
            return redirect(url_for("dashboard.index", fecha=absence.date.isoformat()))

    absence.status = "pending"
    absence.returned_at = None
    if absence.guard and absence.guard.status == "returned":
        for rec in absence.guard.records.all():
            teacher = db.session.get(User, rec.teacher_id)
            if teacher:
                teacher.points = round(teacher.points - rec.points_awarded, 2)
            db.session.delete(rec)
        absence.guard.status = "pending"
    db.session.commit()
    flash("Reincorporación deshecha. El profesor figura de nuevo como ausente.", "warning")

    back = request.form.get("back", "dashboard")
    if back == "absences":
        return redirect(url_for("absences.index"))
    fecha = request.form.get("back_fecha") or absence.date.isoformat()
    slot  = request.form.get("back_slot") or absence.slot_id
    return redirect(url_for("dashboard.index", fecha=fecha) + f"#slot-{slot}")
