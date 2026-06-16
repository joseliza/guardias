_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
          "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
_DIAS_ABREV = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
_DIAS_LARGO = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def guard_assign_mode():
    """Devuelve el modo de reparto automático de guardias: 'scoring', 'count' o 'random'.
    Compatibilidad: si la config tiene la clave antigua 'points_system_enabled' y no
    tiene 'guard_assign_mode', deriva el modo del valor booleano legado."""
    try:
        from app.routes.admin import _read_mail_config, GENERAL_DEFAULTS
        gcfg = {**GENERAL_DEFAULTS, **_read_mail_config().get("GENERAL", {})}
        if "guard_assign_mode" in gcfg:
            return gcfg["guard_assign_mode"]
        # Migración desde config antigua
        return "scoring" if gcfg.get("points_system_enabled", True) else "count"
    except Exception:
        return "scoring"


def points_system_enabled():
    """El sistema de puntuación está activo solo cuando el modo de reparto es 'scoring'."""
    return guard_assign_mode() == "scoring"


def fecha_es(d, fmt="%A, %d de %B de %Y"):
    """Formatea una fecha en castellano. Soporta %A (día largo), %a (día abreviado),
    %B (mes largo), %d (día con cero), %Y (año)."""
    return (fmt
            .replace("%A", _DIAS_LARGO[d.weekday()])
            .replace("%a", _DIAS_ABREV[d.weekday()])
            .replace("%B", _MESES[d.month - 1])
            .replace("%d", f"{d.day:02d}")
            .replace("%Y", str(d.year)))
