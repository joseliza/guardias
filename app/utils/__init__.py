_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
          "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
_DIAS_ABREV = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
_DIAS_LARGO = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def points_system_enabled():
    """Lee de la configuración general si el sistema de puntuación está activo."""
    try:
        from app.routes.admin import _read_mail_config, GENERAL_DEFAULTS
        gcfg = {**GENERAL_DEFAULTS, **_read_mail_config().get("GENERAL", {})}
        return gcfg.get("points_system_enabled", True)
    except Exception:
        return True


def fecha_es(d, fmt="%A, %d de %B de %Y"):
    """Formatea una fecha en castellano. Soporta %A (día largo), %a (día abreviado),
    %B (mes largo), %d (día con cero), %Y (año)."""
    return (fmt
            .replace("%A", _DIAS_LARGO[d.weekday()])
            .replace("%a", _DIAS_ABREV[d.weekday()])
            .replace("%B", _MESES[d.month - 1])
            .replace("%d", f"{d.day:02d}")
            .replace("%Y", str(d.year)))
