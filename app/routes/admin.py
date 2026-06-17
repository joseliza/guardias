"""
Blueprint de administración (solo rol management). Gestiona el CRUD de profesores,
grupos y aulas, la importación masiva de profesores y horarios por CSV, y el
informe resumen del carnet de puntos con enlace al historial detallado por profesor.
"""
import csv
import io
import os
import re
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from app.extensions import db, oauth
from app.models.user import User
from app.models.group import Group
from app.models.room import Room
from app.models.schedule import TeacherSchedule

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

PROTECTED_ADMIN_EMAIL = "admin@ies.es"


def _require_management():
    if not current_user.is_management:
        flash("Acceso restringido al equipo directivo.", "danger")
        return False
    return True


def _require_developer():
    if not (current_user.is_management and current_user.dev_access):
        flash("Acceso restringido.", "danger")
        return False
    return True


def _delete_task_attachments(filenames):
    """Borra del disco los PDFs adjuntos de tareas cuyas filas se eliminan en bloque."""
    upload_dir = os.path.join(current_app.root_path, "..", "uploads", "tasks")
    for filename in filenames:
        if not filename:
            continue
        path = os.path.join(upload_dir, filename)
        if os.path.exists(path):
            os.remove(path)


def _is_placeholder_email(email):
    """True si el email es un marcador interno (sin asignar / archivado)."""
    email = email or ""
    return email.startswith("_") and email.endswith("@pendiente.local")


def _transfer_email_to_active_year(teacher, exclude_ids=None):
    """Si el profesor a eliminar tiene un email real y existe otra fila suya
    (mismo nombre y apellidos) en otro curso con el email pendiente/archivado
    (_..._@pendiente.local), le transfiere el email -y la contraseña si no
    tiene una propia- antes del borrado, para no perderlo.

    Si entre las filas candidatas hay una del curso marcado como vigente
    (is_current), se prioriza esa; si no, la del curso más reciente.

    Devuelve la fila destino si se ha transferido algo, o None.
    """
    from app.models.school_year import SchoolYear

    if _is_placeholder_email(teacher.email) or not teacher.school_year_id:
        return None

    years = SchoolYear.query.all()
    starts = {y.id: y.start_date for y in years}
    current_year_id = next((y.id for y in years if y.is_current), None)
    if teacher.school_year_id not in starts:
        return None

    exclude_ids = set(exclude_ids or [])
    candidates = User.query.filter(
        User.id != teacher.id,
        User.role != "display",
        User.surname.ilike(teacher.surname),
        User.name.ilike(teacher.name),
    )
    if exclude_ids:
        candidates = candidates.filter(User.id.notin_(exclude_ids))

    pending = [u for u in candidates.all()
               if u.school_year_id and _is_placeholder_email(u.email)]
    if not pending:
        return None

    target = next((u for u in pending if u.school_year_id == current_year_id), None)
    if target is None:
        target = max(pending, key=lambda u: starts.get(u.school_year_id, starts[teacher.school_year_id]))

    email = teacher.email
    teacher.email = f"_deleted_{teacher.id}@pendiente.local"
    # Flush para liberar el email único antes de asignarlo a target (evita
    # IntegrityError si el UPDATE de target se ejecuta antes que este).
    db.session.flush()
    if not target.password_hash:
        target.password_hash = teacher.password_hash
    target.email = email
    target.receive_emails = True
    return target


# ── Profesores ───────────────────────────────────────────────────────────────

@admin_bp.route("/profesores")
@login_required
def teachers():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.utils.school_year import get_current_school_year
    current_year = get_current_school_year()
    from sqlalchemy import or_
    all_teachers = (User.query
                    .filter(or_(
                        User.school_year_id == current_year.id,
                        User.school_year_id.is_(None),
                        User.role == "display",
                    ))
                    .order_by(User.surname, User.name).all())
    all_emails_on = all(t.receive_emails for t in all_teachers)
    covered_by = {}
    for t in all_teachers:
        if t.substitutes_id:
            covered_by.setdefault(t.substitutes_id, []).append(t)
    return render_template("admin/teachers.html", teachers=all_teachers,
                           all_emails_on=all_emails_on, covered_by=covered_by,
                           current_year=current_year)


@admin_bp.route("/profesores/nuevo", methods=["GET", "POST"])
@login_required
def teacher_create():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        import secrets
        email = request.form["email"].strip().lower()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        if password != password_confirm:
            flash("Las contraseñas no coinciden.", "danger")
            return redirect(request.url)
        if User.query.filter_by(email=email).first():
            flash("Ya existe un usuario con ese correo electrónico.", "danger")
            return redirect(request.url)
        role = request.form.get("role", "teacher")
        from app.utils.school_year import get_current_school_year as _get_year
        user = User(
            email=email,
            name=request.form["name"].strip(),
            surname=request.form["surname"].strip(),
            abbreviation=request.form.get("abbreviation", "").strip() or None,
            role=role,
            track_points=request.form.get("track_points") == "on" and role == "management",
            dev_access=(current_user.dev_access and request.form.get("dev_access") == "on"
                        and role == "management"),
            receive_emails=True,
            school_year_id=_get_year().id,
        )
        sent_password = password
        if not password:
            password = secrets.token_hex(24)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        try:
            _send_welcome_email(user, sent_password or None)
        except Exception as e:
            current_app.logger.error("Error enviando bienvenida a %s: %s", user.email, e)
        flash("Profesor creado.", "success")
        return redirect(url_for("admin.teachers"))
    return render_template("admin/teacher_form.html", teacher=None, all_teachers=[])


