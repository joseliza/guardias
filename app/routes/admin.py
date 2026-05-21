import csv
import io
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app.extensions import db
from app.models.user import User
from app.models.group import Group
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
        group = Group(
            name=request.form["name"].strip(),
            level=request.form["level"].strip(),
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
        group.level = request.form["level"].strip()
        group.high_difficulty = request.form.get("high_difficulty") == "on"
        group.difficulty_multiplier = float(request.form.get("difficulty_multiplier", 1.0))
        group.active = request.form.get("active") == "on"
        db.session.commit()
        flash("Grupo actualizado.", "success")
        return redirect(url_for("admin.groups"))
    return render_template("admin/group_form.html", group=group)


# ── Importación CSV horarios ──────────────────────────────────────────────────
# Formato: email_profesor,dia(0-4),tramo(1-7),email_grupo_o_nombre,es_guardia(true/false)

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
