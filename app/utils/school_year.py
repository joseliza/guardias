"""
Utilidad para obtener el curso escolar activo y calcular nombres/fechas de cursos.
"""
from datetime import date


def year_name_for(d: date) -> str:
    """Devuelve '2025/2026' para cualquier fecha dentro de ese curso."""
    if d.month >= 9:
        return f"{d.year}/{d.year + 1}"
    return f"{d.year - 1}/{d.year}"


def year_dates(name: str):
    """Dado '2025/2026' devuelve (date(2025,9,1), date(2026,6,30))."""
    start_year = int(name.split('/')[0])
    return date(start_year, 9, 1), date(start_year + 1, 6, 30)


def get_year_groups(year_id):
    """Devuelve los grupos activos del curso dado, ordenados por nombre."""
    from app.models.group import Group
    return Group.query.filter_by(school_year_id=year_id, active=True).order_by(Group.name).all()


def get_year_subjects(year_id):
    """Devuelve las materias del curso dado, ordenadas por nombre."""
    from app.models.subject import Subject
    return Subject.query.filter_by(school_year_id=year_id).order_by(Subject.name).all()


def get_current_school_year():
    """Devuelve el SchoolYear marcado como actual. Si no existe ninguno, lo crea automáticamente."""
    try:
        from flask import g
        if hasattr(g, '_current_school_year'):
            return g._current_school_year
    except RuntimeError:
        pass

    from app.models.school_year import SchoolYear
    from app.extensions import db

    sy = SchoolYear.query.filter_by(is_current=True).first()
    if not sy:
        name = year_name_for(date.today())
        sy = SchoolYear.query.filter_by(name=name).first()
        if not sy:
            start, end = year_dates(name)
            sy = SchoolYear(name=name, start_date=start, end_date=end, is_current=True)
            db.session.add(sy)
            db.session.commit()
        else:
            sy.is_current = True
            db.session.commit()

    try:
        from flask import g
        g._current_school_year = sy
    except RuntimeError:
        pass

    return sy
