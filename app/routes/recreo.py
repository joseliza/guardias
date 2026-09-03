"""
Blueprint de guardias de recreo. Gestiona el catálogo de zonas y las
asignaciones diarias zona→profesor con rotación automática o manual.
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

# Lunes de referencia para calcular el offset de rotación diaria
_ROTATION_REF = date(2000, 1, 3)


def _require_management():
    if not current_user.is_management:
        flash("Acceso restringido al equipo directivo.", "danger")
        return False
    return True


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _eligible_teachers_by_day(year_id):
    """Profesores con guardia de recreo (grupo G-Rec en slot 4) del curso activo,
    agrupados por día de la semana (0=Lunes … 4=Viernes) según su horario fijo."""
    grec = Group.query.filter_by(abbreviation="G-Rec").first()
    if not grec:
        return {}
    rows = (
        db.session.query(TeacherSchedule.teacher_id, TeacherSchedule.day_of_week)
        .filter_by(slot_id=4, group_id=grec.id, school_year_id=year_id)
        .distinct()
        .all()
    )
    ids_by_day = {}
    all_ids = set()
    for teacher_id, day_of_week in rows:
        ids_by_day.setdefault(day_of_week, set()).add(teacher_id)
        all_ids.add(teacher_id)

    teachers_by_id = {
        u.id: u
        for u in User.query.filter(User.id.in_(all_ids), User.active == True).all()
    }
    return {
        day_of_week: sorted(
            (teachers_by_id[tid] for tid in ids if tid in teachers_by_id),
            key=lambda t: (t.surname, t.name),
        )
        for day_of_week, ids in ids_by_day.items()
    }


def _teachers_union(teachers_by_day):
    """Lista ordenada y sin duplicados de todos los profesores elegibles en la semana,
    usada como filas de la tabla (una fila por profesor con guardia algún día)."""
    by_id = {}
    for day_teachers in teachers_by_day.values():
        for t in day_teachers:
            by_id[t.id] = t
    return sorted(by_id.values(), key=lambda t: (t.surname, t.name))


def _auto_assignments(d: date, zones, teachers):
    """Devuelve {zone_id: teacher} para la rotación automática del día concreto d.

    Si hay más zonas activas que profesores elegibles ese día, las zonas
    sobrantes quedan sin asignar en vez de repetir profesor (el módulo de la
    rotación repetiría al mismo profesor en dos zonas y violaría la
    restricción única assignment_date+teacher_id)."""
    if not teachers or not zones:
        return {}
    day_offset = (d - _ROTATION_REF).days
    n = min(len(zones), len(teachers))
    return {
        zones[i].id: teachers[(i + day_offset) % len(teachers)]
        for i in range(n)
    }


def get_recreo_for_date(d: date):
    """Devuelve lista de (zone, teacher) para la fecha dada. Usado por dashboard y display."""
    year = get_current_school_year()
    assignments = (
        RecreoAssignment.query
        .filter(
            RecreoAssignment.assignment_date == d,
            RecreoAssignment.school_year_id == year.id,
            RecreoAssignment.zone_id.isnot(None),
        )
        .join(RecreoZone, RecreoAssignment.zone_id == RecreoZone.id)
        .order_by(RecreoZone.display_order, RecreoZone.name)
        .all()
    )
    return [(a.zone, a.teacher) for a in assignments]


# ─── Zonas ──────────────────────────────────────────────────────────────────
# La gestión de zonas vive en Configuración → «Guardias de recreo»
# (admin.config, sección #section-recreo-zonas).

def _to_config_zonas(**kwargs):
    return redirect(url_for("admin.config", _anchor="section-recreo-zonas", **kwargs))


@recreo_bp.route("/zonas/nueva", methods=["POST"])
@login_required
def zona_create():
    if not _require_management():
        return redirect(url_for("dashboard.index"))
    name = request.form.get("name", "").strip()
    order = int(request.form.get("display_order") or 0)
    if not name:
        flash("El nombre es obligatorio.", "danger")
        return _to_config_zonas()
    z = RecreoZone(name=name, display_order=order)
    db.session.add(z)
    db.session.commit()
    flash(f"Zona «{z.name}» creada.", "success")
    return _to_config_zonas(highlight=z.id)


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
    return _to_config_zonas(highlight=z.id)


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
    return _to_config_zonas()


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

    days = [week_start + timedelta(days=i) for i in range(5)]
    week_end = days[-1]
    prev_week = week_start - timedelta(weeks=1)
    next_week = week_start + timedelta(weeks=1)

    year = get_current_school_year()
    zones = RecreoZone.query.filter_by(active=True).order_by(RecreoZone.display_order, RecreoZone.name).all()
    teachers_by_day = _eligible_teachers_by_day(year.id)
    teachers = _teachers_union(teachers_by_day)

    # Cargar todas las asignaciones de la semana de una sola consulta
    all_assignments = RecreoAssignment.query.filter(
        RecreoAssignment.assignment_date.in_(days),
        RecreoAssignment.school_year_id == year.id,
    ).all()
    # {(teacher_id, date): assignment}
    assigned = {(a.teacher_id, a.assignment_date): a for a in all_assignments}

    # Auto-rotación por día: {date: {teacher_id: zone}}, solo entre quienes tienen
    # guardia de recreo ese día concreto según su horario fijo.
    auto_by_day = {}
    duty_ids_by_day = {}
    for d in days:
        day_teachers = teachers_by_day.get(d.weekday(), [])
        duty_ids_by_day[d] = {t.id for t in day_teachers}
        auto_day = _auto_assignments(d, zones, day_teachers)  # {zone_id: teacher}
        auto_by_day[d] = {t.id: zones_by_id_lookup(zones, zid) for zid, t in auto_day.items()}

    rows = []
    for t in teachers:
        day_cells = {}
        for d in days:
            has_duty = t.id in duty_ids_by_day[d]
            assgn = assigned.get((t.id, d))
            auto_zone = auto_by_day[d].get(t.id)
            day_cells[d] = {
                "has_duty": has_duty,
                "assignment": assgn,
                "zone": assgn.zone if assgn else None,
                "auto_zone": auto_zone,
                "is_manual": assgn.is_manual if assgn else False,
            }
        rows.append({"teacher": t, "days": day_cells})

    return render_template(
        "admin/recreo_semana.html",
        week_start=week_start,
        week_end=week_end,
        days=days,
        prev_week=prev_week,
        next_week=next_week,
        rows=rows,
        zones=zones,
        teachers=teachers,
        has_assignments=bool(all_assignments),
        year=year,
    )


def zones_by_id_lookup(zones, zone_id):
    """Devuelve el objeto zone dado su id, buscando en la lista."""
    for z in zones:
        if z.id == zone_id:
            return z
    return None


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
    teachers_by_day = _eligible_teachers_by_day(year.id)

    if not zones or not teachers_by_day:
        flash("No hay zonas activas o profesores elegibles (grupo G-Rec en recreo).", "warning")
        return redirect(url_for("recreo.semana", semana=week_start_str))

    days = [week_start + timedelta(days=i) for i in range(5)]

    for d in days:
        auto = _auto_assignments(d, zones, teachers_by_day.get(d.weekday(), []))
        existing = RecreoAssignment.query.filter_by(
            assignment_date=d, school_year_id=year.id
        ).all()
        manual_zone_ids = {a.zone_id for a in existing if a.is_manual and a.zone_id is not None}
        # Profesores con una fila manual ese día (en cualquier zona, o "Sin guardia"
        # con zone_id=NULL): no se les debe asignar otra zona por rotación, o se
        # duplicaría la fila del profesor para esa fecha y saltaría la restricción única.
        manual_teacher_ids = {a.teacher_id for a in existing if a.is_manual}
        for a in existing:
            if not a.is_manual:
                db.session.delete(a)
        db.session.flush()
        for zone_id, teacher in auto.items():
            if zone_id in manual_zone_ids:
                continue
            if teacher.id in manual_teacher_ids:
                continue
            db.session.add(RecreoAssignment(
                assignment_date=d,
                zone_id=zone_id,
                teacher_id=teacher.id,
                school_year_id=year.id,
                is_manual=False,
            ))

    db.session.commit()
    flash("Asignación automática generada para toda la semana.", "success")
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
    teachers_by_day = _eligible_teachers_by_day(year.id)
    days = [week_start + timedelta(days=i) for i in range(5)]

    # Borrar todas las asignaciones de la semana y reconstruir
    RecreoAssignment.query.filter(
        RecreoAssignment.assignment_date.in_(days),
        RecreoAssignment.school_year_id == year.id,
    ).delete(synchronize_session=False)
    db.session.flush()

    for d in days:
        day_teachers = teachers_by_day.get(d.weekday(), [])
        auto = _auto_assignments(d, zones, day_teachers)  # {zone_id: teacher}
        auto_by_teacher = {t.id: z_id for z_id, t in auto.items()}
        seen_zones = set()
        for t in day_teachers:
            field = f"teacher_{t.id}_{d.isoformat()}"
            zid_str = request.form.get(field)
            if not zid_str:
                continue  # "No asignada" — no se guarda nada

            try:
                zid = int(zid_str)
            except ValueError:
                continue
            if zid in seen_zones:
                flash(f"Una zona no puede tener dos profesores el mismo día ({d.strftime('%d/%m')}).", "danger")
                db.session.rollback()
                return redirect(url_for("recreo.semana", semana=week_start_str))
            seen_zones.add(zid)
            auto_zone_id = auto_by_teacher.get(t.id)
            is_manual = auto_zone_id is None or auto_zone_id != zid
            db.session.add(RecreoAssignment(
                assignment_date=d,
                zone_id=zid,
                teacher_id=t.id,
                school_year_id=year.id,
                is_manual=is_manual,
            ))

    db.session.commit()
    flash("Asignación semanal guardada.", "success")
    return redirect(url_for("recreo.semana", semana=week_start_str))
