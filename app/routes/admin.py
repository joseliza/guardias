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
from app.extensions import db, oauth
from app.models.user import User
from app.models.group import Group
from app.models.room import Room
from app.models.schedule import TeacherSchedule

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _require_management():
    if not current_user.is_management:
        flash("Acceso restringido al equipo directivo.", "danger")
        return False
    return True


# ── Profesores ───────────────────────────────────────────────────────────────

@admin_bp.route("/profesores")
@login_required
def teachers():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    all_teachers = User.query.order_by(User.surname).all()
    all_emails_on = all(t.receive_emails for t in all_teachers if t.role != "display")
    return render_template("admin/teachers.html", teachers=all_teachers, all_emails_on=all_emails_on)


@admin_bp.route("/profesores/nuevo", methods=["GET", "POST"])
@login_required
def teacher_create():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        import secrets
        role = request.form.get("role", "teacher")
        user = User(
            email=request.form["email"].strip().lower(),
            name=request.form["name"].strip(),
            surname=request.form["surname"].strip(),
            role=role,
            track_points=request.form.get("track_points") == "on" and role == "management",
            receive_emails=True,
        )
        password = request.form.get("password") or secrets.token_hex(24)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        try:
            _send_welcome_email(user, password if request.form.get("password") else None)
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
        teacher.name = request.form["name"].strip()
        teacher.surname = request.form["surname"].strip()
        teacher.email = request.form["email"].strip().lower()
        teacher.role = request.form.get("role", "teacher")
        teacher.active = request.form.get("active") == "on"
        teacher.track_points = request.form.get("track_points") == "on" and teacher.role == "management"
        teacher.receive_emails = request.form.get("receive_emails") == "on"
        if request.form.get("password"):
            teacher.set_password(request.form["password"])
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
    if teacher.id == current_user.id:
        flash("No puedes eliminar tu propia cuenta.", "danger")
        return redirect(url_for("admin.teacher_edit", tid=tid))

    name = teacher.full_name
    # Eliminar registros dependientes manualmente (sin cascade en modelo)
    from app.models.guard import GuardRecord
    from app.models.absence import Absence
    from app.models.schedule import TeacherSchedule
    from app.models.guard import Guard

    GuardRecord.query.filter_by(teacher_id=tid).delete(synchronize_session=False)
    # Guardias generadas por ausencias de este profesor
    absence_ids = [a.id for a in Absence.query.filter_by(teacher_id=tid).all()]
    if absence_ids:
        Guard.query.filter(Guard.absence_id.in_(absence_ids)).delete(synchronize_session=False)
    Absence.query.filter_by(teacher_id=tid).delete(synchronize_session=False)
    Absence.query.filter_by(reported_by_id=tid).update({"reported_by_id": None}, synchronize_session=False)
    TeacherSchedule.query.filter_by(teacher_id=tid).delete(synchronize_session=False)
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
    db.session.delete(teacher)
    db.session.commit()
    flash(f"Profesor {name} eliminado permanentemente.", "success")
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
    all_groups = Group.query.order_by(Group.name).all()
    return render_template("admin/groups.html", groups=all_groups)


@admin_bp.route("/grupos/nuevo", methods=["GET", "POST"])
@login_required
def group_create():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        group = Group(
            name=request.form["name"].strip(),
            high_difficulty=request.form.get("high_difficulty") == "on",
            difficulty_multiplier=float(request.form.get("difficulty_multiplier", 1.0)),
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
        group.high_difficulty = request.form.get("high_difficulty") == "on"
        group.difficulty_multiplier = float(request.form.get("difficulty_multiplier", 1.0))
        group.active = request.form.get("active") == "on"
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
        name=f"{original.name} (copia)",
        high_difficulty=original.high_difficulty,
        difficulty_multiplier=original.difficulty_multiplier,
        active=original.active,
    )
    db.session.add(clone)
    db.session.commit()
    flash(f"Grupo clonado como '{clone.name}'. Edítalo para cambiar el nombre.", "success")
    return redirect(url_for("admin.groups"))


