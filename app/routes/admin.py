import csv
import io
from flask import Blueprint, render_template, redirect, url_for, flash, request
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
    return render_template("admin/teachers.html", teachers=all_teachers)


@admin_bp.route("/profesores/nuevo", methods=["GET", "POST"])
@login_required
def teacher_create():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        user = User(
            email=request.form["email"].strip().lower(),
            name=request.form["name"].strip(),
            surname=request.form["surname"].strip(),
            role=request.form.get("role", "teacher"),
        )
        user.set_password(request.form["password"])
        db.session.add(user)
        db.session.commit()
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
        if request.form.get("password"):
            teacher.set_password(request.form["password"])
        db.session.commit()
        flash("Profesor actualizado.", "success")
        return redirect(url_for("admin.teachers"))
    return render_template("admin/teacher_form.html", teacher=teacher)


# ── Importación CSV profesores ────────────────────────────────────────────────
# Formato esperado: email,nombre,apellidos,rol,contraseña

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
