"""
Blueprint de administración (solo rol management). Gestiona el CRUD de profesores,
grupos y aulas, la importación masiva de profesores y horarios por CSV, y el
informe resumen del carnet de puntos con enlace al historial detallado por profesor.
"""
import csv
import io
import os
import re
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from app.extensions import db
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
    return render_template("admin/teacher_form.html", teacher=None)


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
        db.session.commit()
        flash("Profesor actualizado.", "success")
        return redirect(url_for("admin.teachers"))
    return render_template("admin/teacher_form.html", teacher=teacher)


# ── Importación CSV profesores ────────────────────────────────────────────────
# Formato esperado: email,nombre,apellidos,rol,contraseña

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


@admin_bp.route("/profesores/plantilla-csv")
@login_required
def teachers_csv_template():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from flask import Response
    content = "email,nombre,apellidos,rol,contraseña\nana.garcia@iesciudadjardin.es,Ana,García,teacher,\npedro.lopez@iesciudadjardin.es,Pedro,López,management,\n"
    return Response(
        "﻿" + content,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=plantilla_profesores.csv"},
    )


@admin_bp.route("/profesores/importar-csv", methods=["GET", "POST"])
@login_required
def import_teachers_csv():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        file = request.files.get("csv_file")
        if not file:
            flash("Selecciona un fichero CSV.", "warning")
            return redirect(request.url)
        stream = io.StringIO(file.stream.read().decode("utf-8-sig"))
        reader = csv.DictReader(stream)
        created, skipped = 0, 0
        for row in reader:
            email = row.get("email", "").strip().lower()
            if not email or User.query.filter_by(email=email).first():
                skipped += 1
                continue
            user = User(
                email=email,
                name=row.get("nombre", "").strip(),
                surname=row.get("apellidos", "").strip(),
                role=row.get("rol", "teacher").strip() or "teacher",
                receive_emails=True,
            )
            user.set_password(row.get("contraseña", "cambiar123"))
            db.session.add(user)
            created += 1
        db.session.commit()
        flash(f"Importados: {created} profesores. Omitidos: {skipped}.", "success")
        return redirect(url_for("admin.teachers"))
    return render_template("admin/import_csv.html", target="profesores")


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
        room_id = request.form.get("room_id") or None
        group = Group(
            name=request.form["name"].strip(),
            high_difficulty=request.form.get("high_difficulty") == "on",
            difficulty_multiplier=float(request.form.get("difficulty_multiplier", 1.0)),
            room_id=int(room_id) if room_id else None,
        )
        db.session.add(group)
        db.session.commit()
        flash("Grupo creado.", "success")
        return redirect(url_for("admin.groups"))
    rooms = Room.query.filter_by(active=True).order_by(Room.name).all()
    return render_template("admin/group_form.html", group=None, rooms=rooms)


@admin_bp.route("/grupos/<int:gid>/editar", methods=["GET", "POST"])
@login_required
def group_edit(gid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    group = Group.query.get_or_404(gid)
    if request.method == "POST":
        room_id = request.form.get("room_id") or None
        group.name = request.form["name"].strip()
        group.high_difficulty = request.form.get("high_difficulty") == "on"
        group.difficulty_multiplier = float(request.form.get("difficulty_multiplier", 1.0))
        group.active = request.form.get("active") == "on"
        group.room_id = int(room_id) if room_id else None
        db.session.commit()
        flash("Grupo actualizado.", "success")
        return redirect(url_for("admin.groups"))
    rooms = Room.query.filter_by(active=True).order_by(Room.name).all()
    return render_template("admin/group_form.html", group=group, rooms=rooms)


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
        room_id=original.room_id,
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
    if room.groups:
        flash(f"No se puede borrar '{room.name}': tiene grupos asignados. Desactívala o reasigna los grupos.", "danger")
        return redirect(url_for("admin.rooms"))
    db.session.delete(room)
    db.session.commit()
    flash(f"Aula '{room.name}' eliminada.", "success")
    return redirect(url_for("admin.rooms"))


@admin_bp.route("/horarios")
@login_required
def schedules():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    from flask import current_app

    teachers = (User.query
                .filter_by(active=True)
                .filter(User.role.notin_(["management", "display"]))
                .order_by(User.surname, User.name)
                .all())

    teacher_id = request.args.get("teacher_id", type=int)
    selected = User.query.get(teacher_id) if teacher_id else None

    slots_cfg = current_app.config["TIME_SLOTS"]
    days = current_app.config["DAYS_OF_WEEK"]
    schedule_grid = None
    if selected:
        entries = TeacherSchedule.query.filter_by(teacher_id=selected.id).all()
        entry_map = {(e.day_of_week, e.slot_id): e for e in entries}
        schedule_grid = []
        for s in slots_cfg:
            row = {"slot": s, "days": [entry_map.get((d, s["id"])) for d in range(5)]}
            schedule_grid.append(row)

    return render_template("admin/schedules.html",
                           teachers=teachers,
                           selected=selected,
                           schedule_grid=schedule_grid,
                           days=days)


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


@admin_bp.route("/horarios/importar-csv", methods=["GET", "POST"])
@login_required
def import_schedule_csv():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        file = request.files.get("csv_file")
        if not file:
            flash("Selecciona un fichero CSV.", "warning")
            return redirect(request.url)
        stream = io.StringIO(file.stream.read().decode("utf-8-sig"))
        reader = csv.DictReader(stream)
        created, skipped = 0, 0
        for row in reader:
            teacher = User.query.filter_by(email=row.get("email_profesor", "").strip().lower()).first()
            if not teacher:
                skipped += 1
                continue
            day = int(row.get("dia", 0))
            slot = int(row.get("tramo", 1))
            is_guard = row.get("es_guardia", "false").lower() in ("true", "1", "si", "sí")
            group_name = row.get("grupo", "").strip()
            group = Group.query.filter_by(name=group_name).first() if group_name else None

            existing = TeacherSchedule.query.filter_by(
                teacher_id=teacher.id, day_of_week=day, slot_id=slot
            ).first()
            if existing:
                skipped += 1
                continue
            entry = TeacherSchedule(
                teacher_id=teacher.id,
                group_id=group.id if group else None,
                day_of_week=day,
                slot_id=slot,
                is_guard_slot=is_guard,
            )
            db.session.add(entry)
            created += 1
        db.session.commit()
        flash(f"Importados: {created} tramos. Omitidos: {skipped}.", "success")
        return redirect(url_for("admin.teachers"))
    return render_template("admin/import_csv.html", target="horarios")


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

MAIL_KEYS = ["MAIL_SERVER", "MAIL_PORT", "MAIL_USE_TLS", "MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_DEFAULT_SENDER", "MAIL_WELCOME_TEMPLATE"]


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
                           points_config=points_config,
                           users=users,
                           scorable_teachers=scorable_teachers,
                           help_content=help_content)


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
