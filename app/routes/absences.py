"""
Blueprint de ausencias. Permite registrar ausencias de profesores, añadir tareas
para el grupo que queda sin clase, marcar reincorporaciones y generar PDFs
(por ausencia individual o por tramo horario completo con grupos y aulas).
"""
import os
from datetime import date, datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify
from flask_login import login_required, current_user
from app.extensions import db
from app.models.absence import Absence
from app.models.guard import Guard
from app.models.task import Task
from app.models.user import User
from app.models.group import Group
from app.models.schedule import TeacherSchedule
from app.utils.points import apply_absence_penalty
from app.utils.guards import auto_assign_pending_guards

absences_bp = Blueprint("absences", __name__, url_prefix="/ausencias")


@absences_bp.route("/")
@login_required
def index():
    from collections import defaultdict
    if current_user.is_management:
        absences = Absence.query.order_by(Absence.date.desc(), Absence.slot_id).all()
    else:
        absences = Absence.query.filter_by(teacher_id=current_user.id).order_by(Absence.date.desc(), Absence.slot_id).all()

    slots_cfg = current_app.config["TIME_SLOTS"]
    slot_map = {s["id"]: s for s in slots_cfg}

    # Agrupar por fecha → lista de (slot_cfg, [absences])
    by_date = defaultdict(lambda: defaultdict(list))
    for a in absences:
        by_date[a.date][a.slot_id].append(a)

    # Ordenar: fechas desc, slots asc
    grouped = []
    for d in sorted(by_date.keys(), reverse=True):
        slots_in_day = []
        for sid in sorted(by_date[d].keys()):
            slots_in_day.append({
                "slot": slot_map.get(sid),
                "absences": by_date[d][sid],
            })
        grouped.append({"date": d, "slots": slots_in_day})

    return render_template("absences/index.html", grouped=grouped, slots=slots_cfg)


