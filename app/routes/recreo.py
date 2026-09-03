"""
Blueprint de guardias de recreo. Gestiona el catálogo de zonas y las
asignaciones diarias zona→profesor con rotación automática o manual.
"""
import calendar
from datetime import date, timedelta
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models.recreo import RecreoZone, RecreoAssignment
from app.models.user import User
from app.models.schedule import TeacherSchedule
from app.models.group import Group
from app.models.school_year import SchoolYear
from app.utils import _MESES
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


# ─── Informe de zonas asignadas ────────────────────────────────────────────────

def _weekdays_in_range(desde, hasta):
    """Lista de fechas de lunes a viernes entre desde y hasta, ambos inclusive."""
    days = []
    d = desde
    while d <= hasta:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _period_bounds(period, args):
    """Calcula (desde, hasta, etiqueta) del periodo del informe: semana, mes o curso."""
    if period == "mes":
        try:
            y_str, m_str = args.get("mes", "").split("-")
            y, m = int(y_str), int(m_str)
        except (ValueError, AttributeError):
            today = date.today()
            y, m = today.year, today.month
        desde = date(y, m, 1)
        hasta = date(y, m, calendar.monthrange(y, m)[1])
        label = f"{_MESES[m - 1].capitalize()} de {y}"
    elif period == "curso":
        year_id = args.get("year_id", type=int)
        year = SchoolYear.query.get(year_id) if year_id else None
        if not year:
            year = get_current_school_year()
        desde, hasta = year.start_date, year.end_date
        label = f"Curso {year.name}"
    else:
        period = "semana"
        try:
            iso_year, iso_week = args.get("semana", "").split("-W")
            desde = date.fromisocalendar(int(iso_year), int(iso_week), 1)
        except (ValueError, AttributeError):
            desde = _monday_of(date.today())
        hasta = desde + timedelta(days=4)
        label = f"Semana del {desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}"
    return desde, hasta, label, period


def _report_teachers():
    """Profesores con al menos una guardia de recreo asignada alguna vez (para el selector)."""
    return (
        db.session.query(User)
        .join(RecreoAssignment, RecreoAssignment.teacher_id == User.id)
        .filter(RecreoAssignment.zone_id.isnot(None))
        .distinct()
        .order_by(User.surname, User.name)
        .all()
    )


def _build_report_individual(teacher_id, desde, hasta):
    days = _weekdays_in_range(desde, hasta)
    assignments = (
        RecreoAssignment.query
        .filter(
            RecreoAssignment.teacher_id == teacher_id,
            RecreoAssignment.assignment_date.in_(days),
            RecreoAssignment.zone_id.isnot(None),
        )
        .join(RecreoZone, RecreoAssignment.zone_id == RecreoZone.id)
        .order_by(RecreoAssignment.assignment_date)
        .all()
    )
    return [{"date": a.assignment_date, "zone": a.zone, "is_manual": a.is_manual} for a in assignments]


def _build_report_general(desde, hasta):
    days = _weekdays_in_range(desde, hasta)
    if not days:
        return []
    assignments = (
        RecreoAssignment.query
        .filter(
            RecreoAssignment.assignment_date.in_(days),
            RecreoAssignment.zone_id.isnot(None),
        )
        .join(RecreoZone, RecreoAssignment.zone_id == RecreoZone.id)
        .order_by(RecreoAssignment.assignment_date, RecreoZone.display_order, RecreoZone.name)
        .all()
    )
    by_date = {}
    for a in assignments:
        by_date.setdefault(a.assignment_date, []).append((a.zone, a.teacher, a.is_manual))
    return [{"date": d, "entries": by_date[d]} for d in days if d in by_date]


@recreo_bp.route("/informe")
@login_required
def informe():
    if not _require_management():
        return redirect(url_for("dashboard.index"))

    scope = request.args.get("scope", "general")
    if scope not in ("general", "individual"):
        scope = "general"
    teacher_id = request.args.get("teacher_id", type=int)
    period_in = request.args.get("period", "semana")

    desde, hasta, label, period = _period_bounds(period_in, request.args)

    teachers = _report_teachers()

    summary = None
    if scope == "individual" and teacher_id:
        teacher = next((t for t in teachers if t.id == teacher_id), None) or User.query.get(teacher_id)
        entries = _build_report_individual(teacher_id, desde, hasta)
        summary = {"kind": "individual", "teacher": teacher, "entries": entries, "total": len(entries)}
    elif scope == "general":
        days_data = _build_report_general(desde, hasta)
        total = sum(len(d["entries"]) for d in days_data)
        summary = {"kind": "general", "days": days_data, "total_days": len(days_data), "total": total}

    selected_year_id = request.args.get("year_id", type=int) or get_current_school_year().id
    iso = desde.isocalendar()

    return render_template(
        "admin/recreo_informe.html",
        scope=scope,
        period=period,
        teacher_id=teacher_id,
        desde=desde,
        hasta=hasta,
        label=label,
        teachers=teachers,
        years=SchoolYear.query.order_by(SchoolYear.start_date.desc()).all(),
        selected_year_id=selected_year_id,
        semana_value=f"{iso[0]}-W{iso[1]:02d}",
        mes_value=f"{desde.year}-{desde.month:02d}",
        summary=summary,
    )


@recreo_bp.route("/informe/imprimir")
@login_required
def informe_pdf():
    if not _require_management():
        return redirect(url_for("dashboard.index"))

    scope = request.args.get("scope", "general")
    if scope not in ("general", "individual"):
        scope = "general"
    teacher_id = request.args.get("teacher_id", type=int)
    desde, hasta, label, period = _period_bounds(request.args.get("period", "semana"), request.args)

    if scope == "individual":
        if not teacher_id:
            flash("Selecciona un profesor para el informe individual.", "danger")
            return redirect(url_for("recreo.informe", **request.args.to_dict()))
        teacher = User.query.get_or_404(teacher_id)
        entries = _build_report_individual(teacher_id, desde, hasta)
        return render_template(
            "admin/recreo_print.html",
            scope="individual", teacher=teacher, entries=entries, label=label,
        )

    days_data = _build_report_general(desde, hasta)
    return render_template(
        "admin/recreo_print.html",
        scope="general", days=days_data, label=label,
    )
