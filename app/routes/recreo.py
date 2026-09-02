"""
Blueprint de guardias de recreo. Gestiona el catálogo de zonas y las
asignaciones semanales zona→profesor con rotación automática o manual.
"""
from datetime import date, timedelta
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models.recreo import RecreoZone, RecreoAssignment
from app.models.user import User
from app.models.schedule import TeacherSchedule
from app.models.group import Group
from app.utils.school_year import get_current_school_year

recreo_bp = Blueprint("recreo", __name__, url_prefix="/admin/recreo")

# Lunes de referencia para calcular el offset de rotación semanal
_ROTATION_REF = date(2000, 1, 3)


def _require_management():
    if not current_user.is_management:
        flash("Acceso restringido al equipo directivo.", "danger")
        return False
    return True


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _eligible_teachers(year_id):
    """Profesores con guardia de recreo (grupo G-Rec en slot 4) del curso activo."""
    grec = Group.query.filter_by(abbreviation="G-Rec").first()
    if not grec:
        return []
    teacher_ids = (
        db.session.query(TeacherSchedule.teacher_id)
        .filter_by(slot_id=4, group_id=grec.id, school_year_id=year_id)
        .distinct()
        .all()
    )
    ids = [r[0] for r in teacher_ids]
    return (
        User.query.filter(User.id.in_(ids), User.active == True)
        .order_by(User.surname, User.name)
        .all()
    )


def _auto_assignments(week_start: date, zones, teachers):
    """Devuelve {zone_id: teacher} para la rotación automática de esa semana."""
    if not teachers or not zones:
        return {}
    week_offset = (week_start - _ROTATION_REF).days // 7
    return {
        zone.id: teachers[(i + week_offset) % len(teachers)]
        for i, zone in enumerate(zones)
    }


def get_recreo_for_date(d: date):
    """Devuelve lista de (zone, teacher) para la fecha dada. Usado por dashboard y display."""
    week_start = _monday_of(d)
    year = get_current_school_year()
    assignments = (
        RecreoAssignment.query
        .filter_by(week_start=week_start, school_year_id=year.id)
        .join(RecreoZone, RecreoAssignment.zone_id == RecreoZone.id)
        .order_by(RecreoZone.display_order, RecreoZone.name)
        .all()
    )
    return [(a.zone, a.teacher) for a in assignments]


# ─── Zonas ────────────────────────────────────────────────────────────────────

@recreo_bp.route("/zonas")
@login_required
def zonas():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    zones = RecreoZone.query.order_by(RecreoZone.display_order, RecreoZone.name).all()
    return render_template("admin/recreo_zonas.html", zones=zones)


@recreo_bp.route("/zonas/nueva", methods=["POST"])
@login_required
def zona_create():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    name = request.form.get("name", "").strip()
    order = int(request.form.get("display_order") or 0)
    if not name:
        flash("El nombre es obligatorio.", "danger")
        return redirect(url_for("recreo.zonas"))
    z = RecreoZone(name=name, display_order=order)
    db.session.add(z)
    db.session.commit()
    flash(f"Zona «{z.name}» creada.", "success")
    return redirect(url_for("recreo.zonas", highlight=z.id))