@absences_bp.route("/nueva", methods=["GET", "POST"])
@login_required
def create():
    teachers = User.query.filter_by(active=True).order_by(User.surname).all()
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

        day_idx = absence_date.weekday()
        configured_penalty = current_app.config.get("ABSENCE_PENALTY", -1.0)
        slots_cfg = {s["id"]: s for s in current_app.config["TIME_SLOTS"]}
        skipped_free = []
        normal_slot_ids = []  # tramos normales para auto-asignar después

        for slot_id in slot_ids:
            slot_id = int(slot_id)
            slot_cfg = slots_cfg.get(slot_id, {})
            is_break = slot_cfg.get("is_break", False)

            existing = Absence.query.filter_by(
                teacher_id=teacher_id, date=absence_date, slot_id=slot_id
            ).first()
            if existing:
                continue

            if is_break:
                # Recreo: solo registrar si el profesor tenía recreo asignado en su horario
                has_break_slot = TeacherSchedule.query.filter_by(
                    teacher_id=teacher_id,
                    day_of_week=day_idx,
                    slot_id=slot_id,
                ).first()
                if not has_break_slot:
                    skipped_free.append(slot_cfg.get("label", f"Tramo {slot_id}"))
                    continue
                db.session.add(Absence(
                    teacher_id=teacher_id,
                    date=absence_date,
                    slot_id=slot_id,
                    reason=reason,
                    reported_by_role="self" if teacher_id == current_user.id else "management",
                    reported_by_id=current_user.id,
                    penalty_points=0.0,
                ))
                continue

            # Tramo normal: verificar que el profesor tiene algo asignado
            schedule_entry = TeacherSchedule.query.filter_by(
                teacher_id=teacher_id,
                day_of_week=day_idx,
                slot_id=slot_id,
                is_guard_slot=False,
            ).first()
            has_guard_slot = TeacherSchedule.query.filter_by(
                teacher_id=teacher_id,
                day_of_week=day_idx,
                slot_id=slot_id,
                is_guard_slot=True,
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

            db.session.add(Guard(
                absence_id=absence.id,
                date=absence_date,
                slot_id=slot_id,
                group_id=group_id,
                status="pending",
            ))

            if has_guard_slot:
                absence.penalty_points = configured_penalty
                apply_absence_penalty(teacher_id, configured_penalty)

            normal_slot_ids.append(slot_id)

        db.session.commit()

        # Auto-asignación solo en tramos normales (no recreo)
        for slot_id in normal_slot_ids:
            auto_assign_pending_guards(absence_date, slot_id)

        if skipped_free:
            flash(f"Tramos omitidos (el profesor no tiene clase ni guardia asignada): {', '.join(skipped_free)}.", "warning")
        flash("Ausencia registrada correctamente.", "success")
        return redirect(url_for("absences.index"))

    return render_template("absences/create.html", teachers=teachers, slots=slots,
                           today=date.today().isoformat())


@absences_bp.route("/horario-json/<int:tid>/<int:day_idx>")
@login_required
def schedule_json(tid, day_idx):
    """Devuelve los tramos que tiene el profesor en ese día de la semana."""
    slots_cfg = {s["id"]: s for s in current_app.config["TIME_SLOTS"]}
    entries = TeacherSchedule.query.filter_by(
        teacher_id=tid,
        day_of_week=day_idx,
        is_guard_slot=False,
    ).all()
    result = []
    for e in entries:
        s = slots_cfg.get(e.slot_id)
        if s:
            result.append({"id": s["id"], "label": s["label"],
                           "start": s["start"], "end": s["end"]})
    result.sort(key=lambda x: x["id"])
    return jsonify(result)


def _append_attachments(main_bytes, tasks, upload_dir):
    """Añade las páginas de los PDFs adjuntos a las tareas al PDF principal."""
    import io
    from pypdf import PdfWriter, PdfReader

    attachments = [t.attachment for t in tasks if t.attachment]
    if not attachments:
        return main_bytes

    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(main_bytes)))
    for filename in attachments:
        path = os.path.join(upload_dir, filename)
        if os.path.exists(path):
            writer.append(PdfReader(path))

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


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
    if absence.teacher_id != current_user.id and not current_user.is_management:
        flash("No tienes acceso a esta ausencia.", "danger")
        return redirect(url_for("absences.index"))

    groups = Group.query.filter_by(active=True).order_by(Group.name).all()

    schedule_entry = TeacherSchedule.query.filter_by(
        teacher_id=absence.teacher_id,
        day_of_week=absence.date.weekday(),
        slot_id=absence.slot_id,
        is_guard_slot=False,
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


@absences_bp.route("/<int:absence_id>/tareas/pdf")
@login_required
def tasks_pdf(absence_id):
    from fpdf import FPDF
    from flask import make_response

    absence = Absence.query.get_or_404(absence_id)
    tasks = absence.tasks.all()

    slots = current_app.config["TIME_SLOTS"]
    slot = next((s for s in slots if s["id"] == absence.slot_id), None)
    slot_label = f"{slot['label']} ({slot['start']}-{slot['end']})" if slot else str(absence.slot_id)

    # Grupo del profesor en ese tramo
    from app.models.schedule import TeacherSchedule
    schedule_entry = TeacherSchedule.query.filter_by(
        teacher_id=absence.teacher_id,
        day_of_week=absence.date.weekday(),
        slot_id=absence.slot_id,
        is_guard_slot=False,
    ).first()
    group = schedule_entry.group if schedule_entry else None
    group_name = group.name if group else "-"
    room_name = schedule_entry.room.name if schedule_entry and schedule_entry.room else "-"

    FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    pdf = FPDF()
    pdf.add_font("dv", "", FONT)
    pdf.add_font("dv", "B", FONT_B)
    pdf.add_page()

    pdf.set_font("dv", "B", 16)
    pdf.cell(0, 10, current_app.config["INSTITUTE_NAME"], ln=True, align="C")
    pdf.set_font("dv", "", 11)
    pdf.cell(0, 7, "Tareas para guardia", ln=True, align="C")
    pdf.ln(5)

    pdf.set_font("dv", "B", 11)
    pdf.cell(40, 7, "Fecha:")
    pdf.set_font("dv", "", 11)
    pdf.cell(0, 7, absence.date.strftime("%d/%m/%Y"), ln=True)

    pdf.set_font("dv", "B", 11)
    pdf.cell(40, 7, "Tramo:")
    pdf.set_font("dv", "", 11)
    pdf.cell(0, 7, slot_label, ln=True)

    pdf.set_font("dv", "B", 11)
    pdf.cell(40, 7, "Profesor/a:")
    pdf.set_font("dv", "", 11)
    pdf.cell(0, 7, absence.teacher.full_name, ln=True)

    pdf.set_font("dv", "B", 11)
    pdf.cell(40, 7, "Grupo:")
    pdf.set_font("dv", "", 11)
    pdf.cell(0, 7, group_name, ln=True)

    pdf.set_font("dv", "B", 11)
    pdf.cell(40, 7, "Aula:")
    pdf.set_font("dv", "", 11)
    pdf.cell(0, 7, room_name, ln=True)

    pdf.ln(5)
    pdf.set_font("dv", "B", 12)
    pdf.cell(0, 8, "Tareas:", ln=True)
    pdf.set_font("dv", "", 11)

    if tasks:
        for i, task in enumerate(tasks, 1):
            suffix = " [adjunto PDF]" if task.attachment else ""
            pdf.multi_cell(0, 7, f"{i}. {task.description}{suffix}")
            pdf.ln(1)
    else:
        pdf.cell(0, 7, "Sin tareas registradas.", ln=True)

    upload_dir = os.path.join(current_app.root_path, '..', 'uploads', 'tasks')
    pdf_bytes = _append_attachments(bytes(pdf.output()), tasks, upload_dir)

    response = make_response(pdf_bytes)
    filename = f"tareas_{absence.date.isoformat()}_tramo{absence.slot_id}.pdf"
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


@absences_bp.route("/pdf-tramo/<date_str>/<int:slot_id>")
@login_required
def slot_pdf(date_str, slot_id):
    from fpdf import FPDF
    from flask import make_response
    from datetime import date as date_type
    from app.models.schedule import TeacherSchedule

    target_date = date_type.fromisoformat(date_str)
    absences = Absence.query.filter_by(date=target_date, slot_id=slot_id).order_by(Absence.id).all()
    if not absences:
        flash("No hay ausencias en este tramo.", "warning")
        return redirect(url_for("dashboard.index"))

    slots = current_app.config["TIME_SLOTS"]
    slot = next((s for s in slots if s["id"] == slot_id), None)
    slot_label = f"{slot['label']} ({slot['start']}-{slot['end']})" if slot else str(slot_id)

    import io
    from pypdf import PdfWriter, PdfReader

    FONT   = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    upload_dir = os.path.join(current_app.root_path, '..', 'uploads', 'tasks')

    writer = PdfWriter()

    for absence in absences:
        tasks = absence.tasks.all()
        entry = TeacherSchedule.query.filter_by(
            teacher_id=absence.teacher_id,
            day_of_week=target_date.weekday(),
            slot_id=slot_id,
            is_guard_slot=False,
        ).first()
        group = entry.group if entry else None
        group_name = group.name if group else "-"
        room_name = entry.room.name if entry and entry.room else "-"

        pdf = FPDF()
        pdf.add_font("dv", "",  FONT)
        pdf.add_font("dv", "B", FONT_B)
        pdf.add_page()

        # Cabecera
        pdf.set_font("dv", "B", 16)
        pdf.cell(0, 10, current_app.config["INSTITUTE_NAME"], ln=True, align="C")
        pdf.set_font("dv", "", 11)
        pdf.cell(0, 7, "Tareas para guardia", ln=True, align="C")
        pdf.ln(4)

        # Línea separadora
        pdf.set_draw_color(180, 180, 180)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(4)

        # Datos
        for label, value in [
            ("Fecha:",      target_date.strftime("%d/%m/%Y")),
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
            pdf.cell(0, 7, "El profesor/a no ha dejado tareas.", ln=True)

        # Añadir página(s) de esta ausencia al ensamblador
        writer.append(PdfReader(io.BytesIO(bytes(pdf.output()))))

        # Adjuntos de las tareas de esta ausencia, justo a continuación
        for task in tasks:
            if task.attachment:
                path = os.path.join(upload_dir, task.attachment)
                if os.path.exists(path):
                    writer.append(PdfReader(path))

    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()

    response = make_response(pdf_bytes)
    filename = f"tareas_{date_str}_tramo{slot_id}.pdf"
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


@absences_bp.route("/<int:absence_id>/reincorporar", methods=["POST"])
@login_required
def mark_returned(absence_id):
    from app.models.schedule import TeacherSchedule
    from datetime import date as _date

    absence = Absence.query.get_or_404(absence_id)

    # Permitido a: directivos, pantalla, y profesor con guardia en ese tramo hoy
    if not current_user.is_management and current_user.role != "display":
        has_guard = TeacherSchedule.query.filter_by(
            teacher_id=current_user.id,
            day_of_week=_date.today().weekday(),
            slot_id=absence.slot_id,
            is_guard_slot=True,
        ).first()
        if not has_guard:
            flash("Sin permiso.", "danger")
            return redirect(url_for("dashboard.index"))

    absence.status = "returned"
    if absence.guard:
        absence.guard.status = "returned"
    db.session.commit()
    flash("Reincorporación registrada.", "success")

    back = request.form.get("back", "dashboard")
    if back == "my_guard":
        return redirect(url_for("guards.my_guard") + f"#slot-{absence.slot_id}")
    if back == "display":
        return redirect(url_for("display.index"))
    return redirect(url_for("dashboard.index") + f"#slot-{absence.slot_id}")


@absences_bp.route("/<int:absence_id>/deshacer-reincorporacion", methods=["POST"])
@login_required
def unmark_returned(absence_id):
    absence = Absence.query.get_or_404(absence_id)

    if not current_user.is_management:
        flash("Sin permiso.", "danger")
        return redirect(url_for("dashboard.index"))

    absence.status = "absent"
    if absence.guard and absence.guard.status == "returned":
        for rec in absence.guard.records.all():
            teacher = db.session.get(User, rec.teacher_id)
            if teacher:
                teacher.points = round(teacher.points - rec.points_awarded, 2)
            db.session.delete(rec)
        absence.guard.status = "pending"
    db.session.commit()
    flash("Reincorporación deshecha. El profesor figura de nuevo como ausente.", "warning")
    return redirect(url_for("dashboard.index") + f"#slot-{absence.slot_id}")