@admin_bp.route("/profesores/<int:tid>/editar", methods=["GET", "POST"])
@login_required
def teacher_edit(tid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    teacher = User.query.get_or_404(tid)
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        if password != password_confirm:
            flash("Las contraseñas no coinciden.", "danger")
            return redirect(request.url)
        existing = User.query.filter_by(email=email).first()
        if existing and existing.id != teacher.id:
            flash("Ya existe un usuario con ese correo electrónico.", "danger")
            return redirect(request.url)
        is_protected_admin = teacher.email == PROTECTED_ADMIN_EMAIL
        teacher.name = request.form["name"].strip()
        teacher.surname = request.form["surname"].strip()
        if is_protected_admin:
            teacher.email = PROTECTED_ADMIN_EMAIL
        else:
            teacher.email = email
        teacher.abbreviation = request.form.get("abbreviation", "").strip() or None
        if is_protected_admin:
            teacher.role = "management"
            teacher.dev_access = True
        else:
            teacher.role = request.form.get("role", "teacher")
            teacher.active = request.form.get("active") == "on"
            teacher.track_points = request.form.get("track_points") == "on" and teacher.role == "management"
            if current_user.dev_access:
                teacher.dev_access = request.form.get("dev_access") == "on" and teacher.role == "management"
            elif teacher.role != "management":
                teacher.dev_access = False
        teacher.receive_emails = request.form.get("receive_emails") == "on"
        teacher.show_substitute_public = request.form.get("show_substitute_public") == "on"
        if password:
            teacher.set_password(password)
        # Gestión de sustitución
        from app.utils.school_year import get_current_school_year as _get_year
        _year_id = _get_year().id
        new_sub_id_raw = request.form.get("substitutes_id", "").strip()
        new_sub_id = int(new_sub_id_raw) if new_sub_id_raw else None
        old_sub_id = teacher.substitutes_id
        if new_sub_id != old_sub_id:
            if old_sub_id:
                old_original = User.query.get(old_sub_id)
                if old_original:
                    old_original.active = True
                TeacherSchedule.query.filter_by(teacher_id=teacher.id, school_year_id=_year_id).delete(synchronize_session=False)
            if new_sub_id and new_sub_id != teacher.id:
                new_original = User.query.get(new_sub_id)
                if new_original:
                    TeacherSchedule.query.filter_by(teacher_id=teacher.id, school_year_id=_year_id).delete(synchronize_session=False)
                    for entry in new_original.schedule_entries.filter_by(school_year_id=_year_id).all():
                        db.session.add(TeacherSchedule(
                            teacher_id=teacher.id,
                            group_id=entry.group_id,
                            day_of_week=entry.day_of_week,
                            slot_id=entry.slot_id,
                            is_guard_slot=entry.is_guard_slot,
                            room_id=entry.room_id,
                            notes=entry.notes,
                            school_year_id=_year_id,
                        ))
                    new_original.active = False
        teacher.substitutes_id = new_sub_id
        db.session.commit()
        flash("Profesor actualizado.", "success")
        return redirect(url_for("admin.teachers"))
    all_teachers = User.query.filter(User.id != tid).order_by(User.surname).all()
    return render_template("admin/teacher_form.html", teacher=teacher, all_teachers=all_teachers)


# ── Importación CSV profesores ────────────────────────────────────────────────
# Formato esperado: email,nombre,apellidos,rol,contraseña

@admin_bp.route("/profesores/<int:tid>/eliminar", methods=["POST"])
@login_required
def teacher_delete(tid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    teacher = User.query.get_or_404(tid)
    if teacher.email == PROTECTED_ADMIN_EMAIL:
        flash("El usuario administrador del sistema no puede ser eliminado.", "danger")
        return redirect(url_for("admin.teacher_edit", tid=tid))
    if teacher.id == current_user.id:
        flash("No puedes eliminar tu propia cuenta.", "danger")
        return redirect(url_for("admin.teacher_edit", tid=tid))

    name = teacher.full_name
    # Eliminar registros dependientes manualmente (sin cascade en modelo)
    from app.models.guard import GuardRecord
    from app.models.absence import Absence
    from app.models.task import Task
    from app.models.schedule import TeacherSchedule
    from app.models.guard import Guard
    from app.models.presence import UserPresence
    from app.models.activity import ExtraActivity, ExtraActivityTeacher
    from app.models.chat import ChatMessage, ChatClear
    from app.models.availability import AvailabilityPeriod, AvailabilityPeriodGroup, AvailabilityPeriodSlot

    UserPresence.query.filter_by(user_id=tid).delete(synchronize_session=False)
    GuardRecord.query.filter_by(teacher_id=tid).delete(synchronize_session=False)
    # Guardias generadas por ausencias de este profesor
    absence_ids = [a.id for a in Absence.query.filter_by(teacher_id=tid).all()]
    if absence_ids:
        # Tareas dejadas para el grupo en estas ausencias
        task_attachments = [
            t.attachment for t in Task.query.filter(Task.absence_id.in_(absence_ids))
            .with_entities(Task.attachment).all()
        ]
        Task.query.filter(Task.absence_id.in_(absence_ids)).delete(synchronize_session=False)
        guard_ids = [g.id for g in Guard.query.filter(Guard.absence_id.in_(absence_ids)).all()]
        if guard_ids:
            # Registros de OTROS profesores que cubrieron estas guardias
            GuardRecord.query.filter(GuardRecord.guard_id.in_(guard_ids)).delete(synchronize_session=False)
        Guard.query.filter(Guard.absence_id.in_(absence_ids)).delete(synchronize_session=False)
    else:
        task_attachments = []
    Absence.query.filter_by(teacher_id=tid).delete(synchronize_session=False)
    Absence.query.filter_by(reported_by_id=tid).update({"reported_by_id": None}, synchronize_session=False)
    TeacherSchedule.query.filter_by(teacher_id=tid).delete(synchronize_session=False)
    # Periodos de disponibilidad de este profesor (y sus restricciones de grupo)
    period_ids = [p.id for p in AvailabilityPeriod.query.filter_by(teacher_id=tid).all()]
    if period_ids:
        AvailabilityPeriodGroup.query.filter(AvailabilityPeriodGroup.period_id.in_(period_ids)).delete(synchronize_session=False)
        AvailabilityPeriodSlot.query.filter(AvailabilityPeriodSlot.period_id.in_(period_ids)).delete(synchronize_session=False)
        AvailabilityPeriod.query.filter(AvailabilityPeriod.id.in_(period_ids)).delete(synchronize_session=False)
    AvailabilityPeriod.query.filter_by(created_by_id=tid).update({"created_by_id": current_user.id}, synchronize_session=False)
    # Asignaciones a actividades extraescolares de este profesor; autoría se transfiere al admin que borra
    ExtraActivityTeacher.query.filter_by(teacher_id=tid).delete(synchronize_session=False)
    ExtraActivity.query.filter_by(created_by_id=tid).update({"created_by_id": current_user.id}, synchronize_session=False)
    # Mensajes de chat: se conservan, pero la autoría se transfiere al admin que borra
    ChatMessage.query.filter_by(author_id=tid).update({"author_id": current_user.id}, synchronize_session=False)
    ChatClear.query.filter_by(cleared_by_id=tid).update({"cleared_by_id": current_user.id}, synchronize_session=False)
    # Si este profesor era sustituto, reactivar al original
    if teacher.substitutes_id:
        original = User.query.get(teacher.substitutes_id)
        if original:
            original.active = True
    # Si alguien sustituía a este profesor, limpiar esa relación
    substitute_teacher = User.query.filter_by(substitutes_id=tid).first()
    if substitute_teacher:
        substitute_teacher.substitutes_id = None
        TeacherSchedule.query.filter_by(teacher_id=substitute_teacher.id).delete(synchronize_session=False)

    transferred = _transfer_email_to_active_year(teacher)

    db.session.delete(teacher)
    db.session.commit()
    _delete_task_attachments(task_attachments)
    msg = f"Profesor {name} eliminado permanentemente."
    if transferred:
        from app.models.school_year import SchoolYear
        year = SchoolYear.query.get(transferred.school_year_id)
        msg += f" Su email se ha transferido a su perfil del curso {year.name if year else ''}."
    flash(msg, "success")
    return redirect(url_for("admin.teachers"))


@admin_bp.route("/profesores/borrar-masivo", methods=["POST"])
@login_required
def teacher_bulk_delete():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.models.guard import GuardRecord, Guard
    from app.models.absence import Absence
    from app.models.task import Task
    from app.models.schedule import TeacherSchedule
    from app.models.presence import UserPresence
    from app.models.activity import ExtraActivity, ExtraActivityTeacher
    from app.models.chat import ChatMessage, ChatClear
    from app.models.availability import AvailabilityPeriod, AvailabilityPeriodGroup, AvailabilityPeriodSlot

    raw_ids = request.form.getlist("ids[]")
    try:
        ids = [int(i) for i in raw_ids if i.strip().isdigit()]
    except ValueError:
        ids = []

    ids = [i for i in ids if i != current_user.id]
    protected = User.query.filter_by(email=PROTECTED_ADMIN_EMAIL).first()
    if protected and protected.id in ids:
        ids = [i for i in ids if i != protected.id]
        flash("El usuario administrador del sistema no puede ser eliminado y fue excluido de la selección.", "warning")
    if not ids:
        flash("No se seleccionó ningún profesor válido.", "warning")
        return redirect(url_for("admin.teachers"))

    deleted = 0
    transferred_count = 0
    all_task_attachments = []
    for tid in ids:
        teacher = User.query.get(tid)
        if not teacher:
            continue
        UserPresence.query.filter_by(user_id=tid).delete(synchronize_session=False)
        GuardRecord.query.filter_by(teacher_id=tid).delete(synchronize_session=False)
        absence_ids = [a.id for a in Absence.query.filter_by(teacher_id=tid).all()]
        if absence_ids:
            # Tareas dejadas para el grupo en estas ausencias
            task_attachments = [
                t.attachment for t in Task.query.filter(Task.absence_id.in_(absence_ids))
                .with_entities(Task.attachment).all()
            ]
            Task.query.filter(Task.absence_id.in_(absence_ids)).delete(synchronize_session=False)
            all_task_attachments.extend(task_attachments)
            guard_ids = [g.id for g in Guard.query.filter(Guard.absence_id.in_(absence_ids)).all()]
            if guard_ids:
                # Registros de OTROS profesores que cubrieron estas guardias
                GuardRecord.query.filter(GuardRecord.guard_id.in_(guard_ids)).delete(synchronize_session=False)
            Guard.query.filter(Guard.absence_id.in_(absence_ids)).delete(synchronize_session=False)
        Absence.query.filter_by(teacher_id=tid).delete(synchronize_session=False)
        Absence.query.filter_by(reported_by_id=tid).update({"reported_by_id": None}, synchronize_session=False)
        TeacherSchedule.query.filter_by(teacher_id=tid).delete(synchronize_session=False)
        # Periodos de disponibilidad de este profesor (y sus restricciones de grupo)
        period_ids = [p.id for p in AvailabilityPeriod.query.filter_by(teacher_id=tid).all()]
        if period_ids:
            AvailabilityPeriodGroup.query.filter(AvailabilityPeriodGroup.period_id.in_(period_ids)).delete(synchronize_session=False)
            AvailabilityPeriodSlot.query.filter(AvailabilityPeriodSlot.period_id.in_(period_ids)).delete(synchronize_session=False)
            AvailabilityPeriod.query.filter(AvailabilityPeriod.id.in_(period_ids)).delete(synchronize_session=False)
        AvailabilityPeriod.query.filter_by(created_by_id=tid).update({"created_by_id": current_user.id}, synchronize_session=False)
        # Asignaciones a actividades extraescolares de este profesor; autoría se transfiere al admin que borra
        ExtraActivityTeacher.query.filter_by(teacher_id=tid).delete(synchronize_session=False)
        ExtraActivity.query.filter_by(created_by_id=tid).update({"created_by_id": current_user.id}, synchronize_session=False)
        # Mensajes de chat: se conservan, pero la autoría se transfiere al admin que borra
        ChatMessage.query.filter_by(author_id=tid).update({"author_id": current_user.id}, synchronize_session=False)
        ChatClear.query.filter_by(cleared_by_id=tid).update({"cleared_by_id": current_user.id}, synchronize_session=False)
        if teacher.substitutes_id:
            original = User.query.get(teacher.substitutes_id)
            if original:
                original.active = True
        substitute = User.query.filter_by(substitutes_id=tid).first()
        if substitute:
            substitute.substitutes_id = None
            TeacherSchedule.query.filter_by(teacher_id=substitute.id).delete(synchronize_session=False)

        if _transfer_email_to_active_year(teacher, exclude_ids=ids):
            transferred_count += 1

        db.session.delete(teacher)
        deleted += 1

    db.session.commit()
    _delete_task_attachments(all_task_attachments)
    msg = f"{deleted} profesor{'es' if deleted != 1 else ''} eliminado{'s' if deleted != 1 else ''} permanentemente."
    if transferred_count:
        msg += f" Se transfirió el email a su perfil del curso vigente en {transferred_count} caso{'s' if transferred_count != 1 else ''}."
    flash(msg, "success")
    return redirect(url_for("admin.teachers"))


@admin_bp.route("/profesores/toggle-emails-all", methods=["POST"])
@login_required
def teacher_toggle_emails_all():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    enable = request.form.get("enable") == "1"
    User.query.filter(User.role != "display").update({"receive_emails": enable})
    db.session.commit()
    flash(f"Correos {'activados' if enable else 'desactivados'} para todos los profesores.", "success")
    return redirect(url_for("admin.teachers"))


@admin_bp.route("/profesores/<int:tid>/toggle-emails", methods=["POST"])
@login_required
def teacher_toggle_emails(tid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    teacher = User.query.get_or_404(tid)
    teacher.receive_emails = not teacher.receive_emails
    db.session.commit()
    return redirect(url_for("admin.teachers"))


# ── Grupos ───────────────────────────────────────────────────────────────────

@admin_bp.route("/grupos")
@login_required
def groups():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.utils.school_year import get_current_school_year
    year = get_current_school_year()
    all_groups = Group.query.filter_by(school_year_id=year.id).order_by(Group.name).all()
    return render_template("admin/groups.html", groups=all_groups, current_year=year)


@admin_bp.route("/grupos/nuevo", methods=["GET", "POST"])
@login_required
def group_create():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.utils.school_year import get_current_school_year
    year = get_current_school_year()
    if request.method == "POST":
        guard_type = request.form.get("guard_type") or None
        group = Group(
            school_year_id=year.id,
            name=request.form["name"].strip(),
            abbreviation=request.form.get("abbreviation", "").strip() or None,
            high_difficulty=request.form.get("high_difficulty") == "on",
            difficulty_multiplier=float(request.form.get("difficulty_multiplier", 1.0)),
            guard_type=guard_type if guard_type in ("guard", "guard_55") else None,
        )
        db.session.add(group)
        db.session.commit()
        flash("Grupo creado.", "success")
        return redirect(url_for("admin.groups"))
    return render_template("admin/group_form.html", group=None)


@admin_bp.route("/grupos/<int:gid>/editar", methods=["GET", "POST"])
@login_required
def group_edit(gid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    group = Group.query.get_or_404(gid)
    if request.method == "POST":
        group.name = request.form["name"].strip()
        group.abbreviation = request.form.get("abbreviation", "").strip() or None
        group.high_difficulty = request.form.get("high_difficulty") == "on"
        group.difficulty_multiplier = float(request.form.get("difficulty_multiplier", 1.0))
        group.active = request.form.get("active") == "on"
        guard_type = request.form.get("guard_type") or None
        group.guard_type = guard_type if guard_type in ("guard", "guard_55") else None
        db.session.commit()
        flash("Grupo actualizado.", "success")
        return redirect(url_for("admin.groups"))
    return render_template("admin/group_form.html", group=group)


# ── Importación CSV horarios ──────────────────────────────────────────────────
# Formato: email_profesor,dia(0-4),tramo(1-7),email_grupo_o_nombre,es_guardia(true/false)


@admin_bp.route("/grupos/<int:gid>/clonar", methods=["POST"])
@login_required
def group_clone(gid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    original = Group.query.get_or_404(gid)
    clone = Group(
        school_year_id=original.school_year_id,
        name=f"{original.name} (copia)",
        abbreviation=original.abbreviation,
        high_difficulty=original.high_difficulty,
        difficulty_multiplier=original.difficulty_multiplier,
        active=original.active,
        guard_type=original.guard_type,
    )
    db.session.add(clone)
    db.session.commit()
    flash(f"Grupo clonado como '{clone.name}'. Edítalo para cambiar el nombre.", "success")
    return redirect(url_for("admin.groups", highlight=clone.id))


@admin_bp.route("/grupos/<int:gid>/borrar", methods=["POST"])
@login_required
def group_delete(gid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    group = Group.query.get_or_404(gid)
    if group.schedule_entries.count() > 0:
        flash(f"No se puede borrar '{group.name}': tiene horarios asignados. Desactívalo en su lugar.", "danger")
        return redirect(url_for("admin.groups"))
    try:
        db.session.delete(group)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(
            f"No se puede borrar '{group.name}': está en uso (guardias, tareas, "
            "actividades extraescolares o disponibilidades registradas). Desactívalo en su lugar.",
            "danger",
        )
        return redirect(url_for("admin.groups"))
    flash(f"Grupo '{group.name}' eliminado.", "success")
    return redirect(url_for("admin.groups"))


@admin_bp.route("/materias")
@login_required
def subjects():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.models.subject import Subject
    from app.utils.school_year import get_current_school_year
    year = get_current_school_year()
    all_subjects = Subject.query.filter_by(school_year_id=year.id).order_by(Subject.name).all()
    return render_template("admin/subjects.html", subjects=all_subjects, current_year=year)


@admin_bp.route("/materias/nueva", methods=["POST"])
@login_required
def subject_create():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.models.subject import Subject
    from app.utils.school_year import get_current_school_year
    year = get_current_school_year()
    name = request.form.get("name", "").strip()
    abbreviation = request.form.get("abbreviation", "").strip() or None
    guard_type = request.form.get("guard_type") or None
    if guard_type not in ("desdoble_fp", "permanencia"):
        guard_type = None
    if name:
        db.session.add(Subject(school_year_id=year.id, name=name,
                               abbreviation=abbreviation, guard_type=guard_type))
        db.session.commit()
        flash("Materia creada.", "success")
    return redirect(url_for("admin.subjects"))


@admin_bp.route("/materias/<int:sid>/editar", methods=["POST"])
@login_required
def subject_edit(sid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.models.subject import Subject
    subject = Subject.query.get_or_404(sid)
    subject.name = request.form.get("name", "").strip() or subject.name
    subject.abbreviation = request.form.get("abbreviation", "").strip() or None
    guard_type = request.form.get("guard_type") or None
    subject.guard_type = guard_type if guard_type in ("desdoble_fp", "permanencia") else None
    db.session.commit()
    flash("Materia actualizada.", "success")
    return redirect(url_for("admin.subjects"))


@admin_bp.route("/materias/<int:sid>/clonar", methods=["POST"])
@login_required
def subject_clone(sid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.models.subject import Subject
    original = Subject.query.get_or_404(sid)
    clone = Subject(
        school_year_id=original.school_year_id,
        name=f"{original.name} (copia)",
        abbreviation=None,
        guard_type=original.guard_type,
    )
    db.session.add(clone)
    db.session.commit()
    flash(f"Materia clonada como '{clone.name}'. Edítala para asignarle una abreviatura.", "success")
    return redirect(url_for("admin.subjects", highlight=clone.id))


@admin_bp.route("/materias/<int:sid>/borrar", methods=["POST"])
@login_required
def subject_delete(sid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.models.subject import Subject
    subject = Subject.query.get_or_404(sid)
    if subject.schedule_entries.count() > 0:
        flash(f"No se puede borrar '{subject.name}': tiene horarios asignados.", "danger")
        return redirect(url_for("admin.subjects"))
    db.session.delete(subject)
    db.session.commit()
    flash(f"Materia '{subject.name}' eliminada.", "success")
    return redirect(url_for("admin.subjects"))


@admin_bp.route("/aulas")
@login_required
def rooms():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    all_rooms = Room.query.order_by(Room.name).all()
    return render_template("admin/rooms.html", rooms=all_rooms)


@admin_bp.route("/aulas/nuevo", methods=["GET", "POST"])
@login_required
def room_create():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        name = request.form["name"].strip()
        if Room.query.filter_by(name=name).first():
            flash("Ya existe un aula con ese nombre.", "warning")
            return redirect(request.url)
        db.session.add(Room(name=name))
        db.session.commit()
        flash("Aula creada.", "success")
        return redirect(url_for("admin.rooms"))
    return render_template("admin/room_form.html", room=None)


@admin_bp.route("/aulas/<int:rid>/editar", methods=["GET", "POST"])
@login_required
def room_edit(rid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    room = Room.query.get_or_404(rid)
    if request.method == "POST":
        name = request.form["name"].strip()
        existing = Room.query.filter_by(name=name).first()
        if existing and existing.id != rid:
            flash("Ya existe un aula con ese nombre.", "warning")
            return redirect(request.url)
        room.name = name
        room.active = request.form.get("active") == "on"
        db.session.commit()
        flash("Aula actualizada.", "success")
        return redirect(url_for("admin.rooms"))
    return render_template("admin/room_form.html", room=room)


@admin_bp.route("/aulas/<int:rid>/clonar", methods=["POST"])
@login_required
def room_clone(rid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    original = Room.query.get_or_404(rid)
    base_name = f"{original.name} (copia)"
    name = base_name
    n = 1
    while Room.query.filter_by(name=name).first():
        n += 1
        name = f"{base_name} {n}"
    clone = Room(name=name, description=original.description, active=original.active)
    db.session.add(clone)
    db.session.commit()
    flash(f"Aula clonada como '{clone.name}'. Edítala para cambiar el nombre.", "success")
    return redirect(url_for("admin.rooms", highlight=clone.id))


@admin_bp.route("/aulas/<int:rid>/borrar", methods=["POST"])
@login_required
def room_delete(rid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    room = Room.query.get_or_404(rid)
    db.session.delete(room)
    db.session.commit()
    flash(f"Aula '{room.name}' eliminada.", "success")
    return redirect(url_for("admin.rooms"))


def _can_edit_schedule(teacher_id=None):
    if current_user.is_management:
        return True
    general = _read_mail_config().get("GENERAL", {})
    if general.get("teachers_can_edit_schedule") and current_user.role == "teacher":
        return teacher_id is None or teacher_id == current_user.id
    return False


@admin_bp.route("/horarios")
@login_required
def schedules():
    is_self_view = current_user.role == "teacher"
    if not current_user.is_management and current_user.role != "display" and not is_self_view:
        return redirect(url_for("dashboard.index"))
    from flask import current_app
    from sqlalchemy import func
    from app.utils.school_year import get_current_school_year

    current_year = get_current_school_year()
    year_id = current_year.id

    teachers = (User.query
                .filter_by(active=True)
                .filter(User.school_year_id == year_id, User.role != "display")
                .order_by(User.surname, User.name)
                .all())

    # Resumen de guardias/clases por profesor en el curso actual
    summary_rows = (db.session.query(
        TeacherSchedule.teacher_id,
        TeacherSchedule.is_guard_slot,
        func.count(TeacherSchedule.id),
    ).filter_by(school_year_id=year_id
    ).group_by(TeacherSchedule.teacher_id, TeacherSchedule.is_guard_slot).all())
    summaries = {}
    for tid, is_guard, count in summary_rows:
        s = summaries.setdefault(tid, {"guard": 0, "class": 0})
        if is_guard:
            s["guard"] = count
        else:
            s["class"] = count

    if is_self_view:
        selected = current_user
    else:
        teacher_id = request.args.get("teacher_id", type=int)
        selected = (User.query.filter_by(id=teacher_id, school_year_id=year_id).first()
                    if teacher_id else None)

    prev_teacher = next_teacher = None
    if selected and not is_self_view:
        idx = next((i for i, t in enumerate(teachers) if t.id == selected.id), None)
        if idx is not None:
            if idx > 0:
                prev_teacher = teachers[idx - 1]
            if idx < len(teachers) - 1:
                next_teacher = teachers[idx + 1]

    slots_cfg = current_app.config["TIME_SLOTS"]
    days = current_app.config["DAYS_OF_WEEK"]
    schedule_grid = None
    availability_periods = []
    if selected:
        entries = TeacherSchedule.query.filter_by(teacher_id=selected.id, school_year_id=year_id).all()
        entry_map = {(e.day_of_week, e.slot_id): e for e in entries}
        schedule_grid = []
        for s in slots_cfg:
            row = {"slot": s, "days": [entry_map.get((d, s["id"])) for d in range(5)]}
            schedule_grid.append(row)

        from app.models.availability import AvailabilityPeriod
        availability_periods = (AvailabilityPeriod.query
                                .filter_by(teacher_id=selected.id)
                                .order_by(AvailabilityPeriod.start_date.desc())
                                .all())

    from app.models.group import Group as GroupModel
    from app.models.room import Room as RoomModel
    from app.models.subject import Subject
    from app.utils.school_year import get_year_groups
    groups = get_year_groups(year_id)
    rooms  = RoomModel.query.filter_by(active=True).order_by(RoomModel.name).all()
    subjects = Subject.query.filter_by(school_year_id=year_id).order_by(Subject.name).all()

    general_cfg = _read_mail_config().get("GENERAL", {})
    teachers_can_edit_schedule = general_cfg.get("teachers_can_edit_schedule", False)

    from datetime import date as _date
    return render_template("admin/schedules.html",
                           teachers=teachers,
                           summaries=summaries,
                           selected=selected,
                           schedule_grid=schedule_grid,
                           days=days,
                           slots=slots_cfg,
                           groups=groups,
                           rooms=rooms,
                           subjects=subjects,
                           availability_periods=availability_periods,
                           prev_teacher=prev_teacher,
                           next_teacher=next_teacher,
                           today=_date.today(),
                           current_year=current_year,
                           is_self_view=is_self_view,
                           teachers_can_edit_schedule=teachers_can_edit_schedule,
                           can_edit=_can_edit_schedule(selected.id if selected else None))


@admin_bp.route("/horarios/clonar-celda", methods=["POST"])
@login_required
def schedule_clone_cell():
    teacher_id = int(request.form["teacher_id"])
    if not _can_edit_schedule(teacher_id):
        return redirect(url_for("dashboard.index"))
    from app.utils.school_year import get_current_school_year
    year_id = get_current_school_year().id

    src_day     = int(request.form["src_day"])
    src_slot_id = int(request.form["src_slot_id"])

    source = TeacherSchedule.query.filter_by(
        teacher_id=teacher_id, day_of_week=src_day, slot_id=src_slot_id,
        school_year_id=year_id,
    ).first()
    if not source:
        flash("El tramo origen ya no existe.", "warning")
        return redirect(url_for("admin.schedules", teacher_id=teacher_id))

    targets = request.form.getlist("targets")  # lista de "day:slot_id"
    cloned = 0
    for t in targets:
        try:
            d, s = map(int, t.split(":"))
        except ValueError:
            continue
        existing = TeacherSchedule.query.filter_by(
            teacher_id=teacher_id, day_of_week=d, slot_id=s,
            school_year_id=year_id,
        ).first()
        if existing:
            existing.is_guard_slot = source.is_guard_slot
            existing.group_id      = source.group_id
            existing.room_id       = source.room_id
            existing.subject_id    = source.subject_id
            existing.notes         = source.notes
        else:
            db.session.add(TeacherSchedule(
                teacher_id=teacher_id, day_of_week=d, slot_id=s,
                school_year_id=year_id,
                is_guard_slot=source.is_guard_slot,
                group_id=source.group_id,
                room_id=source.room_id,
                subject_id=source.subject_id,
                notes=source.notes,
            ))
        cloned += 1

    db.session.commit()
    if cloned:
        flash(f"Tramo clonado en {cloned} celda{'s' if cloned != 1 else ''}.", "success")
    return redirect(url_for("admin.schedules", teacher_id=teacher_id))


@admin_bp.route("/horarios/celda", methods=["POST"])
@login_required
def schedule_set_cell():
    teacher_id = int(request.form["teacher_id"])
    if not _can_edit_schedule(teacher_id):
        return redirect(url_for("dashboard.index"))
    from app.utils.school_year import get_current_school_year
    year_id = get_current_school_year().id
    day        = int(request.form["day"])
    slot_id    = int(request.form["slot_id"])
    action     = request.form["action"]  # "clear" | "guard" | "group"

    existing = TeacherSchedule.query.filter_by(
        teacher_id=teacher_id, day_of_week=day, slot_id=slot_id,
        school_year_id=year_id,
    ).first()
    if existing:
        db.session.delete(existing)
        db.session.flush()

    room_id = request.form.get("room_id", type=int) or None
    notes   = request.form.get("notes", "").strip() or None
    if action == "guard":
        db.session.add(TeacherSchedule(
            teacher_id=teacher_id, day_of_week=day, school_year_id=year_id,
            slot_id=slot_id, is_guard_slot=True, group_id=None,
            room_id=room_id, notes=notes,
        ))
    elif action == "group":
        group_id = request.form.get("group_id", type=int) or None
        subject_id = request.form.get("subject_id", type=int) or None
        group = Group.query.get(group_id) if group_id else None
        is_guard = bool(group and group.guard_type == "guard")
        if group_id or subject_id:
            db.session.add(TeacherSchedule(
                teacher_id=teacher_id, day_of_week=day, school_year_id=year_id,
                slot_id=slot_id, is_guard_slot=is_guard, group_id=group_id,
                room_id=room_id, notes=notes,
                subject_id=subject_id,
            ))
    elif action == "other":
        if notes:
            db.session.add(TeacherSchedule(
                teacher_id=teacher_id, day_of_week=day, school_year_id=year_id,
                slot_id=slot_id, is_guard_slot=False, group_id=None,
                room_id=room_id, notes=notes,
            ))

    db.session.commit()
    return redirect(url_for("admin.schedules", teacher_id=teacher_id))


@admin_bp.route("/google-drive/conectar")
@login_required
def drive_connect():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    redirect_uri = url_for("admin.drive_callback", _external=True, _scheme="https")
    return oauth.google.authorize_redirect(
        redirect_uri,
        scope="https://www.googleapis.com/auth/drive.readonly",
        access_type="offline",
        prompt="consent",
    )


@admin_bp.route("/google-drive/callback")
@login_required
def drive_callback():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    try:
        token = oauth.google.authorize_access_token()
    except Exception as e:
        flash(f"Error al conectar con Google Drive: {e}", "danger")
        return redirect(url_for("admin.data_load"))
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        flash("Google no devolvió un token permanente. Asegúrate de revocar permisos anteriores en myaccount.google.com/permissions y vuelve a intentarlo.", "warning")
        return redirect(url_for("admin.data_load"))
    current_user.google_drive_token = refresh_token
    db.session.commit()
    flash("Google Drive conectado correctamente.", "success")
    return redirect(url_for("admin.data_load"))


@admin_bp.route("/google-drive/desconectar")
@login_required
def drive_disconnect():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    current_user.google_drive_token = None
    db.session.commit()
    flash("Google Drive desconectado.", "info")
    return redirect(url_for("admin.data_load"))


@admin_bp.route("/carga-datos/drive/hojas")
@login_required
def drive_sheets():
    if not _require_management():
        return jsonify({"error": "No autorizado"}), 403
    if not current_user.google_drive_token:
        return jsonify({"error": "Google Drive no está conectado."}), 400
    file_id = request.args.get("file_id", "").strip()
    if not file_id:
        return jsonify({"error": "Falta el ID del fichero."}), 400
    try:
        from app.utils.google_drive import list_spreadsheet_sheets
        sheets = list_spreadsheet_sheets(file_id, current_user.google_drive_token)
        current_user.google_drive_file_id = file_id
        db.session.commit()
        return jsonify({"sheets": sheets})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/carga-datos/drive/cargar")
@login_required
def drive_fetch():
    if not _require_management():
        return jsonify({"error": "No autorizado"}), 403
    if not current_user.google_drive_token:
        return jsonify({"error": "Google Drive no está conectado."}), 400
    file_id = request.args.get("file_id", "").strip()
    sheet_title = request.args.get("sheet", "").strip()
    if not file_id:
        return jsonify({"error": "Falta el ID del fichero."}), 400
    try:
        from app.utils.google_drive import fetch_drive_file_as_csv, fetch_sheet_as_csv
        if sheet_title:
            csv_text = fetch_sheet_as_csv(file_id, sheet_title, current_user.google_drive_token)
        else:
            csv_text = fetch_drive_file_as_csv(file_id, current_user.google_drive_token)
        return jsonify({"csv": csv_text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/carga-datos")
@login_required
def data_load():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.utils.school_year import get_current_school_year
    from app.models.raw_schedule import RawScheduleRow
    year = get_current_school_year()
    raw_count = RawScheduleRow.query.filter_by(school_year_id=year.id).count() if year else 0
    prereqs = _dl_prereqs(year.id) if year else None
    return render_template(
        "admin/carga_datos.html",
        drive_connected=bool(current_user.google_drive_token),
        drive_file_id=current_user.google_drive_file_id or "",
        current_year=year,
        raw_count=raw_count,
        prereqs=prereqs,
    )


@admin_bp.route("/carga-datos/usuarios", methods=["POST"])
@login_required
def data_load_users():
    """Asigna emails de Google Workspace a profesores ya creados desde Drive."""
    if not _require_management():
        return jsonify({"error": "No autorizado"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    assignments = payload.get("assignments", [])   # [{teacher_id, email}]

    if not assignments:
        return jsonify({"error": "Sin asignaciones."}), 400

    from app.utils.school_year import get_current_school_year as _get_year
    year_id = _get_year().id

    from app.models.school_year import SchoolYear

    updated = skipped = 0
    errors = []
    archived = []
    for a in assignments:
        tid   = a.get("teacher_id")
        email = (a.get("email") or "").strip().lower()
        if not tid or not email or "@" not in email:
            skipped += 1
            continue
        user = User.query.filter_by(id=tid, school_year_id=year_id).first()
        if not user:
            skipped += 1
            continue
        conflict = User.query.filter(User.email == email, User.id != tid).first()
        if conflict:
            if conflict.school_year_id != year_id:
                # El email pertenece a la fila de un curso anterior del mismo
                # profesor: se archiva para liberarlo y asignarlo al curso vigente.
                conflict_year = SchoolYear.query.get(conflict.school_year_id)
                archived.append({
                    "teacher": conflict.full_name,
                    "email": conflict.email,
                    "year": conflict_year.name if conflict_year else "—",
                })
                conflict.email = f"_archived_{conflict.id}@pendiente.local"
                conflict.receive_emails = False
                conflict.active = False
            else:
                errors.append(f"{email} ya en uso por {conflict.full_name}")
                skipped += 1
                continue
        user.email = email
        user.receive_emails = True
        updated += 1

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

    return jsonify({"updated": updated, "skipped": skipped, "errors": errors, "archived": archived})


@admin_bp.route("/carga-datos/usuarios/preview", methods=["POST"])
@login_required
def data_load_users_preview():
    """Vista previa de coincidencias CSV Workspace → profesores creados desde Drive."""
    if not _require_management():
        return jsonify({"error": "No autorizado"}), 403
    payload       = request.get_json(force=True, silent=True) or {}
    rows          = payload.get("rows", [])
    nombre_col    = payload.get("nombre_col", "")
    apellidos_col = payload.get("apellidos_col", "")
    email_col     = payload.get("email_col", "")

    from app.utils.school_year import get_current_school_year as _get_year
    year_id = _get_year().id

    matches   = []
    unmatched = []

    for i, row in enumerate(rows):
        nombre    = (row.get(nombre_col)    or "").strip()
        apellidos = (row.get(apellidos_col) or "").strip()
        email     = (row.get(email_col)     or "").strip().lower()

        if not email or "@" not in email:
            unmatched.append({"row_idx": i, "nombre_gw": nombre,
                              "apellidos_gw": apellidos, "email_gw": email or "(vacío)",
                              "motivo": "Email inválido"})
            continue

        user = None
        if apellidos and nombre:
            user = User.query.filter(
                User.surname.ilike(apellidos),
                User.name.ilike(nombre),
                User.school_year_id == year_id,
            ).first()

        if user:
            already = bool(user.email and not user.email.startswith("_"))
            matches.append({
                "row_idx"         : i,
                "teacher_id"      : user.id,
                "teacher_name"    : user.full_name,
                "email_gw"        : email,
                "nombre_gw"       : nombre,
                "apellidos_gw"    : apellidos,
                "already_has_email": already,
                "current_email"   : user.email if already else None,
            })
        else:
            unmatched.append({
                "row_idx"     : i,
                "nombre_gw"   : nombre,
                "apellidos_gw": apellidos,
                "email_gw"    : email,
                "motivo"      : "No encontrado en BD del curso activo",
            })

    return jsonify({"matches": matches, "unmatched": unmatched})


@admin_bp.route("/carga-datos/abreviaturas-prof", methods=["POST"])
@login_required
def data_load_prof_abbrevs():
    """Carga profesores desde Drive: crea los que falten y actualiza abreviaturas.

    El Drive es la fuente canónica del curso activo. Si un profesor no existe
    todavía se crea (sin email, pendiente de Panel B) en lugar de descartarlo.
    """
    if not _require_management():
        return jsonify({"error": "No autorizado"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    rows = payload.get("rows", [])
    abrev_col = payload.get("abrev_col", "")
    nombre_col = payload.get("nombre_col", "")
    selected_ids = payload.get("selected", None)

    from app.utils.school_year import get_current_school_year as _get_year
    year_id = _get_year().id

    created = updated = skipped = not_found = created_no_abrev = 0
    warnings = []
    for i, row in enumerate(rows):
        if selected_ids is not None and i not in selected_ids:
            continue
        abrev = (row.get(abrev_col) or "").strip()
        nombre = (row.get(nombre_col) or "").strip()
        parts = nombre.split(",", 1) if nombre else []
        has_full_name = len(parts) == 2 and parts[0].strip() and parts[1].strip()
        if not abrev and not has_full_name:
            skipped += 1
            continue
        # Buscar en el curso activo por abreviatura o por nombre "Apellidos, Nombre"
        user = User.query.filter_by(abbreviation=abrev, school_year_id=year_id).first() if abrev else None
        if not user and has_full_name:
            user = User.query.filter(
                User.surname.ilike(parts[0].strip()),
                User.name.ilike(parts[1].strip()),
                User.school_year_id == year_id,
            ).first()
        if user:
            if abrev:
                if user.abbreviation and user.abbreviation != abrev:
                    warnings.append({"nombre": user.full_name, "abrev_bd": user.abbreviation, "abrev_nueva": abrev})
                user.abbreviation = abrev
                updated += 1
            else:
                skipped += 1
            continue
        # No existe: crear si el nombre viene en formato "Apellidos, Nombre"
        if has_full_name:
            surname_new, name_new = parts[0].strip(), parts[1].strip()
            if abrev:
                placeholder = f"_{abrev}_{year_id}@pendiente.local".lower()
                if User.query.filter_by(email=placeholder).first():
                    not_found += 1
                    warnings.append({"nombre": nombre, "abrev_bd": None, "abrev_nueva": abrev, "no_encontrado": True})
                    continue
            else:
                slug = re.sub(r"[^a-z0-9]+", "", f"{surname_new}{name_new}".lower()) or "profesor"
                placeholder = f"_{slug}_{year_id}@pendiente.local"
                n = 1
                while User.query.filter_by(email=placeholder).first():
                    n += 1
                    placeholder = f"_{slug}{n}_{year_id}@pendiente.local"
            u = User(email=placeholder, name=name_new, surname=surname_new,
                     abbreviation=abrev or None, role="teacher", active=True,
                     school_year_id=year_id, receive_emails=False)
            u.password_hash = ""
            db.session.add(u)
            if abrev:
                created += 1
            else:
                created_no_abrev += 1
                warnings.append({"nombre": u.full_name, "abrev_bd": None, "abrev_nueva": None, "sin_abrev": True})
            continue
        not_found += 1
        warnings.append({"nombre": nombre, "abrev_bd": None, "abrev_nueva": abrev, "no_encontrado": True})

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
    return jsonify({
        "created": created, "updated": updated, "skipped": skipped,
        "not_found": not_found, "created_no_abrev": created_no_abrev, "warnings": warnings,
    })


@admin_bp.route("/carga-datos/materias", methods=["POST"])
@login_required
def data_load_subjects():
    if not _require_management():
        return jsonify({"error": "No autorizado"}), 403
    from app.models.subject import Subject
    from app.utils.school_year import get_current_school_year
    payload = request.get_json(force=True, silent=True) or {}
    rows = payload.get("rows", [])
    abrev_col = payload.get("abrev_col", "")
    nombre_col = payload.get("nombre_col", "")
    selected_ids = payload.get("selected", None)
    year_id = get_current_school_year().id

    created = updated = skipped = 0
    for i, row in enumerate(rows):
        if selected_ids is not None and i not in selected_ids:
            continue
        abrev = (row.get(abrev_col) or "").strip()
        nombre = (row.get(nombre_col) or "").strip()
        if not abrev and not nombre:
            skipped += 1
            continue
        subj = Subject.query.filter_by(abbreviation=abrev, school_year_id=year_id).first() if abrev else None
        if not subj and nombre and not abrev:
            subj = Subject.query.filter_by(name=nombre, school_year_id=year_id).first()
        if subj:
            changed = False
            if abrev and subj.abbreviation != abrev:
                subj.abbreviation = abrev
                changed = True
            if nombre and subj.name != nombre:
                subj.name = nombre
                changed = True
            updated += changed
            skipped += not changed
        else:
            db.session.add(Subject(school_year_id=year_id, name=nombre or abrev,
                                   abbreviation=abrev or None))
            created += 1

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
    return jsonify({"created": created, "updated": updated, "skipped": skipped})


@admin_bp.route("/carga-datos/grupos", methods=["POST"])
@login_required
def data_load_groups():
    if not _require_management():
        return jsonify({"error": "No autorizado"}), 403
    from app.utils.school_year import get_current_school_year
    payload = request.get_json(force=True, silent=True) or {}
    rows = payload.get("rows", [])
    abrev_col = payload.get("abrev_col", "")
    nombre_col = payload.get("nombre_col", "")
    selected_ids = payload.get("selected", None)
    year_id = get_current_school_year().id

    created = updated = skipped = 0
    for i, row in enumerate(rows):
        if selected_ids is not None and i not in selected_ids:
            continue
        abrev_raw = (row.get(abrev_col) or "").strip()
        nombre = (row.get(nombre_col) or "").strip()
        if not abrev_raw and not nombre:
            skipped += 1
            continue
        # Una celda puede tener varias abreviaturas separadas por comas:
        # se crea un Group por cada una, todas con el mismo nombre.
        abrevs = [a.strip() for a in abrev_raw.split(",") if a.strip()] if abrev_raw else [""]
        for abrev in abrevs:
            grp = Group.query.filter_by(abbreviation=abrev, school_year_id=year_id).first() if abrev else None
            if not grp and nombre and not abrev:
                grp = Group.query.filter_by(name=nombre, school_year_id=year_id).first()
            if grp:
                changed = False
                if nombre and grp.name != nombre:
                    grp.name = nombre
                    changed = True
                detected = Group.detect_guard_type(grp.abbreviation)
                if detected and grp.guard_type != detected:
                    grp.guard_type = detected
                    changed = True
                updated += changed
                skipped += not changed
            else:
                db.session.add(Group(school_year_id=year_id, name=nombre or abrev,
                                     abbreviation=abrev or None,
                                     guard_type=Group.detect_guard_type(abrev)))
                created += 1

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
    return jsonify({"created": created, "updated": updated, "skipped": skipped})


@admin_bp.route("/carga-datos/raw-horarios", methods=["POST"])
@login_required
def data_load_raw_schedule():
    if not _require_management():
        return jsonify({"error": "No autorizado"}), 403
    from app.models.raw_schedule import RawScheduleRow
    from app.utils.school_year import get_current_school_year
    payload = request.get_json(force=True, silent=True) or {}
    rows = payload.get("rows", [])
    cmap = payload.get("mapping", {})   # {col_name: field_type}
    selected_ids = payload.get("selected", None)
    replace = payload.get("replace", True)

    year = get_current_school_year()
    if not year:
        return jsonify({"error": "No hay curso activo."}), 400

    def field(row, ftype):
        for col, ft in cmap.items():
            if ft == ftype and col in row:
                return (row[col] or "").strip()
        return ""

    if replace:
        RawScheduleRow.query.filter_by(school_year_id=year.id).delete(synchronize_session=False)

    loaded = skipped = 0
    for i, row in enumerate(rows):
        if selected_ids is not None and i not in selected_ids:
            continue
        teacher_abbr = field(row, "abrev_prof")
        group_abbr = field(row, "grupo")
        try:
            day = int(field(row, "dia") or 0)
            slot = int(field(row, "tramo") or 0)
        except ValueError:
            skipped += 1
            continue
        if not teacher_abbr or not group_abbr or not (1 <= day <= 5) or not (1 <= slot <= 7):
            skipped += 1
            continue
        db.session.add(RawScheduleRow(
            school_year_id=year.id,
            teacher_abbr=teacher_abbr,
            subject_abbr=field(row, "abrev_asig") or None,
            group_abbr=group_abbr,
            room_abbr=field(row, "aula") or None,
            day_of_week=day,
            slot_number=slot,
        ))
        loaded += 1

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
    return jsonify({"loaded": loaded, "skipped": skipped, "year": year.label if hasattr(year, "label") else str(year)})


def _dl_prereqs(year_id):
    """Devuelve dict con el estado de los prerequisitos para crear horarios."""
    from app.models.raw_schedule import RawScheduleRow
    from app.models.subject import Subject

    drive_teachers_count = User.query.filter(
        User.school_year_id == year_id,
        User.role.in_(["teacher", "management", "extracurricular"]),
    ).count()

    rows = RawScheduleRow.query.filter_by(school_year_id=year_id).all()
    if not rows:
        return {"ok": False, "raw_rows": 0,
                "missing_teachers": [], "missing_subjects": [], "missing_groups": [],
                "substitutions_count": 0, "drive_teachers_count": drive_teachers_count}

    t_abbrs = {r.teacher_abbr for r in rows if r.teacher_abbr}
    s_abbrs = {r.subject_abbr for r in rows if r.subject_abbr}
    g_abbrs = {r.group_abbr for r in rows if r.group_abbr}

    t_known = {u.abbreviation for u in User.query.filter(User.abbreviation.isnot(None)).all()}
    from app.models.subject import Subject
    s_known = {s.abbreviation for s in Subject.query.filter(Subject.abbreviation.isnot(None), Subject.school_year_id == year_id).all()}
    g_known = {g.abbreviation for g in Group.query.filter(Group.abbreviation.isnot(None), Group.school_year_id == year_id).all()}

    miss_t = sorted(t_abbrs - t_known)
    miss_s = sorted(s_abbrs - s_known)
    miss_g = sorted(g_abbrs - g_known)

    subs_count = User.query.filter(
        User.substitutes_id.isnot(None),
        User.school_year_id == year_id,
    ).count()

    return {
        "ok": not miss_t and not miss_s and not miss_g,
        "raw_rows": len(rows),
        "missing_teachers": miss_t,
        "missing_subjects": miss_s,
        "missing_groups": miss_g,
        "substitutions_count": subs_count,
        "drive_teachers_count": drive_teachers_count,
    }


@admin_bp.route("/carga-datos/prefs", methods=["GET", "POST"])
@login_required
def data_load_prefs():
    if not _require_management():
        return jsonify({"error": "No autorizado"}), 403
    if request.method == "POST":
        current_user.data_load_prefs = request.get_json(silent=True) or {}
        db.session.commit()
        return jsonify({"ok": True})
    return jsonify(current_user.data_load_prefs or {})


@admin_bp.route("/carga-datos/prereqs")
@login_required
def data_load_prereqs():
    if not _require_management():
        return jsonify({"error": "No autorizado"}), 403
    from app.utils.school_year import get_current_school_year
    year = get_current_school_year()
    if not year:
        return jsonify({"error": "Sin curso activo"}), 400
    return jsonify(_dl_prereqs(year.id))


@admin_bp.route("/carga-datos/crear-aulas", methods=["POST"])
@login_required
def data_load_create_rooms():
    if not _require_management():
        return jsonify({"error": "No autorizado"}), 403
    from app.models.raw_schedule import RawScheduleRow
    from app.utils.school_year import get_current_school_year

    year = get_current_school_year()
    if not year:
        return jsonify({"error": "Sin curso activo"}), 400

    room_abbrs = {
        r.room_abbr for r in RawScheduleRow.query.filter_by(school_year_id=year.id).all()
        if r.room_abbr
    }
    existing = {r.name for r in Room.query.all()}
    to_create = sorted(room_abbrs - existing)
    for name in to_create:
        db.session.add(Room(name=name))

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

    return jsonify({"created": len(to_create), "names": to_create})


@admin_bp.route("/carga-datos/preview-horarios")
@login_required
def data_load_preview():
    if not _require_management():
        return jsonify({"error": "No autorizado"}), 403
    from app.models.raw_schedule import RawScheduleRow
    from app.models.subject import Subject
    from app.utils.school_year import get_current_school_year

    year = get_current_school_year()
    if not year:
        return jsonify({"error": "Sin curso activo"}), 400

    rows = RawScheduleRow.query.filter_by(school_year_id=year.id).all()
    from app.models.subject import Subject
    teacher_map = {u.abbreviation: u for u in User.query.filter(User.abbreviation.isnot(None)).all()}
    subject_map = {s.abbreviation: s for s in Subject.query.filter(Subject.abbreviation.isnot(None), Subject.school_year_id == year.id).all()}
    group_map = {g.abbreviation: g for g in Group.query.filter(Group.abbreviation.isnot(None), Group.school_year_id == year.id).all()}
    room_map = {r.name: r for r in Room.query.all()}

    new_entries = []
    changed_entries = []
    unchanged = 0
    unresolved = []
    seen_new_keys = {}  # (teacher_id, day_idx, slot) -> entry ya añadida a new_entries

    DAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]

    for raw in rows:
        teacher = teacher_map.get(raw.teacher_abbr)
        if not teacher:
            unresolved.append(f"Profesor: {raw.teacher_abbr}")
            continue
        subject = subject_map.get(raw.subject_abbr) if raw.subject_abbr else None
        group = group_map.get(raw.group_abbr) if raw.group_abbr else None
        room = room_map.get(raw.room_abbr) if raw.room_abbr else None

        # Auto-crear aula si no existe
        if raw.room_abbr and not room:
            room = Room(name=raw.room_abbr)
            db.session.add(room)
            db.session.flush()
            room_map[raw.room_abbr] = room

        day_idx = raw.day_of_week - 1   # 0-based para TeacherSchedule
        existing = TeacherSchedule.query.filter_by(
            teacher_id=teacher.id, day_of_week=day_idx,
            slot_id=raw.slot_number, school_year_id=year.id,
        ).first()

        entry = {
            "raw_id": raw.id,
            "teacher": teacher.full_name,
            "teacher_abbr": raw.teacher_abbr,
            "group": group.name if group else raw.group_abbr,
            "subject": subject.name if subject else (raw.subject_abbr or ""),
            "room": raw.room_abbr or "",
            "day": DAYS[day_idx] if 0 <= day_idx < 5 else str(raw.day_of_week),
            "slot": raw.slot_number,
        }

        if not existing:
            key = (teacher.id, day_idx, raw.slot_number)
            prev = seen_new_keys.get(key)
            if prev:
                unresolved.append(
                    f"Conflicto: {teacher.full_name} tiene más de una clase nueva el {entry['day']} "
                    f"tramo {entry['slot']} (aulas: {prev['room'] or '—'} / {entry['room'] or '—'}) "
                    f"— revisa el CSV o el horario manualmente."
                )
                continue
            seen_new_keys[key] = entry
            new_entries.append(entry)
        else:
            changes = {}
            new_gid = group.id if group else None
            new_sid = subject.id if subject else None
            new_rid = room.id if room else None
            if existing.group_id != new_gid:
                old = Group.query.get(existing.group_id) if existing.group_id else None
                changes["grupo"] = {"old": old.name if old else "", "new": entry["group"]}
            if existing.subject_id != new_sid:
                old = Subject.query.get(existing.subject_id) if existing.subject_id else None
                changes["materia"] = {"old": old.name if old else "", "new": entry["subject"]}
            if existing.room_id != new_rid:
                old = Room.query.get(existing.room_id) if existing.room_id else None
                changes["aula"] = {"old": old.name if old else "", "new": entry["room"]}
            if changes:
                entry["existing_id"] = existing.id
                entry["changes"] = changes
                changed_entries.append(entry)
            else:
                unchanged += 1

    db.session.rollback()  # deshacer flush de aulas temporales
    return jsonify({
        "new": new_entries,
        "changed": changed_entries,
        "unchanged": unchanged,
        "unresolved": list(set(unresolved)),
    })


@admin_bp.route("/carga-datos/crear-horarios", methods=["POST"])
@login_required
def data_load_create_schedules():
    if not _require_management():
        return jsonify({"error": "No autorizado"}), 403
    from app.models.raw_schedule import RawScheduleRow
    from app.models.subject import Subject
    from app.utils.school_year import get_current_school_year

    year = get_current_school_year()
    if not year:
        return jsonify({"error": "Sin curso activo"}), 400

    payload = request.get_json(force=True, silent=True) or {}
    # raw_ids_new: lista de raw_id a crear; changed_approved: lista de {existing_id, raw_id}
    raw_ids_new = set(payload.get("new_ids", []))
    changed_approved = {item["existing_id"]: item["raw_id"] for item in payload.get("changed", [])}

    rows = RawScheduleRow.query.filter_by(school_year_id=year.id).all()
    from app.models.subject import Subject
    teacher_map = {u.abbreviation: u for u in User.query.filter(User.abbreviation.isnot(None)).all()}
    subject_map = {s.abbreviation: s for s in Subject.query.filter(Subject.abbreviation.isnot(None), Subject.school_year_id == year.id).all()}
    group_map = {g.abbreviation: g for g in Group.query.filter(Group.abbreviation.isnot(None), Group.school_year_id == year.id).all()}
    room_map = {r.name: r for r in Room.query.all()}

    created = updated = rooms_created = 0
    errors = []
    created_keys = set()  # (teacher_id, day_idx, slot) ya añadidas en este lote

    DAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]

    for raw in rows:
        if raw.id not in raw_ids_new and raw.id not in set(changed_approved.values()):
            continue
        teacher = teacher_map.get(raw.teacher_abbr)
        if not teacher:
            errors.append(f"Profesor no resuelto: {raw.teacher_abbr}")
            continue
        subject = subject_map.get(raw.subject_abbr) if raw.subject_abbr else None
        group = group_map.get(raw.group_abbr) if raw.group_abbr else None

        room = room_map.get(raw.room_abbr) if raw.room_abbr else None
        if raw.room_abbr and not room:
            room = Room(name=raw.room_abbr)
            db.session.add(room)
            db.session.flush()
            room_map[raw.room_abbr] = room
            rooms_created += 1

        day_idx = raw.day_of_week - 1

        if raw.id in raw_ids_new:
            key = (teacher.id, day_idx, raw.slot_number)
            if key in created_keys:
                day_label = DAYS[day_idx] if 0 <= day_idx < 5 else str(raw.day_of_week)
                errors.append(
                    f"Conflicto: {teacher.full_name} tiene más de una clase nueva el "
                    f"{day_label} tramo {raw.slot_number} — se omite (aula: {raw.room_abbr or '—'})."
                )
                continue
            created_keys.add(key)
            db.session.add(TeacherSchedule(
                teacher_id=teacher.id,
                group_id=group.id if group else None,
                subject_id=subject.id if subject else None,
                room_id=room.id if room else None,
                day_of_week=day_idx,
                slot_id=raw.slot_number,
                # Grupo tipo guardia oficial → tramo de guardia.
                # GUA-2 (guard_55) queda como tramo normal: no se cubre si falta
                # y el profesor cae en el pool de libres a esa hora.
                is_guard_slot=bool(group and group.guard_type == "guard"),
                school_year_id=year.id,
            ))
            created += 1
        else:
            # Actualizar existente
            for existing_id, rid in changed_approved.items():
                if rid == raw.id:
                    ts = TeacherSchedule.query.get(existing_id)
                    if ts:
                        ts.group_id = group.id if group else None
                        ts.subject_id = subject.id if subject else None
                        ts.room_id = room.id if room else None
                        ts.is_guard_slot = bool(group and group.guard_type == "guard")
                        updated += 1

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

    return jsonify({"created": created, "updated": updated, "rooms_created": rooms_created, "errors": errors})


@admin_bp.route("/horarios/disponibilidad/nueva", methods=["POST"])
@login_required
def availability_create():
    if not _require_management():
        return redirect(url_for("dashboard.index"))

    from datetime import date as _date
    from app.models.availability import AvailabilityPeriod, AvailabilityPeriodGroup, AvailabilityPeriodSlot

    teacher_id = int(request.form["teacher_id"])
    teacher = User.query.get(teacher_id)
    if not teacher:
        flash("Profesor no encontrado.", "warning")
        return redirect(url_for("admin.schedules"))

    try:
        start_date = _date.fromisoformat(request.form["start_date"])
        end_date = _date.fromisoformat(request.form["end_date"])
    except (KeyError, ValueError):
        flash("Fechas no válidas.", "warning")
        return redirect(url_for("admin.schedules", teacher_id=teacher_id))

    if end_date < start_date:
        flash("La fecha de fin no puede ser anterior a la de inicio.", "warning")
        return redirect(url_for("admin.schedules", teacher_id=teacher_id))

    from app.utils.school_year import get_current_school_year
    period = AvailabilityPeriod(
        school_year_id=get_current_school_year().id,
        teacher_id=teacher_id,
        start_date=start_date,
        end_date=end_date,
        created_by_id=current_user.id,
    )
    db.session.add(period)
    db.session.flush()

    for gid in request.form.getlist("group_ids"):
        db.session.add(AvailabilityPeriodGroup(period_id=period.id, group_id=int(gid)))

    for sid in request.form.getlist("slot_ids"):
        day_str, _, slot_str = sid.partition(":")
        if day_str.isdigit() and slot_str.isdigit():
            db.session.add(AvailabilityPeriodSlot(period_id=period.id, day_of_week=int(day_str), slot_id=int(slot_str)))

    db.session.commit()
    flash("Periodo de disponibilidad para guardia creado.", "success")
    return redirect(url_for("admin.schedules", teacher_id=teacher_id))


@admin_bp.route("/horarios/disponibilidad/<int:period_id>/editar", methods=["POST"])
@login_required
def availability_edit(period_id):
    if not _require_management():
        return redirect(url_for("dashboard.index"))

    from datetime import date as _date
    from app.models.availability import AvailabilityPeriod, AvailabilityPeriodGroup, AvailabilityPeriodSlot

    period = AvailabilityPeriod.query.get_or_404(period_id)
    teacher_id = period.teacher_id

    try:
        start_date = _date.fromisoformat(request.form["start_date"])
        end_date = _date.fromisoformat(request.form["end_date"])
    except (KeyError, ValueError):
        flash("Fechas no válidas.", "warning")
        return redirect(url_for("admin.schedules", teacher_id=teacher_id))

    if end_date < start_date:
        flash("La fecha de fin no puede ser anterior a la de inicio.", "warning")
        return redirect(url_for("admin.schedules", teacher_id=teacher_id))

    period.start_date = start_date
    period.end_date = end_date

    AvailabilityPeriodGroup.query.filter_by(period_id=period.id).delete()
    for gid in request.form.getlist("group_ids"):
        db.session.add(AvailabilityPeriodGroup(period_id=period.id, group_id=int(gid)))

    AvailabilityPeriodSlot.query.filter_by(period_id=period.id).delete()
    for sid in request.form.getlist("slot_ids"):
        day_str, _, slot_str = sid.partition(":")
        if day_str.isdigit() and slot_str.isdigit():
            db.session.add(AvailabilityPeriodSlot(period_id=period.id, day_of_week=int(day_str), slot_id=int(slot_str)))

    db.session.commit()
    flash("Periodo de disponibilidad actualizado.", "success")
    return redirect(url_for("admin.schedules", teacher_id=teacher_id))


@admin_bp.route("/horarios/disponibilidad/<int:period_id>/eliminar", methods=["POST"])
@login_required
def availability_delete(period_id):
    if not _require_management():
        return redirect(url_for("dashboard.index"))

    from app.models.availability import AvailabilityPeriod, AvailabilityPeriodGroup, AvailabilityPeriodSlot

    period = AvailabilityPeriod.query.get_or_404(period_id)
    teacher_id = period.teacher_id
    AvailabilityPeriodGroup.query.filter_by(period_id=period.id).delete()
    AvailabilityPeriodSlot.query.filter_by(period_id=period.id).delete()
    db.session.delete(period)
    db.session.commit()
    flash("Periodo de disponibilidad eliminado.", "success")
    return redirect(url_for("admin.schedules", teacher_id=teacher_id))


# ── Correo de bienvenida ──────────────────────────────────────────────────────

def _send_welcome_email(user, password: str = None):
    """Envía el correo de bienvenida si hay plantilla configurada. Lanza excepción si falla."""
    from flask_mail import Message
    from app.extensions import mail

    cfg = _read_mail_config()
    template = cfg.get("MAIL_WELCOME_TEMPLATE", "").strip()
    if not template:
        return

    password_text = password if password else "(usa tu acceso Google Workspace o contacta con el equipo directivo)"
    body = (template
            .replace("{nombre}", user.full_name)
            .replace("{email}", user.email)
            .replace("{contraseña}", password_text))

    mail.send(Message(
        subject=f"Bienvenido/a a la aplicación de guardias — {current_app.config.get('INSTITUTE_NAME', '')}",
        recipients=[user.email],
        sender=current_app.config.get("MAIL_DEFAULT_SENDER"),
        body=body,
    ))


@admin_bp.route("/profesores/<int:tid>/enviar-bienvenida", methods=["POST"])
@login_required
def teacher_send_welcome(tid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    teacher = User.query.get_or_404(tid)
    cfg = _read_mail_config()
    if not cfg.get("MAIL_WELCOME_TEMPLATE", "").strip():
        flash("No hay plantilla de bienvenida configurada. "
              "Ve a Admin → Configuración → Servidor de correo para definirla.", "warning")
        return redirect(url_for("admin.teacher_edit", tid=tid))
    try:
        _send_welcome_email(teacher)
        flash(f"Correo de bienvenida enviado a {teacher.email}.", "success")
    except Exception as e:
        cause = e.__context__ or e
        flash(f"Error al enviar el correo: {cause}", "danger")
    return redirect(url_for("admin.teacher_edit", tid=tid))


# ── Configuración ─────────────────────────────────────────────────────────────

MAIL_KEYS = ["MAIL_SERVER", "MAIL_PORT", "MAIL_USE_TLS", "MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_DEFAULT_SENDER", "MAIL_WELCOME_TEMPLATE", "MAIL_JUSTIFICATION_TEMPLATE"]
GENERAL_DEFAULTS = {
    "show_future_absences": False,
    "auto_justify_extracurricular": False,
    "guard_assign_mode": "scoring",
    "blink_guard_alert": False,
    "presence_visible_to": "none",
    "presence_detail": "count",
    "teachers_see_all_absences": True,
    "teachers_can_edit_schedule": False,
}


def _mail_config_path():
    return os.path.join(current_app.root_path, "..", "instance", "mail_config.json")


def _read_mail_config():
    import json
    path = _mail_config_path()
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _write_mail_config(data: dict):
    import json
    path = _mail_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


@admin_bp.route("/configuracion", methods=["GET", "POST"])
@login_required
def config():
    if not _require_management():
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        port_str = request.form.get("MAIL_PORT", "587").strip()
        updates = {
            "MAIL_SERVER":            request.form.get("MAIL_SERVER", "").strip(),
            "MAIL_PORT":              port_str,
            "MAIL_USE_TLS":           "true" if request.form.get("MAIL_USE_TLS") else "false",
            "MAIL_USERNAME":          request.form.get("MAIL_USERNAME", "").strip(),
            "MAIL_PASSWORD":          request.form.get("MAIL_PASSWORD", "").strip(),
            "MAIL_DEFAULT_SENDER":    request.form.get("MAIL_DEFAULT_SENDER", "").strip(),
            "MAIL_WELCOME_TEMPLATE":  request.form.get("MAIL_WELCOME_TEMPLATE", ""),
        }
        _write_mail_config(updates)
        current_app.config["MAIL_SERVER"] = updates["MAIL_SERVER"]
        current_app.config["MAIL_PORT"] = int(port_str) if port_str.isdigit() else 587
        current_app.config["MAIL_USE_TLS"] = updates["MAIL_USE_TLS"] == "true"
        current_app.config["MAIL_USERNAME"] = updates["MAIL_USERNAME"]
        current_app.config["MAIL_PASSWORD"] = updates["MAIL_PASSWORD"]
        current_app.config["MAIL_DEFAULT_SENDER"] = updates["MAIL_DEFAULT_SENDER"]
        current_app.config["MAIL_WELCOME_TEMPLATE"] = updates["MAIL_WELCOME_TEMPLATE"]
        flash("Configuración guardada y aplicada.", "success")
        return redirect(url_for("admin.config"))

    stored = _read_mail_config()
    mail_config = {k: stored.get(k, current_app.config.get(k, "")) for k in MAIL_KEYS}
    schedule_config = stored.get("MAIL_SCHEDULE", {})
    general_config = {**GENERAL_DEFAULTS, **stored.get("GENERAL", {})}
    points_config = stored.get("POINTS", {
        "absence_penalty": current_app.config.get("ABSENCE_PENALTY", -1.0),
        "points_per_hour":  current_app.config.get("POINTS_PER_HOUR",  1.0),
        "course_start":     current_app.config.get("COURSE_START", ""),
    })
    users = (User.query
             .filter_by(active=True)
             .filter(User.role != "display")
             .filter(User.receive_emails == True)
             .order_by(User.role, User.surname, User.name)
             .all())
    scorable_teachers = (User.query
                         .filter_by(active=True)
                         .filter(db.or_(
                             db.and_(User.role != "management", User.role != "display"),
                             db.and_(User.role == "management", User.track_points == True)
                         ))
                         .order_by(User.surname, User.name)
                         .all())
    help_content = _read_help_content()

    from app.models.school_year import SchoolYear
    from app.utils.school_year import get_current_school_year, year_dates
    from datetime import date as _date
    _sy_years = SchoolYear.query.order_by(SchoolYear.start_date.desc()).all()
    _sy_current = get_current_school_year()
    _sy_start_year = int(_sy_current.name.split('/')[0]) + 1
    _next_year_name = f"{_sy_start_year}/{_sy_start_year + 1}"
    _next_year_start, _next_year_end = year_dates(_next_year_name)

    return render_template("admin/config.html",
                           mail_config=mail_config,
                           schedule_config=schedule_config,
                           general_config=general_config,
                           points_config=points_config,
                           users=users,
                           scorable_teachers=scorable_teachers,
                           help_content=help_content,
                           school_years=_sy_years,
                           next_year_name=_next_year_name,
                           next_year_start=_next_year_start,
                           next_year_end=_next_year_end,
                           today=_date.today(),
                           dev_info=_dev_system_info() if current_user.dev_access else None)


@admin_bp.route("/configuracion/general", methods=["POST"])
@login_required
def config_general():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    current = _read_mail_config()
    assign_mode = request.form.get("guard_assign_mode", "scoring")
    if assign_mode not in ("scoring", "count", "random"):
        assign_mode = "scoring"
    current["GENERAL"] = {
        "show_future_absences":        bool(request.form.get("show_future_absences")),
        "auto_justify_extracurricular": bool(request.form.get("auto_justify_extracurricular")),
        "guard_assign_mode":           assign_mode,
        "blink_guard_alert":           bool(request.form.get("blink_guard_alert")),
        "presence_visible_to":         request.form.get("presence_visible_to", "none"),
        "presence_detail":             request.form.get("presence_detail", "count"),
        "teachers_see_all_absences":   bool(request.form.get("teachers_see_all_absences")),
        "teachers_can_edit_schedule":  bool(request.form.get("teachers_can_edit_schedule")),
    }
    _write_mail_config(current)
    flash("Configuración general guardada.", "success")
    return redirect(url_for("admin.config") + "#section-general")


@admin_bp.route("/configuracion/horario", methods=["POST"])
@login_required
def config_schedule():
    if not _require_management():
        return redirect(url_for("dashboard.index"))

    current = _read_mail_config()
    days = [int(d) for d in request.form.getlist("days")]
    excluded_ids = [int(i) for i in request.form.getlist("excluded_ids")]

    current["MAIL_SCHEDULE"] = {
        "enabled":        bool(request.form.get("enabled")),
        "time":           request.form.get("time", "07:30").strip(),
        "days":           days,
        "recipient_roles": request.form.getlist("recipient_roles"),
        "excluded_ids":   excluded_ids,
        "subject":        request.form.get("subject", "Guardias del {fecha}").strip(),
        "intro":          request.form.get("intro", ""),
        "show_group":     bool(request.form.get("show_group")),
        "show_reason":    bool(request.form.get("show_reason")),
        "show_status":    bool(request.form.get("show_status")),
        "show_assigned":  bool(request.form.get("show_assigned")),
        "show_room":      bool(request.form.get("show_room")),
    }
    _write_mail_config(current)

    from app.utils.mail_digest import reload_schedule
    reload_schedule(current_app._get_current_object())

    flash("Programación guardada.", "success")
    return redirect(url_for("admin.config") + "#section-schedule")


@admin_bp.route("/configuracion/horario/test", methods=["POST"])
@login_required
def test_digest():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.utils.mail_digest import send_daily_digest
    try:
        send_daily_digest(current_app._get_current_object())
        flash("Resumen enviado a todos los destinatarios configurados.", "success")
    except Exception as e:
        flash(f"Error al enviar el resumen: {e}", "danger")
    return redirect(url_for("admin.config") + "#section-schedule")


@admin_bp.route("/configuracion/test-email", methods=["POST"])
@login_required
def test_email():
    if not _require_management():
        return redirect(url_for("dashboard.index"))

    from flask_mail import Message
    from app.extensions import mail

    recipient = request.form.get("recipient", "").strip() or current_user.email
    try:
        msg = Message(
            subject="Correo de prueba — Guardias",
            sender=current_app.config.get("MAIL_DEFAULT_SENDER"),
            recipients=[recipient],
            body=f"Este es un correo de prueba enviado desde la aplicación de guardias.\n\nServidor: {current_app.config.get('MAIL_SERVER')}\nRemitente: {current_app.config.get('MAIL_DEFAULT_SENDER')}",
        )
        mail.send(msg)
        flash(f"Correo enviado correctamente a {recipient}.", "success")
    except Exception as e:
        cause = e.__context__ or e
        flash(f"Error al enviar el correo: {cause}", "danger")

    return redirect(url_for("admin.config"))


# ── Justificación de faltas ───────────────────────────────────────────────────

@admin_bp.route("/justificacion")
@login_required
def justification():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.models.absence import Absence
    from app.models.schedule import TeacherSchedule

    from datetime import date as _date
    slots_cfg = {s["id"]: s for s in current_app.config["TIME_SLOTS"]}

    cfg = _read_mail_config()
    general_cfg = {**GENERAL_DEFAULTS, **cfg.get("GENERAL", {})}
    show_future = general_cfg.get("show_future_absences", False)
    today = _date.today()

    # Profesores con alguna ausencia (justificada o no), ordenados alfabéticamente
    # Si show_future está desactivado, solo se consideran ausencias hasta hoy
    teachers_q = (User.query
        .join(Absence, Absence.teacher_id == User.id)
        .filter(User.role != "display"))
    if not show_future:
        teachers_q = teachers_q.filter(Absence.date <= today)
    teachers_with_absences = teachers_q.distinct().order_by(User.surname, User.name).all()

    teacher_id = request.args.get("teacher_id", type=int)
    if not teacher_id and teachers_with_absences:
        teacher_id = teachers_with_absences[0].id

    selected_teacher = User.query.get(teacher_id) if teacher_id else None

    absence_groups = {}

    # Para el panel derecho: TODAS las ausencias del profesor (filtradas por fecha si procede)
    absences_by_date = {}
    if selected_teacher:
        q = (Absence.query
             .filter_by(teacher_id=selected_teacher.id)
             .order_by(Absence.date.desc(), Absence.slot_id))
        if not show_future:
            q = q.filter(Absence.date <= today)
        all_absences = q.all()
        for a in all_absences:
            entry = TeacherSchedule.query.filter_by(
                teacher_id=a.teacher_id, day_of_week=a.date.weekday(),
                slot_id=a.slot_id, is_guard_slot=False,
            ).first()
            absence_groups[a.id] = entry.group.name if entry and entry.group else "—"
            absences_by_date.setdefault(a.date, []).append(a)

    # Ausencias sin justificar para envío de correos (respeta filtro de fechas)
    pending_q = Absence.query.filter_by(justified=False)
    if not show_future:
        pending_q = pending_q.filter(Absence.date <= today)
    pending_email = pending_q.order_by(Absence.date.desc(), Absence.slot_id).all()

    has_template = bool(cfg.get("MAIL_JUSTIFICATION_TEMPLATE", "").strip())

    # Profesores con ausencia activa hoy o futura (status != returned) → se consideran aún ausentes
    still_absent_ids = {
        a.teacher_id
        for a in Absence.query
        .filter(Absence.date >= today, Absence.status != "returned")
        .all()
    }

    return render_template("admin/justification.html",
                           teachers_with_absences=teachers_with_absences,
                           selected_teacher=selected_teacher,
                           absences_by_date=absences_by_date,
                           absence_groups=absence_groups,
                           slots_cfg=slots_cfg,
                           pending_email=pending_email,
                           has_template=has_template,
                           still_absent_ids=still_absent_ids)


def _send_justification_email_for_teacher(teacher_id: int) -> bool:
    """Envía un único correo al profesor con TODAS sus faltas sin justificar.
    Devuelve True si se envió correctamente."""
    from flask_mail import Message
    from app.extensions import mail
    from app.models.absence import Absence

    cfg = _read_mail_config()
    template = cfg.get("MAIL_JUSTIFICATION_TEMPLATE", "").strip()
    if not template:
        flash("No hay plantilla de correo de justificación configurada.", "danger")
        return False

    teacher = User.query.get(teacher_id)
    if not teacher:
        return False

    slots_cfg = {s["id"]: s for s in current_app.config["TIME_SLOTS"]}
    unjustified = (Absence.query
                   .filter_by(teacher_id=teacher_id, justified=False)
                   .order_by(Absence.date, Absence.slot_id)
                   .all())
    if not unjustified:
        return False

    # Construir lista de faltas
    lines = []
    for a in unjustified:
        slot = slots_cfg.get(a.slot_id, {})
        slot_label = f"{slot.get('label', a.slot_id)} ({slot.get('start','')}–{slot.get('end','')})"
        lines.append(f"  • {a.date.strftime('%d/%m/%Y')} — {slot_label} — {a.reason or '—'}")
    lista_faltas = "\n".join(lines)

    body = (template
            .replace("{nombre}", teacher.full_name)
            .replace("{lista_faltas}", lista_faltas))

    try:
        mail.send(Message(
            subject=f"Faltas pendientes de justificación — {current_app.config.get('INSTITUTE_NAME', '')}",
            recipients=[teacher.email],
            sender=current_app.config.get("MAIL_DEFAULT_SENDER"),
            body=body,
        ))
        for a in unjustified:
            a.justification_email_sent = True
        db.session.commit()
        return True
    except Exception as e:
        cause = e.__context__ or e
        flash(f"Error al enviar el correo a {teacher.email}: {cause}", "danger")
        current_app.logger.error("Error enviando justificación a %s: %s", teacher.email, e)
        return False


@admin_bp.route("/justificacion/<int:absence_id>/enviar-correo", methods=["POST"])
@login_required
def send_justification_email_single(absence_id):
    """Envía UN correo al profesor listando TODAS sus faltas sin justificar."""
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.models.absence import Absence

    absence = Absence.query.get_or_404(absence_id)
    teacher = absence.teacher

    if not teacher.receive_emails:
        flash(f"Aviso: {teacher.full_name} tiene desactivado el envío automático de correos. "
              "El correo se envía igualmente por ser manual.", "warning")

    from datetime import date as _date
    still_absent = Absence.query.filter(
        Absence.teacher_id == absence.teacher_id,
        Absence.date >= _date.today(),
        Absence.status != "returned",
    ).first()
    if still_absent:
        flash("Aviso: el profesor tiene ausencia activa a partir de hoy. El correo se envía igualmente.", "warning")

    sent = _send_justification_email_for_teacher(absence.teacher_id)
    if sent:
        flash(f"Correo enviado a {teacher.email}.", "success")
    return redirect(url_for("admin.justification", teacher_id=absence.teacher_id))


@admin_bp.route("/justificacion/justificar-dia", methods=["POST"])
@login_required
def justify_day():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from datetime import date as _date
    from app.models.absence import Absence

    teacher_id  = int(request.form["teacher_id"])
    date_str    = request.form["date"]
    target_date = _date.fromisoformat(date_str)
    justified   = request.form.get("action") == "justify"

    absences = Absence.query.filter_by(teacher_id=teacher_id, date=target_date).all()
    for a in absences:
        a.justified = justified
    db.session.commit()
    return redirect(url_for("admin.justification", teacher_id=teacher_id))


@admin_bp.route("/justificacion/<int:absence_id>/toggle", methods=["POST"])
@login_required
def toggle_justified(absence_id):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.models.absence import Absence
    absence = Absence.query.get_or_404(absence_id)
    absence.justified = not absence.justified
    db.session.commit()
    teacher_id = request.form.get("teacher_id", type=int) or absence.teacher_id
    back = request.form.get("back", "")
    url = url_for("admin.justification", teacher_id=teacher_id)
    return redirect(url + (f"#{back}" if back else ""))


@admin_bp.route("/justificacion/enviar-correos", methods=["POST"])
@login_required
def send_justification_emails():
    """Envía UN correo por profesor (listando todas sus faltas sin justificar)."""
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.models.absence import Absence

    absence_ids = [int(x) for x in request.form.getlist("absence_ids")]
    teacher_id  = request.form.get("teacher_id", type=int)
    if not absence_ids:
        flash("No se seleccionó ninguna ausencia.", "warning")
        return redirect(url_for("admin.justification", teacher_id=teacher_id))

    # Agrupa por profesor y envía un único correo por cada uno
    teacher_ids = list({Absence.query.get(aid).teacher_id
                        for aid in absence_ids
                        if Absence.query.get(aid)})
    sent = skipped = 0
    for tid in teacher_ids:
        t = User.query.get(tid)
        if t and not t.receive_emails:
            flash(f"Aviso: {t.full_name} tiene desactivado el envío automático de correos. "
                  "El correo se envía igualmente por ser manual.", "warning")
        ok = _send_justification_email_for_teacher(tid)
        if ok:
            sent += 1
        else:
            skipped += 1

    if sent:
        flash(f"Correo{'s' if sent != 1 else ''} enviado{'s' if sent != 1 else ''}: {sent} profesor{'es' if sent != 1 else ''}.", "success")
    if skipped:
        flash(f"Omitidos (sin faltas pendientes o error): {skipped}.", "warning")
    return redirect(url_for("admin.justification", teacher_id=teacher_id))


# ── Informe PDF de faltas sin justificar ──────────────────────────────────────

def _build_justification_report_data(desde, hasta):
    """Devuelve lista de dicts con los datos del informe, agrupados por profesor y fecha."""
    from datetime import date as _date
    from app.models.absence import Absence
    from app.models.schedule import TeacherSchedule

    from app.utils import fecha_es as _fecha_es, _DIAS_ABREV
    slots_cfg = current_app.config["TIME_SLOTS"]
    slot_map = {s["id"]: s for s in slots_cfg}
    break_ids = {s["id"] for s in slots_cfg if s.get("is_break")}

    absences = (Absence.query
                .filter_by(justified=False)
                .filter(Absence.date >= desde, Absence.date <= hasta)
                .join(User, Absence.teacher_id == User.id)
                .filter(User.role != "display")
                .order_by(User.surname, User.name, Absence.date, Absence.slot_id)
                .all())

    # Agrupar: teacher_id → date → [absence]
    from collections import defaultdict
    by_teacher = defaultdict(lambda: defaultdict(list))
    teacher_map = {}
    for a in absences:
        by_teacher[a.teacher_id][a.date].append(a)
        teacher_map[a.teacher_id] = a.teacher

    rows = []
    for tid, dates in sorted(by_teacher.items(), key=lambda x: teacher_map[x[0]].full_name):
        teacher = teacher_map[tid]
        teacher_rows = []
        for d in sorted(dates.keys()):
            day_absences = dates[d]
            absent_slots = {a.slot_id for a in day_absences}
            non_break_absent = absent_slots - break_ids

            # Tramos de clase (no-recreo, no-guardia) del horario del profesor ese día
            scheduled_slots = {
                e.slot_id for e in TeacherSchedule.query.filter_by(
                    teacher_id=tid, day_of_week=d.weekday()
                ).all()
                if e.slot_id not in break_ids and not e.is_guard_slot
            }
            if scheduled_slots:
                # Día completo si todos los tramos del horario del profesor están ausentes
                full_day = scheduled_slots <= non_break_absent
            else:
                # Sin horario registrado: día completo si cubre todos los tramos lectivos
                all_non_break = {s["id"] for s in slots_cfg if not s.get("is_break")}
                full_day = bool(all_non_break) and all_non_break <= non_break_absent

            slots_label = ", ".join(
                slot_map[sid]["label"] for sid in sorted(absent_slots)
                if sid in slot_map
            )
            teacher_rows.append({
                "date": d,
                "date_label": f"{_DIAS_ABREV[d.weekday()]} {d.strftime('%d/%m/%Y')}",
                "date_full": _fecha_es(d, "%A, %d de %B de %Y"),
                "full_day": full_day,
                "slots_label": slots_label,
                "slot_details": [
                    (slot_map[sid]["label"], slot_map[sid]["start"], slot_map[sid]["end"])
                    for sid in sorted(absent_slots) if sid in slot_map
                ],
                "count": len(absent_slots),
                "teacher": teacher.full_name,
            })
        rows.append({"teacher": teacher, "entries": teacher_rows,
                     "total": sum(r["count"] for r in teacher_rows)})
    return rows


@admin_bp.route("/informe-justificacion")
@login_required
def justification_report():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from datetime import date as _date, timedelta
    from app.models.school_year import SchoolYear

    # Por defecto: primer día del mes actual → hoy
    today = _date.today()
    default_desde = today.replace(day=1).isoformat()
    default_hasta = today.isoformat()

    desde_str = request.args.get("desde", default_desde)
    hasta_str = request.args.get("hasta", default_hasta)

    year_id = request.args.get("year_id", type=int)
    if year_id and "desde" not in request.args and "hasta" not in request.args:
        selected = SchoolYear.query.get(year_id)
        if selected:
            desde_str = selected.start_date.isoformat()
            hasta_str = selected.end_date.isoformat()

    summary = None
    try:
        desde = _date.fromisoformat(desde_str)
        hasta = _date.fromisoformat(hasta_str)
        rows_data = _build_justification_report_data(desde, hasta)
        flat_rows = [r for teacher_data in rows_data for r in teacher_data["entries"]]
        summary = {
            "rows": flat_rows,
            "total_slots": sum(r["count"] for r in flat_rows),
            "total_teachers": len(rows_data),
        } if rows_data else None
    except (ValueError, TypeError):
        desde_str, hasta_str = default_desde, default_hasta

    years = SchoolYear.query.order_by(SchoolYear.start_date.desc()).all()
    return render_template("admin/justification_report.html",
                           desde=desde_str, hasta=hasta_str, summary=summary, years=years)


@admin_bp.route("/informe-justificacion/imprimir")
@login_required
def justification_report_pdf():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from datetime import date as _date

    desde_str = request.args.get("desde", "")
    hasta_str = request.args.get("hasta", "")
    try:
        desde = _date.fromisoformat(desde_str)
        hasta = _date.fromisoformat(hasta_str)
    except (ValueError, TypeError):
        flash("Fechas no válidas.", "danger")
        return redirect(url_for("admin.justification_report"))

    rows_data = _build_justification_report_data(desde, hasta)

    return render_template(
        "admin/print_justification.html",
        desde=desde,
        hasta=hasta,
        rows_data=rows_data,
        institute_name=current_app.config.get("INSTITUTE_NAME", ""),
    )


# ── Página de ayuda ───────────────────────────────────────────────────────────

def _help_content_path():
    return os.path.join(current_app.root_path, "..", "instance", "help_content.md")


def _read_help_content():
    path = _help_content_path()
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _write_help_content(content: str):
    path = _help_content_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


@admin_bp.route("/copia-seguridad")
@login_required
def backup_database():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    import gzip
    import subprocess
    from datetime import datetime
    from sqlalchemy.engine import make_url
    from flask import Response

    url = make_url(current_app.config["SQLALCHEMY_DATABASE_URI"])
    cmd = [
        "mysqldump",
        "-h", url.host or "localhost",
        "-P", str(url.port or 3306),
        "-u", url.username or "root",
        "--single-transaction", "--routines", "--triggers",
        "--databases", url.database,
    ]
    env = os.environ.copy()
    if url.password:
        env["MYSQL_PWD"] = url.password

    try:
        result = subprocess.run(cmd, env=env, capture_output=True, check=True, timeout=300)
    except FileNotFoundError:
        flash("No se encontró la herramienta mysqldump en el servidor.", "danger")
        return redirect(url_for("admin.config") + "#section-backup")
    except subprocess.CalledProcessError as e:
        current_app.logger.error("Error en mysqldump: %s", e.stderr.decode(errors="replace"))
        flash("Error al generar la copia de seguridad de la base de datos.", "danger")
        return redirect(url_for("admin.config") + "#section-backup")

    filename = f"guardias_backup_{datetime.now():%Y%m%d_%H%M%S}.sql.gz"
    return Response(
        gzip.compress(result.stdout),
        mimetype="application/gzip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@admin_bp.route("/copia-seguridad/restaurar", methods=["POST"])
@login_required
def restore_database():
    if not _require_developer():
        return redirect(url_for("dashboard.index"))

    if request.form.get("confirm_text", "").strip() != "RESTAURAR":
        flash("Confirmación incorrecta: escribe RESTAURAR para continuar. La base de datos no se ha modificado.", "danger")
        return redirect(url_for("admin.config") + "#section-backup")

    backup_file = request.files.get("backup_file")
    if not backup_file or not backup_file.filename:
        flash("Selecciona un fichero de copia de seguridad (.sql.gz).", "danger")
        return redirect(url_for("admin.config") + "#section-backup")
    if not backup_file.filename.lower().endswith(".sql.gz"):
        flash("El fichero debe tener extensión .sql.gz.", "danger")
        return redirect(url_for("admin.config") + "#section-backup")

    import gzip
    import subprocess
    from sqlalchemy.engine import make_url

    try:
        sql_data = gzip.decompress(backup_file.read())
    except OSError:
        flash("El fichero no es un .gz válido.", "danger")
        return redirect(url_for("admin.config") + "#section-backup")

    url = make_url(current_app.config["SQLALCHEMY_DATABASE_URI"])
    cmd = [
        "mysql",
        "-h", url.host or "localhost",
        "-P", str(url.port or 3306),
        "-u", url.username or "root",
    ]
    env = os.environ.copy()
    if url.password:
        env["MYSQL_PWD"] = url.password

    app = current_app._get_current_object()

    def _do_restore():
        import re as _re
        import eventlet
        import eventlet.tpool
        from app.extensions import socketio as _sock

        def progress(msg, done=False, error=False):
            _sock.emit("restore_progress", {"msg": msg, "done": done, "error": error})
            _sock.sleep(0)  # cede el event loop para que el mensaje se envíe ya

        def _run_mysql(input_data, timeout=60):
            # Corre en thread pool para no bloquear el event loop de eventlet
            return subprocess.run(cmd, input=input_data, env=env,
                                  capture_output=True, check=True, timeout=timeout)

        # Espera a que el navegador cargue la página y conecte el WebSocket
        _sock.sleep(1.5)

        try:
            with app.app_context():
                db.engine.dispose()

            sql_text = sql_data.decode("utf-8", errors="replace")

            # Separar en preámbulo + una sección por tabla
            parts = _re.split(r'(?=-- Table structure for table )', sql_text)
            preamble = parts[0]
            table_parts = parts[1:]

            # Prefijo para cada sección: extraer USE `db` del preámbulo y
            # añadir explícitamente charset y FK checks (en el dump vienen como
            # comentarios condicionales /*!...*/ que nuestro filtro no captura)
            use_line = ""
            for line in preamble.splitlines():
                s = line.strip()
                if s.startswith("USE `"):
                    use_line = s if s.endswith(";") else s + ";"
                    break
            prefix = "SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\n"
            if use_line:
                prefix += use_line + "\n"

            # Ejecutar preámbulo (crea la BD si no existe) en thread pool
            eventlet.tpool.execute(_run_mysql, preamble.encode("utf-8"), 30)

            total = len(table_parts)
            progress(f"Restaurando {total} tablas…")

            for i, part in enumerate(table_parts, 1):
                m = _re.search(r"Table structure for table `([^`]+)`", part)
                tname = m.group(1) if m else f"tabla_{i}"
                progress(f"Tabla {tname} ({i}/{total})")
                # Eliminar líneas que restauran variables de sesión (@OLD_*)
                # que sólo tienen sentido en una sesión mysql única
                clean = "\n".join(
                    l for l in part.splitlines() if "=@OLD_" not in l
                ) + "\n"
                eventlet.tpool.execute(_run_mysql, (prefix + clean).encode("utf-8"), 60)

            progress("Base de datos restaurada correctamente.", done=True)
            app.logger.info("Restauración completada (%d tablas).", total)

        except subprocess.CalledProcessError as e:
            err = e.stderr.decode(errors="replace")
            progress(f"Error MySQL: {err[:300]}", done=True, error=True)
            app.logger.error("Error al restaurar la base de datos: %s", err)
        except Exception as exc:
            progress(f"Error inesperado: {exc}", done=True, error=True)
            app.logger.error("Excepción inesperada en restauración: %s", exc)

    from app.extensions import socketio as _socketio
    _socketio.start_background_task(_do_restore)

    return redirect(url_for("admin.restore_progress"))


@admin_bp.route("/copia-seguridad/restaurando")
@login_required
def restore_progress():
    if not _require_developer():
        return redirect(url_for("dashboard.index"))
    return render_template("admin/restore_progress.html")


@admin_bp.route("/configuracion/ayuda", methods=["POST"])
@login_required
def config_help():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    _write_help_content(request.form.get("help_content", ""))
    flash("Página de ayuda guardada.", "success")
    return redirect(url_for("admin.config") + "#section-help")


# ── Puntuación ────────────────────────────────────────────────────────────────

POINTS_KEYS = ["absence_penalty", "points_per_hour", "course_start"]


@admin_bp.route("/configuracion/puntuacion", methods=["POST"])
@login_required
def config_points():
    if not _require_management():
        return redirect(url_for("dashboard.index"))

    current = _read_mail_config()
    current["POINTS"] = {
        "absence_penalty": float(request.form.get("absence_penalty", -1.0)),
        "points_per_hour":  float(request.form.get("points_per_hour",  1.0)),
        "course_start":     request.form.get("course_start", "").strip(),
    }
    _write_mail_config(current)
    current_app.config["ABSENCE_PENALTY"] = current["POINTS"]["absence_penalty"]
    current_app.config["POINTS_PER_HOUR"]  = current["POINTS"]["points_per_hour"]
    current_app.config["COURSE_START"]     = current["POINTS"]["course_start"]
    flash("Configuración de puntuación guardada.", "success")
    return redirect(url_for("admin.config") + "#section-points")


@admin_bp.route("/puntuacion/exportar-csv")
@login_required
def points_export_csv():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from flask import Response
    from datetime import date

    teachers = (User.query
                .filter_by(active=True)
                .filter(User.role.notin_(["management", "display"]))
                .filter(db.or_(User.role != "management", User.track_points == True))
                .order_by(User.surname, User.name)
                .all())

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Apellidos", "Nombre", "Email", "Rol", "Puntos"])
    for t in teachers:
        writer.writerow([t.surname, t.name, t.email, t.role, t.points])

    filename = f"puntos_{date.today().isoformat()}.csv"
    return Response(
        "﻿" + output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@admin_bp.route("/puntuacion/reset-all", methods=["POST"])
@login_required
def points_reset_all():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    # Solo resetear profesores activos; los sustituidos (inactivos) quedan con sus puntos congelados
    User.query.filter(User.role.notin_(["management", "display"]), User.active == True).update({"points": 0.0})
    User.query.filter_by(role="management", track_points=True, active=True).update({"points": 0.0})
    db.session.commit()
    flash("Puntos de todos los profesores reseteados a 0.", "success")
    return redirect(url_for("admin.config") + "#section-points")


@admin_bp.route("/puntuacion/reset/<int:tid>", methods=["POST"])
@login_required
def points_reset_teacher(tid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    teacher = User.query.get_or_404(tid)
    teacher.points = 0.0
    db.session.commit()
    flash(f"Puntos de {teacher.full_name} reseteados a 0.", "success")
    return redirect(url_for("admin.config") + "#section-points")


# ── Cursos escolares ──────────────────────────────────────────────────────────

def _previous_school_year(year):
    """Devuelve el curso inmediatamente anterior al dado (por fecha de inicio)."""
    from app.models.school_year import SchoolYear
    return (SchoolYear.query
            .filter(SchoolYear.start_date < year.start_date)
            .order_by(SchoolYear.start_date.desc())
            .first())


def _copy_year_data(src_year, dst_year, copy_teachers=False, copy_groups=False, copy_subjects=False):
    """Copia profesores, grupos y/o materias de src_year a dst_year.

    Es idempotente: los elementos que ya existen en el curso destino (mismo
    nombre o abreviatura) se saltan, así que puede ejecutarse varias veces.

    Profesores: si el curso destino es el vigente, el email real y la
    contraseña se transfieren a la fila nueva y la antigua queda archivada
    (_archived_...@pendiente.local), igual que hace la carga de datos. Si el
    destino no es el vigente, la fila nueva recibe un email marcador para no
    romper el login del curso en uso. Los puntos empiezan a 0 y no se copian
    los vínculos de sustitución.

    Las aulas no se copian porque son globales (no dependen del curso).
    Devuelve un dict de contadores. El llamador hace commit.
    """
    from app.models.subject import Subject

    counts = {"teachers": 0, "groups": 0, "subjects": 0, "skipped": 0}

    if copy_groups:
        existing = {(g.name or "").strip().lower()
                    for g in Group.query.filter_by(school_year_id=dst_year.id).all()}
        for g in Group.query.filter_by(school_year_id=src_year.id).all():
            if (g.name or "").strip().lower() in existing:
                counts["skipped"] += 1
                continue
            db.session.add(Group(
                school_year_id=dst_year.id,
                name=g.name,
                abbreviation=g.abbreviation,
                high_difficulty=g.high_difficulty,
                difficulty_multiplier=g.difficulty_multiplier,
                active=g.active,
                guard_type=g.guard_type,
            ))
            counts["groups"] += 1

    if copy_subjects:
        existing = {(s.name or "").strip().lower()
                    for s in Subject.query.filter_by(school_year_id=dst_year.id).all()}
        for s in Subject.query.filter_by(school_year_id=src_year.id).all():
            if (s.name or "").strip().lower() in existing:
                counts["skipped"] += 1
                continue
            db.session.add(Subject(
                school_year_id=dst_year.id,
                name=s.name,
                abbreviation=s.abbreviation,
                guard_type=s.guard_type,
            ))
            counts["subjects"] += 1

    if copy_teachers:
        existing_users = User.query.filter(User.school_year_id == dst_year.id,
                                           User.role != "display").all()
        by_abbr = {u.abbreviation.strip().lower() for u in existing_users if u.abbreviation}
        by_name = {(u.surname.strip().lower(), u.name.strip().lower()) for u in existing_users}
        src_teachers = User.query.filter(User.school_year_id == src_year.id,
                                         User.role != "display").all()
        for t in src_teachers:
            if ((t.abbreviation and t.abbreviation.strip().lower() in by_abbr)
                    or (t.surname.strip().lower(), t.name.strip().lower()) in by_name):
                counts["skipped"] += 1
                continue
            new_t = User(
                name=t.name,
                surname=t.surname,
                abbreviation=t.abbreviation,
                role=t.role,
                track_points=t.track_points,
                active=t.active,
                receive_emails=t.receive_emails,
                school_year_id=dst_year.id,
                points=0.0,
            )
            if dst_year.is_current and not _is_placeholder_email(t.email):
                email = t.email
                t.email = f"_archived_{t.id}@pendiente.local"
                t.receive_emails = False
                t.active = False
                # Flush para liberar el email único antes de insertarlo en la
                # fila nueva (los INSERT se ejecutan antes que los UPDATE).
                db.session.flush()
                new_t.email = email
                new_t.password_hash = t.password_hash
            else:
                new_t.email = f"_copia_{t.id}_{dst_year.id}@pendiente.local"
                new_t.password_hash = t.password_hash or ""
                new_t.receive_emails = False
            db.session.add(new_t)
            counts["teachers"] += 1

    return counts


def _copy_summary_msg(counts):
    parts = []
    if counts["teachers"]:
        parts.append(f"{counts['teachers']} profesores copiados.")
    if counts["groups"]:
        parts.append(f"{counts['groups']} grupos copiados.")
    if counts["subjects"]:
        parts.append(f"{counts['subjects']} materias copiadas.")
    if counts["skipped"]:
        parts.append(f"{counts['skipped']} elementos ya existían y se han saltado.")
    return " ".join(parts)


@admin_bp.route("/cursos")
@login_required
def school_years():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.models.school_year import SchoolYear
    from app.utils.school_year import get_current_school_year, year_name_for, year_dates
    from datetime import date as _date

    years = SchoolYear.query.order_by(SchoolYear.start_date.desc()).all()
    current = get_current_school_year()

    # Precalcular nombre del siguiente curso
    start_year = int(current.name.split('/')[0]) + 1
    next_name = f"{start_year}/{start_year + 1}"
    next_start, next_end = year_dates(next_name)

    return render_template(
        "admin/school_years.html",
        years=years,
        current_year=current,
        next_name=next_name,
        next_start=next_start,
        next_end=next_end,
        today=_date.today(),
    )


@admin_bp.route("/cursos/nuevo", methods=["POST"])
@login_required
def school_year_create():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.models.school_year import SchoolYear
    from app.utils.school_year import year_dates, get_current_school_year

    name = request.form.get("name", "").strip()
    if not name or '/' not in name:
        flash("Nombre de curso inválido.", "danger")
        return redirect(url_for("admin.config") + "#section-school-years")

    if request.form.get("confirm_irreversible") != "on":
        flash("Debes confirmar que entiendes que esta acción es irreversible.", "danger")
        return redirect(url_for("admin.config") + "#section-school-years")

    if SchoolYear.query.filter_by(name=name).first():
        flash(f"El curso {name} ya existe.", "warning")
        return redirect(url_for("admin.config") + "#section-school-years")

    prev_year = get_current_school_year()
    start, end = year_dates(name)

    SchoolYear.query.update({"is_current": False})
    new_year = SchoolYear(name=name, start_date=start, end_date=end, is_current=True)
    db.session.add(new_year)
    db.session.flush()

    counts = {"teachers": 0, "groups": 0, "subjects": 0, "skipped": 0}
    if prev_year:
        counts = _copy_year_data(
            prev_year, new_year,
            copy_teachers=request.form.get("copy_teachers") == "on",
            copy_groups=request.form.get("copy_groups") == "on",
            copy_subjects=request.form.get("copy_subjects") == "on",
        )

    from app.utils.points import recalculate_points_for_year
    recalculate_points_for_year(new_year)

    db.session.commit()
    msg = f"Curso {name} creado y activado. {_copy_summary_msg(counts)}".strip()
    if prev_year:
        msg += f" El curso {prev_year.name} ha pasado a histórico: solo es consultable desde los informes."
    flash(msg, "success")
    return redirect(url_for("admin.config") + "#section-school-years")


@admin_bp.route("/cursos/<int:year_id>/editar", methods=["GET", "POST"])
@login_required
def school_year_edit(year_id):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.models.school_year import SchoolYear
    from datetime import date as _date

    year = SchoolYear.query.get_or_404(year_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        start_str = request.form.get("start_date", "").strip()
        end_str = request.form.get("end_date", "").strip()

        if not name or '/' not in name:
            flash("Nombre de curso inválido (formato AAAA/AAAA).", "danger")
            return redirect(url_for("admin.school_year_edit", year_id=year_id))

        existing = SchoolYear.query.filter(SchoolYear.name == name, SchoolYear.id != year_id).first()
        if existing:
            flash(f"Ya existe un curso con el nombre {name}.", "danger")
            return redirect(url_for("admin.school_year_edit", year_id=year_id))

        try:
            year.start_date = _date.fromisoformat(start_str)
            year.end_date = _date.fromisoformat(end_str)
        except ValueError:
            flash("Fechas inválidas.", "danger")
            return redirect(url_for("admin.school_year_edit", year_id=year_id))

        year.name = name
        db.session.commit()
        flash(f"Curso {name} actualizado.", "success")
        return redirect(url_for("admin.config") + "#section-school-years")

    return render_template("admin/school_year_form.html", year=year,
                           prev_year=_previous_school_year(year))


@admin_bp.route("/cursos/<int:year_id>/copiar-datos", methods=["POST"])
@login_required
def school_year_copy_data(year_id):
    """Copia profesores/grupos/materias del curso anterior al curso dado."""
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.models.school_year import SchoolYear

    year = SchoolYear.query.get_or_404(year_id)
    prev_year = _previous_school_year(year)
    if not prev_year:
        flash("No hay ningún curso anterior del que copiar datos.", "warning")
        return redirect(url_for("admin.school_year_edit", year_id=year_id))

    counts = _copy_year_data(
        prev_year, year,
        copy_teachers=request.form.get("copy_teachers") == "on",
        copy_groups=request.form.get("copy_groups") == "on",
        copy_subjects=request.form.get("copy_subjects") == "on",
    )
    db.session.commit()

    msg = _copy_summary_msg(counts)
    if not msg:
        msg = "No se ha copiado nada (no había nada seleccionado o nada que copiar)."
    flash(f"Copia desde {prev_year.name}: {msg}", "success")
    return redirect(url_for("admin.school_year_edit", year_id=year_id))


@admin_bp.route("/cursos/<int:year_id>/borrar", methods=["POST"])
@login_required
def school_year_delete(year_id):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.models.school_year import SchoolYear
    from app.models.schedule import TeacherSchedule
    from app.models.raw_schedule import RawScheduleRow
    from app.models.availability import AvailabilityPeriod, AvailabilityPeriodGroup, AvailabilityPeriodSlot
    from app.models.activity import ExtraActivity, ExtraActivityGroup, ExtraActivityTeacher
    from app.models.subject import Subject
    from app.models.guard import Guard, GuardRecord
    from app.models.task import Task

    year = SchoolYear.query.get_or_404(year_id)

    if year.is_current:
        flash("No se puede borrar el curso activo.", "danger")
        return redirect(url_for("admin.config") + "#section-school-years")

    # Borrar dependencias en orden para respetar FKs
    TeacherSchedule.query.filter_by(school_year_id=year_id).delete(synchronize_session=False)
    RawScheduleRow.query.filter_by(school_year_id=year_id).delete(synchronize_session=False)

    period_ids = [p.id for p in AvailabilityPeriod.query.filter_by(school_year_id=year_id).with_entities(AvailabilityPeriod.id).all()]
    if period_ids:
        AvailabilityPeriodGroup.query.filter(AvailabilityPeriodGroup.period_id.in_(period_ids)).delete(synchronize_session=False)
        AvailabilityPeriodSlot.query.filter(AvailabilityPeriodSlot.period_id.in_(period_ids)).delete(synchronize_session=False)
    AvailabilityPeriod.query.filter_by(school_year_id=year_id).delete(synchronize_session=False)

    activity_ids = [a.id for a in ExtraActivity.query.filter_by(school_year_id=year_id).with_entities(ExtraActivity.id).all()]
    if activity_ids:
        ExtraActivityGroup.query.filter(ExtraActivityGroup.activity_id.in_(activity_ids)).delete(synchronize_session=False)
        ExtraActivityTeacher.query.filter(ExtraActivityTeacher.activity_id.in_(activity_ids)).delete(synchronize_session=False)
    ExtraActivity.query.filter_by(school_year_id=year_id).delete(synchronize_session=False)

    # Guardias y tareas que apuntan a los grupos del curso
    group_ids = [g.id for g in Group.query.filter_by(school_year_id=year_id).with_entities(Group.id).all()]
    task_attachments = []
    if group_ids:
        task_attachments = [
            t.attachment for t in Task.query.filter(Task.group_id.in_(group_ids))
            .with_entities(Task.attachment).all()
        ]
        Task.query.filter(Task.group_id.in_(group_ids)).delete(synchronize_session=False)
        guard_ids = [g.id for g in Guard.query.filter(Guard.group_id.in_(group_ids)).with_entities(Guard.id).all()]
        if guard_ids:
            GuardRecord.query.filter(GuardRecord.guard_id.in_(guard_ids)).delete(synchronize_session=False)
            Guard.query.filter(Guard.id.in_(guard_ids)).delete(synchronize_session=False)
        # Referencias a estos grupos desde actividades o disponibilidades de
        # otros cursos (p. ej. actividades sin curso asignado): sin esto el
        # DELETE de groups viola la FK extra_activity_groups/availability_period_groups
        ExtraActivityGroup.query.filter(ExtraActivityGroup.group_id.in_(group_ids)).delete(synchronize_session=False)
        AvailabilityPeriodGroup.query.filter(AvailabilityPeriodGroup.group_id.in_(group_ids)).delete(synchronize_session=False)

    Group.query.filter_by(school_year_id=year_id).delete(synchronize_session=False)
    Subject.query.filter_by(school_year_id=year_id).delete(synchronize_session=False)

    # Antes de desvincular a los profesores, devolver sus emails reales a sus
    # filas homónimas con email marcador de otros cursos (priorizando el vigente),
    # para que el login no quede atrapado en filas huérfanas.
    for t in User.query.filter(User.school_year_id == year_id, User.role != "display").all():
        _transfer_email_to_active_year(t)

    # Los profesores del curso quedan sin curso asignado en lugar de borrarse,
    # para no perder su historial (puntos, ausencias, chat, etc.)
    User.query.filter_by(school_year_id=year_id).update({"school_year_id": None})

    name = year.name
    db.session.delete(year)
    db.session.commit()
    _delete_task_attachments(task_attachments)
    flash(f"Curso {name} y todos sus datos eliminados.", "success")
    return redirect(url_for("admin.config") + "#section-school-years")


# ── Desarrollo ──────────────────────────────────────────────────────────────

def _dev_system_info():
    """Recopila datos de diagnóstico para la sección Desarrollo de configuración."""
    import platform
    import subprocess
    import sqlalchemy
    import flask
    from sqlalchemy.engine import make_url
    from app.models.absence import Absence
    from app.models.guard import Guard, GuardRecord
    from app.models.activity import ExtraActivity
    from app.models.chat import ChatMessage
    from app.models.schedule import TeacherSchedule
    from app.models.school_year import SchoolYear

    counts = {
        "Usuarios":                     User.query.count(),
        "Grupos":                       Group.query.count(),
        "Aulas":                        Room.query.count(),
        "Cursos escolares":             SchoolYear.query.count(),
        "Horarios":                     TeacherSchedule.query.count(),
        "Ausencias":                    Absence.query.count(),
        "Guardias":                     Guard.query.count(),
        "Registros de guardia":         GuardRecord.query.count(),
        "Actividades extraescolares":   ExtraActivity.query.count(),
        "Mensajes de chat":             ChatMessage.query.count(),
    }

    db_size_mb = None
    try:
        url = make_url(current_app.config["SQLALCHEMY_DATABASE_URI"])
        db_size_mb = db.session.execute(db.text(
            "SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) "
            "FROM information_schema.tables WHERE table_schema = :db"
        ), {"db": url.database}).scalar()
    except Exception:
        pass

    git_commit = None
    try:
        git_commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=current_app.root_path, stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
    except Exception:
        pass

    return {
        "counts": counts,
        "db_size_mb": db_size_mb,
        "git_commit": git_commit,
        "python_version": platform.python_version(),
        "flask_version": flask.__version__,
        "sqlalchemy_version": sqlalchemy.__version__,
    }


@admin_bp.route("/desarrollo/cerrar-sesiones", methods=["POST"])
@login_required
def dev_force_logout():
    if not _require_developer():
        return redirect(url_for("dashboard.index"))
    import secrets
    current_app.config["SECRET_KEY"] = secrets.token_hex(32)
    flash("Sesiones invalidadas. Todos los usuarios deberán iniciar sesión de nuevo.", "success")
    return redirect(url_for("auth.logout"))


@admin_bp.route("/desarrollo/reset-bd", methods=["POST"])
@login_required
def dev_reset_database():
    if not _require_developer():
        return redirect(url_for("dashboard.index"))

    if request.form.get("confirm_text", "").strip() != "BORRAR":
        flash("Confirmación incorrecta: escribe BORRAR para continuar. La base de datos no se ha modificado.", "danger")
        return redirect(url_for("admin.config") + "#section-dev")

    # Importa todos los modelos para asegurar que sus tablas están registradas en db.metadata.
    import app.models  # noqa: F401
    from app.models.room import Room as _Room  # noqa: F401
    from app.models.presence import UserPresence  # noqa: F401
    from app.models.chat import ChatClear  # noqa: F401
    from werkzeug.security import generate_password_hash

    try:
        pw_hash = generate_password_hash("admin1234")
        # Cierra todas las conexiones del pool para liberar metadata locks que
        # bloquearían los TRUNCATE (conexiones inactivas con transacciones abiertas).
        db.engine.dispose()
        with db.engine.begin() as conn:
            conn.execute(db.text("SET FOREIGN_KEY_CHECKS=0"))
            for table in reversed(db.metadata.sorted_tables):
                # alembic_version refleja el estado del esquema, no de los datos: no tocar
                if table.name == "alembic_version":
                    continue
                # DELETE en lugar de TRUNCATE: evita el bloqueo de metadatos
                # que TRUNCATE necesita y que las transacciones abiertas de otras
                # peticiones concurrentes bloquean indefinidamente.
                conn.execute(db.text(f"DELETE FROM `{table.name}`"))
            conn.execute(db.text("SET FOREIGN_KEY_CHECKS=1"))
            # Insertar admin dentro del mismo bloque para que la BD nunca quede sin usuarios
            conn.execute(db.text(
                "INSERT INTO users (email, name, surname, password_hash, role, "
                "active, points, track_points, receive_emails, dev_access, show_substitute_public) "
                "VALUES ('admin@ies.es', 'Admin', 'Sistema', :pw, 'management', 1, 0, 0, 1, 1, 1)"
            ), {"pw": pw_hash})
    except Exception as e:
        current_app.logger.error("Error al resetear la base de datos: %s", e)
        flash("Error al resetear la base de datos.", "danger")
        return redirect(url_for("admin.config") + "#section-dev")

    flash("Base de datos reseteada. Inicia sesión con admin@ies.es / admin1234.", "success")
    return redirect(url_for("auth.logout"))
