from datetime import date, datetime
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
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

            # Penalizar solo si el profesor tenía guardia asignada en ese tramo
            day_idx = absence_date.weekday()
            has_guard_slot = TeacherSchedule.query.filter_by(
                teacher_id=teacher_id,
                day_of_week=day_idx,
                slot_id=int(slot_id),
                is_guard_slot=True,
            ).first()
            if has_guard_slot:
                apply_absence_penalty(teacher_id)

        db.session.commit()

        # Auto-asignación de guardias generadas
        for slot_id in slot_ids:
            auto_assign_pending_guards(absence_date, int(slot_id))

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
    room_name = group.room.name if group and group.room else "-"

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
            pdf.multi_cell(0, 7, f"{i}. {task.description}")
            pdf.ln(1)
    else:
        pdf.cell(0, 7, "Sin tareas registradas.", ln=True)

    response = make_response(bytes(pdf.output()))
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

    FONT   = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    pdf = FPDF()
    pdf.add_font("dv", "",  FONT)
    pdf.add_font("dv", "B", FONT_B)

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
        room_name = group.room.name if group and group.room else "-"

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
                pdf.multi_cell(0, 7, f"{i}. {task.description}")
                pdf.ln(1)
        else:
            pdf.set_font("dv", "", 11)
            pdf.cell(0, 7, "El profesor/a no ha dejado tareas.", ln=True)

    response = make_response(bytes(pdf.output()))
    filename = f"tareas_{date_str}_tramo{slot_id}.pdf"
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


@absences_bp.route("/<int:absence_id>/reincorporar", methods=["POST"])
@login_required
def mark_returned(absence_id):
    absence = Absence.query.get_or_404(absence_id)
    absence.status = "returned"
    if absence.guard:
        absence.guard.status = "returned"
    db.session.commit()
    flash("Reincorporación registrada.", "success")
    return redirect(url_for("dashboard.index") + f"#slot-{absence.slot_id}")