@admin_bp.route("/grupos/<int:gid>/borrar", methods=["POST"])
@login_required
def group_delete(gid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    group = Group.query.get_or_404(gid)
    if group.schedule_entries.count() > 0:
        flash(f"No se puede borrar '{group.name}': tiene horarios asignados. Desactívalo en su lugar.", "danger")
        return redirect(url_for("admin.groups"))
    db.session.delete(group)
    db.session.commit()
    flash(f"Grupo '{group.name}' eliminado.", "success")
    return redirect(url_for("admin.groups"))


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


@admin_bp.route("/horarios")
@login_required
def schedules():
    if not current_user.is_management and current_user.role != "display":
        return redirect(url_for("dashboard.index"))
    from flask import current_app
    from sqlalchemy import func
    from app.utils.school_year import get_current_school_year

    current_year = get_current_school_year()
    year_id = current_year.id

    teachers = (User.query
                .filter_by(active=True)
                .filter(User.role != "display")
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

    teacher_id = request.args.get("teacher_id", type=int)
    selected = User.query.get(teacher_id) if teacher_id else None

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
    groups = GroupModel.query.filter_by(active=True).order_by(GroupModel.name).all()
    rooms  = RoomModel.query.filter_by(active=True).order_by(RoomModel.name).all()

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
                           availability_periods=availability_periods,
                           today=_date.today(),
                           current_year=current_year,
                           can_edit=current_user.is_management)


@admin_bp.route("/horarios/clonar-celda", methods=["POST"])
@login_required
def schedule_clone_cell():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.utils.school_year import get_current_school_year
    year_id = get_current_school_year().id

    teacher_id  = int(request.form["teacher_id"])
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
            existing.notes         = source.notes
        else:
            db.session.add(TeacherSchedule(
                teacher_id=teacher_id, day_of_week=d, slot_id=s,
                school_year_id=year_id,
                is_guard_slot=source.is_guard_slot,
                group_id=source.group_id,
                room_id=source.room_id,
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
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.utils.school_year import get_current_school_year
    year_id = get_current_school_year().id

    teacher_id = int(request.form["teacher_id"])
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
        group_id = request.form.get("group_id", type=int)
        if group_id:
            db.session.add(TeacherSchedule(
                teacher_id=teacher_id, day_of_week=day, school_year_id=year_id,
                slot_id=slot_id, is_guard_slot=False, group_id=group_id,
                room_id=room_id, notes=notes,
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


@admin_bp.route("/importar-horarios")
@login_required
def schedule_wizard():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    return render_template(
        "admin/schedule_wizard.html",
        drive_connected=bool(current_user.google_drive_token),
        drive_file_id=current_user.google_drive_file_id or "",
    )


@admin_bp.route("/importar-horarios/usuarios")
@login_required
def schedule_users():
    if not _require_management():
        return jsonify({"error": "No autorizado"}), 403
    users = [
        {"id": u.id, "full_name": u.full_name}
        for u in User.query.filter(User.active == True).order_by(User.surname, User.name).all()
        if u.role in ("teacher", "management", "extracurricular")
    ]
    return jsonify({"users": users})


@admin_bp.route("/importar-horarios/resolver", methods=["POST"])
@login_required
def schedule_resolve():
    """Recibe lista de nombres del Drive y devuelve el usuario del sistema más parecido."""
    import unicodedata

    def _norm(s):
        s = unicodedata.normalize("NFD", (s or "").lower())
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        return set(s.split())

    if not _require_management():
        return jsonify({"error": "No autorizado"}), 403

    names = (request.get_json(force=True, silent=True) or {}).get("names", [])
    teachers = [
        u for u in User.query.filter(User.active == True).all()
        if u.role in ("teacher", "management", "extracurricular")
    ]
    results = {}
    for name in names:
        target = _norm(name)
        best, best_score = None, 0.0
        for u in teachers:
            words = _norm(u.full_name)
            union = len(target | words)
            score = len(target & words) / union if union else 0.0
            if score > best_score:
                best_score = score
                best = u
        if best and best_score >= 0.4:
            results[name] = {"user_id": best.id, "user_name": best.full_name, "conf": round(best_score, 2)}
        else:
            results[name] = {"user_id": None, "user_name": None, "conf": 0.0}
    return jsonify(results)


@admin_bp.route("/importar-horarios/importar", methods=["POST"])
@login_required
def schedule_import():
    if not _require_management():
        return jsonify({"error": "No autorizado"}), 403

    from app.models.subject import Subject
    from app.models.room import Room
    from app.utils.school_year import get_current_school_year

    payload = request.get_json(force=True, silent=True) or {}
    rows_data = payload.get("rows", [])
    cmap = payload.get("mapping", {})
    day_offset = int(payload.get("day_offset", 1))
    prof_by_abrev = payload.get("prof_by_abrev", {})
    asig_dict = payload.get("asig_dict", {})
    aula_dict = payload.get("aula_dict", {})

    if not rows_data:
        return jsonify({"error": "No hay filas."}), 400

    # ── Crear profesores desde CSV de emails (opcional) ───────────────────────
    teachers_created = 0
    email_data = payload.get("email_data")
    if email_data:
        email_col = email_data.get("email_col", "")
        nombre_col = email_data.get("nombre_col", "")
        apellidos_col = email_data.get("apellidos_col", "")
        for erow in email_data.get("rows", []):
            email = (erow.get(email_col) or "").strip().lower()
            if not email or "@" not in email:
                continue
            if User.query.filter_by(email=email).first():
                continue
            nombre = (erow.get(nombre_col) or "").strip()
            apellidos = (erow.get(apellidos_col) or "").strip()
            u = User(email=email, name=nombre, surname=apellidos, role="teacher", receive_emails=True)
            u.set_password("cambiar123")
            db.session.add(u)
            teachers_created += 1
        if teachers_created:
            db.session.flush()

    def field(row, ftype):
        for col, ft in cmap.items():
            if ft == ftype and col in row:
                return (row[col] or "").strip()
        return ""

    year_id = get_current_school_year().id
    created = skipped = subjects_created = groups_created = rooms_created = 0
    errors = []

    for row in rows_data:
        abrev_prof = field(row, "abrev_prof")
        teacher_id = prof_by_abrev.get(abrev_prof)
        if not teacher_id:
            errors.append(f"Profesor sin asignar: {abrev_prof}")
            skipped += 1
            continue

        try:
            dia = int(field(row, "dia") or 0) - day_offset
            tramo = int(field(row, "tramo") or 0)
        except ValueError:
            skipped += 1
            continue

        if not (0 <= dia <= 4) or tramo < 1:
            skipped += 1
            continue

        abrev_asig = field(row, "abrev_asig")
        asig_name = (asig_dict.get(abrev_asig) or abrev_asig).strip() or None
        subject = None
        if asig_name:
            subject = Subject.query.filter_by(name=asig_name).first()
            if not subject:
                subject = Subject(name=asig_name, abbreviation=abrev_asig or None)
                db.session.add(subject)
                db.session.flush()
                subjects_created += 1

        grupo_name = field(row, "grupo")
        group = None
        if grupo_name:
            group = Group.query.filter_by(name=grupo_name).first()
            if not group:
                group = Group(name=grupo_name)
                db.session.add(group)
                db.session.flush()
                groups_created += 1

        abrev_aula = field(row, "abrev_aula")
        aula_name = (aula_dict.get(abrev_aula) or abrev_aula).strip() or None
        room = None
        if aula_name:
            room = Room.query.filter_by(name=aula_name).first()
            if not room:
                room = Room(name=aula_name)
                db.session.add(room)
                db.session.flush()
                rooms_created += 1

        existing = TeacherSchedule.query.filter_by(
            teacher_id=teacher_id, day_of_week=dia,
            slot_id=tramo, school_year_id=year_id,
        ).first()
        if existing:
            skipped += 1
            continue

        db.session.add(TeacherSchedule(
            teacher_id=teacher_id,
            group_id=group.id if group else None,
            subject_id=subject.id if subject else None,
            room_id=room.id if room else None,
            day_of_week=dia,
            slot_id=tramo,
            is_guard_slot=False,
            school_year_id=year_id,
        ))
        created += 1

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "created": created, "skipped": skipped, "errors": errors,
        "teachers_created": teachers_created,
        "subjects_created": subjects_created,
        "groups_created": groups_created,
        "rooms_created": rooms_created,
    })


@admin_bp.route("/horarios/plantilla-csv")
@login_required
def schedule_csv_template():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from flask import Response
    content = "email_profesor,dia,tramo,grupo,es_guardia\nana.garcia@iesciudadjardin.es,0,1,1A Bach,false\nana.garcia@iesciudadjardin.es,0,3,,true\n"
    return Response(
        "﻿" + content,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=plantilla_horarios.csv"},
    )


@admin_bp.route("/carga-datos", methods=["GET", "POST"])
@login_required
def data_load():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    if request.method == "GET":
        return render_template(
            "admin/data_load.html",
            drive_connected=bool(current_user.google_drive_token),
            drive_file_id=current_user.google_drive_file_id or "",
        )

    # POST: recibe JSON {mapping, rows} y devuelve JSON con resultado
    payload = request.get_json(force=True, silent=True) or {}
    mapping = payload.get("mapping", {})
    rows = payload.get("rows", [])

    if not rows:
        return jsonify({"error": "No hay filas para importar."}), 400

    active = {v for v in mapping.values() if v != "ignorar"}

    def field(row, ftype):
        for col, ft in mapping.items():
            if ft == ftype and col in row:
                return (row[col] or "").strip()
        return None

    created, skipped, errors = {}, 0, []

    # ── Profesores (cuando hay columna 'email') ───────────────────────────────
    if "email" in active:
        n = 0
        for row in rows:
            email = field(row, "email")
            if not email:
                skipped += 1
                continue
            email = email.lower()
            if User.query.filter_by(email=email).first():
                skipped += 1
                continue
            nombre = field(row, "nombre") or ""
            apellidos = field(row, "apellidos") or ""
            if not nombre and not apellidos:
                nc = field(row, "nombre_completo") or ""
                parts = nc.split(" ", 1)
                nombre = parts[0]
                apellidos = parts[1] if len(parts) > 1 else ""
            rol = field(row, "rol") or "teacher"
            if rol not in ("teacher", "management", "display", "extracurricular"):
                rol = "teacher"
            u = User(email=email, name=nombre, surname=apellidos, role=rol, receive_emails=True)
            u.set_password("cambiar123")
            db.session.add(u)
            n += 1
        if n:
            created["profesores"] = n

    # ── Horarios (cuando hay columnas 'dia' y 'tramo') ────────────────────────
    elif "dia" in active and "tramo" in active:
        from app.utils.school_year import get_current_school_year
        year_id = get_current_school_year().id
        n = 0
        for row in rows:
            email = field(row, "email")
            if not email:
                skipped += 1
                continue
            teacher = User.query.filter_by(email=email.lower()).first()
            if not teacher:
                skipped += 1
                errors.append(f"Profesor no encontrado: {email}")
                continue
            try:
                day = int(field(row, "dia") or 0)
                slot = int(field(row, "tramo") or 1)
            except ValueError:
                skipped += 1
                continue
            is_guard = (field(row, "es_guardia") or "false").lower() in ("true", "1", "si", "sí")
            group_name = field(row, "grupo") or ""
            group = None
            if group_name:
                group = Group.query.filter_by(name=group_name).first()
                if not group:
                    group = Group(name=group_name)
                    db.session.add(group)
                    db.session.flush()
            existing = TeacherSchedule.query.filter_by(
                teacher_id=teacher.id, day_of_week=day, slot_id=slot,
                school_year_id=year_id,
            ).first()
            if existing:
                skipped += 1
                continue
            db.session.add(TeacherSchedule(
                teacher_id=teacher.id,
                group_id=group.id if group else None,
                day_of_week=day,
                slot_id=slot,
                is_guard_slot=is_guard,
                school_year_id=year_id,
            ))
            n += 1
        if n:
            created["horarios"] = n

    else:
        return jsonify({"error": "No se reconoce el tipo de datos. Mapea al menos 'Email' para importar profesores, o 'Email + Día + Tramo' para importar horarios."}), 400

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

    return jsonify({"created": created, "skipped": skipped, "errors": errors})


@admin_bp.route("/horarios/disponibilidad/nueva", methods=["POST"])
@login_required
def availability_create():
    if not _require_management():
        return redirect(url_for("dashboard.index"))

    from datetime import date as _date
    from app.models.availability import AvailabilityPeriod, AvailabilityPeriodGroup

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

    period = AvailabilityPeriod(
        teacher_id=teacher_id,
        start_date=start_date,
        end_date=end_date,
        created_by_id=current_user.id,
    )
    db.session.add(period)
    db.session.flush()

    for gid in request.form.getlist("group_ids"):
        db.session.add(AvailabilityPeriodGroup(period_id=period.id, group_id=int(gid)))

    db.session.commit()
    flash("Periodo de disponibilidad para guardia creado.", "success")
    return redirect(url_for("admin.schedules", teacher_id=teacher_id))


@admin_bp.route("/horarios/disponibilidad/<int:period_id>/editar", methods=["POST"])
@login_required
def availability_edit(period_id):
    if not _require_management():
        return redirect(url_for("dashboard.index"))

    from datetime import date as _date
    from app.models.availability import AvailabilityPeriod, AvailabilityPeriodGroup

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

    db.session.commit()
    flash("Periodo de disponibilidad actualizado.", "success")
    return redirect(url_for("admin.schedules", teacher_id=teacher_id))


@admin_bp.route("/horarios/disponibilidad/<int:period_id>/eliminar", methods=["POST"])
@login_required
def availability_delete(period_id):
    if not _require_management():
        return redirect(url_for("dashboard.index"))

    from app.models.availability import AvailabilityPeriod, AvailabilityPeriodGroup

    period = AvailabilityPeriod.query.get_or_404(period_id)
    teacher_id = period.teacher_id
    AvailabilityPeriodGroup.query.filter_by(period_id=period.id).delete()
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
    "points_system_enabled": True,
    "blink_guard_alert": False,
    "presence_visible_to": "none",
    "presence_detail": "count",
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
    return render_template("admin/config.html",
                           mail_config=mail_config,
                           schedule_config=schedule_config,
                           general_config=general_config,
                           points_config=points_config,
                           users=users,
                           scorable_teachers=scorable_teachers,
                           help_content=help_content)


@admin_bp.route("/configuracion/general", methods=["POST"])
@login_required
def config_general():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    current = _read_mail_config()
    current["GENERAL"] = {
        "show_future_absences":        bool(request.form.get("show_future_absences")),
        "auto_justify_extracurricular": bool(request.form.get("auto_justify_extracurricular")),
        "points_system_enabled":       bool(request.form.get("points_system_enabled")),
        "blink_guard_alert":           bool(request.form.get("blink_guard_alert")),
        "presence_visible_to":         request.form.get("presence_visible_to", "none"),
        "presence_detail":             request.form.get("presence_detail", "count"),
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

    # Por defecto: primer día del mes actual → hoy
    today = _date.today()
    default_desde = today.replace(day=1).isoformat()
    default_hasta = today.isoformat()

    desde_str = request.args.get("desde", default_desde)
    hasta_str = request.args.get("hasta", default_hasta)

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

    return render_template("admin/justification_report.html",
                           desde=desde_str, hasta=hasta_str, summary=summary)


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
    User.query.filter(User.role.notin_(["management", "display"])).update({"points": 0.0})
    # También resetear management con track_points
    User.query.filter_by(role="management", track_points=True).update({"points": 0.0})
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
    from app.utils.school_year import year_dates

    name = request.form.get("name", "").strip()
    if not name or '/' not in name:
        flash("Nombre de curso inválido.", "danger")
        return redirect(url_for("admin.school_years"))

    if SchoolYear.query.filter_by(name=name).first():
        flash(f"El curso {name} ya existe.", "warning")
        return redirect(url_for("admin.school_years"))

    start, end = year_dates(name)

    # Desactivar curso actual y crear el nuevo
    SchoolYear.query.update({"is_current": False})
    new_year = SchoolYear(name=name, start_date=start, end_date=end, is_current=True)
    db.session.add(new_year)
    db.session.flush()  # obtener new_year.id antes de recalcular

    # Recalcular puntos para el nuevo curso (estará en 0 al no tener datos aún)
    from app.utils.points import recalculate_points_for_year
    recalculate_points_for_year(new_year)

    db.session.commit()
    flash(f"Curso {name} iniciado. Puntos recalculados para este curso (0 al empezar).", "success")
    return redirect(url_for("admin.school_years"))


@admin_bp.route("/cursos/<int:year_id>/activar", methods=["POST"])
@login_required
def school_year_activate(year_id):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from app.models.school_year import SchoolYear

    year = SchoolYear.query.get_or_404(year_id)
    SchoolYear.query.update({"is_current": False})
    year.is_current = True

    from app.utils.points import recalculate_points_for_year
    recalculate_points_for_year(year)

    db.session.commit()
    flash(f"Curso {year.name} activado. Puntos recalculados para este curso.", "success")
    return redirect(url_for("admin.school_years"))