@recreo_bp.route("/zonas/<int:zid>/editar", methods=["POST"])
@login_required
def zona_edit(zid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    z = RecreoZone.query.get_or_404(zid)
    z.name = request.form.get("name", "").strip() or z.name
    z.display_order = int(request.form.get("display_order") or 0)
    z.active = request.form.get("active") == "1"
    db.session.commit()
    flash(f"Zona «{z.name}» actualizada.", "success")
    return redirect(url_for("recreo.zonas", highlight=z.id))


@recreo_bp.route("/zonas/<int:zid>/eliminar", methods=["POST"])
@login_required
def zona_delete(zid):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    z = RecreoZone.query.get_or_404(zid)
    nombre = z.name
    try:
        db.session.delete(z)
        db.session.commit()
        flash(f"Zona «{nombre}» eliminada.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("No se puede eliminar: la zona tiene asignaciones. Desactívala en su lugar.", "danger")
    return redirect(url_for("recreo.zonas"))


# ─── Asignaciones semanales ───────────────────────────────────────────────────

@recreo_bp.route("/")
@login_required
def semana():
    if not _require_management():
        return redirect(url_for("dashboard.index"))

    week_str = request.args.get("semana")
    try:
        week_start = _monday_of(date.fromisoformat(week_str)) if week_str else _monday_of(date.today())
    except ValueError:
        week_start = _monday_of(date.today())

    week_end = week_start + timedelta(days=4)
    prev_week = week_start - timedelta(weeks=1)
    next_week = week_start + timedelta(weeks=1)

    year = get_current_school_year()
    zones = RecreoZone.query.filter_by(active=True).order_by(RecreoZone.display_order, RecreoZone.name).all()
    teachers = _eligible_teachers(year.id)

    existing = RecreoAssignment.query.filter_by(week_start=week_start, school_year_id=year.id).all()
    assigned_by_teacher = {a.teacher_id: a for a in existing}
    auto = _auto_assignments(week_start, zones, teachers)  # {zone_id: teacher}
    zones_by_id = {z.id: z for z in zones}
    auto_by_teacher = {t.id: zones_by_id[zid] for zid, t in auto.items() if zid in zones_by_id}

    rows = []
    for t in teachers:
        assgn = assigned_by_teacher.get(t.id)
        rows.append({
            "teacher": t,
            "assignment": assgn,
            "zone": assgn.zone if assgn else None,
            "auto_zone": auto_by_teacher.get(t.id),
            "is_manual": assgn.is_manual if assgn else False,
        })

    return render_template(
        "admin/recreo_semana.html",
        week_start=week_start,
        week_end=week_end,
        prev_week=prev_week,
        next_week=next_week,
        rows=rows,
        zones=zones,
        teachers=teachers,
        has_assignments=bool(assigned_by_teacher),
        year=year,
    )


@recreo_bp.route("/generar/<week_start_str>", methods=["POST"])
@login_required
def generar(week_start_str):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    try:
        week_start = date.fromisoformat(week_start_str)
    except ValueError:
        flash("Fecha inválida.", "danger")
        return redirect(url_for("recreo.semana"))

    year = get_current_school_year()
    zones = RecreoZone.query.filter_by(active=True).order_by(RecreoZone.display_order, RecreoZone.name).all()
    teachers = _eligible_teachers(year.id)
    auto = _auto_assignments(week_start, zones, teachers)

    if not auto:
        flash("No hay zonas activas o profesores elegibles (grupo G-Rec en recreo).", "warning")
        return redirect(url_for("recreo.semana", semana=week_start_str))

    # Borrar sólo las asignaciones automáticas; respetar las manuales
    existing = RecreoAssignment.query.filter_by(week_start=week_start, school_year_id=year.id).all()
    manual_zone_ids = {a.zone_id for a in existing if a.is_manual}
    for a in existing:
        if not a.is_manual:
            db.session.delete(a)
    db.session.flush()

    for zone_id, teacher in auto.items():
        if zone_id in manual_zone_ids:
            continue
        db.session.add(RecreoAssignment(
            week_start=week_start,
            zone_id=zone_id,
            teacher_id=teacher.id,
            school_year_id=year.id,
            is_manual=False,
        ))

    db.session.commit()
    flash("Asignación automática generada.", "success")
    return redirect(url_for("recreo.semana", semana=week_start_str))


@recreo_bp.route("/editar/<week_start_str>", methods=["POST"])
@login_required
def editar(week_start_str):
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    try:
        week_start = date.fromisoformat(week_start_str)
    except ValueError:
        flash("Fecha inválida.", "danger")
        return redirect(url_for("recreo.semana"))

    year = get_current_school_year()
    zones = RecreoZone.query.filter_by(active=True).order_by(RecreoZone.display_order, RecreoZone.name).all()
    teachers = _eligible_teachers(year.id)
    auto = _auto_assignments(week_start, zones, teachers)  # {zone_id: teacher}
    zones_by_id = {z.id: z for z in zones}
    auto_by_teacher = {t.id: zones_by_id[zid] for zid, t in auto.items() if zid in zones_by_id}

    RecreoAssignment.query.filter_by(week_start=week_start, school_year_id=year.id).delete()
    db.session.flush()

    seen_zones = set()
    for t in teachers:
        zid_str = request.form.get(f"teacher_{t.id}")
        if not zid_str:
            continue
        try:
            zid = int(zid_str)
        except ValueError:
            continue
        if zid in seen_zones:
            flash("Una zona no puede tener dos profesores a la vez.", "danger")
            db.session.rollback()
            return redirect(url_for("recreo.semana", semana=week_start_str))
        seen_zones.add(zid)
        auto_zone = auto_by_teacher.get(t.id)
        is_manual = auto_zone is None or auto_zone.id != zid
        db.session.add(RecreoAssignment(
            week_start=week_start,
            zone_id=zid,
            teacher_id=t.id,
            school_year_id=year.id,
            is_manual=is_manual,
        ))

    db.session.commit()
    flash("Asignación semanal guardada.", "success")
    return redirect(url_for("recreo.semana", semana=week_start_str))
