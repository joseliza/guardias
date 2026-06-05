"""
Envío del resumen diario de guardias por correo. Genera un email personalizado
por profesor con sus tramos de guardia del día y las ausencias registradas en
cada uno. Incluye _reload_schedule() para actualizar el job de APScheduler
cuando cambia la configuración desde el panel de administración.
"""
from datetime import date


def send_daily_digest(app):
    """Envía el resumen diario a cada destinatario configurado."""
    with app.app_context():
        from app.config import _load_mail_config
        from app.extensions import mail
        from app.models.user import User
        from app.models.schedule import TeacherSchedule
        from app.models.absence import Absence
        from flask_mail import Message

        cfg = _load_mail_config()
        sched = cfg.get("MAIL_SCHEDULE", {})
        if not sched.get("enabled"):
            return

        today = date.today()
        day_idx = today.weekday()
        slots_cfg = app.config["TIME_SLOTS"]
        slot_map = {s["id"]: s for s in slots_cfg}

        recipient_roles = sched.get("recipient_roles") or ["teacher"]
        excluded_ids = sched.get("excluded_ids") or []

        users = (User.query
                 .filter_by(active=True, receive_emails=True)
                 .filter(User.role.in_(recipient_roles))
                 .filter(User.id.notin_(excluded_ids))
                 .order_by(User.surname, User.name)
                 .all())

        absences_today = Absence.query.filter_by(date=today).all()
        absences_by_slot = {}
        for a in absences_today:
            absences_by_slot.setdefault(a.slot_id, []).append(a)

        show_group    = sched.get("show_group", True)
        show_reason   = sched.get("show_reason", True)
        show_status   = sched.get("show_status", True)
        show_assigned = sched.get("show_assigned", True)
        show_room     = sched.get("show_room", True)

        subject_tpl = sched.get("subject") or "Guardias del {fecha}"
        intro       = sched.get("intro", "")
        fecha_str   = today.strftime("%d/%m/%Y")

        status_labels = {"pending": "Pendiente", "covered": "Cubierta", "returned": "Reincorporado"}

        sender = app.config.get("MAIL_DEFAULT_SENDER")

        for user in users:
            guard_slot_ids = sorted(
                e.slot_id for e in TeacherSchedule.query.filter_by(
                    teacher_id=user.id,
                    day_of_week=day_idx,
                    is_guard_slot=True,
                ).all()
            )

            subject = (subject_tpl
                       .replace("{fecha}", fecha_str)
                       .replace("{nombre}", user.name))

            lines = []
            if intro:
                lines += [intro, ""]

            if not guard_slot_ids:
                lines.append("No tienes tramos de guardia asignados hoy.")
            else:
                for slot_id in guard_slot_ids:
                    slot = slot_map.get(slot_id)
                    label = f"{slot['label']} ({slot['start']}–{slot['end']})" if slot else f"Tramo {slot_id}"
                    lines.append(f"\n{label}")
                    slot_absences = absences_by_slot.get(slot_id, [])
                    if not slot_absences:
                        lines.append("  Sin ausencias registradas.")
                        continue
                    for a in slot_absences:
                        parts = [f"  • {a.teacher.full_name}"]
                        guard = a.guard
                        if show_group and guard and guard.group:
                            parts.append(f"Grupo: {guard.group.name}")
                            if show_room and guard.room:
                                parts.append(f"Aula: {guard.room.name}")
                        if show_reason and a.reason:
                            parts.append(f"Motivo: {a.reason}")
                        if show_status and guard:
                            parts.append(f"Estado: {status_labels.get(guard.status, guard.status)}")
                        if show_assigned and guard:
                            records = guard.records.all()
                            if records:
                                names = ", ".join(r.teacher.full_name for r in records)
                                parts.append(f"Cubre: {names}")
                        lines.append(" — ".join(parts))

            body = "\n".join(lines)
            try:
                mail.send(Message(subject=subject, recipients=[user.email],
                                  sender=sender, body=body))
            except Exception as e:
                app.logger.error("Error enviando resumen a %s: %s", user.email, e)


def reload_schedule(app):
    """Elimina el job existente y lo re-añade si la programación está activa."""
    from app.extensions import scheduler
    from app.config import _load_mail_config

    try:
        scheduler.remove_job("daily_digest")
    except Exception:
        pass

    cfg = _load_mail_config()
    sched = cfg.get("MAIL_SCHEDULE", {})
    if not sched.get("enabled"):
        return

    time_str = sched.get("time", "07:30")
    try:
        hour, minute = map(int, time_str.split(":"))
    except ValueError:
        return

    days = sched.get("days") or [0, 1, 2, 3, 4]
    day_of_week = ",".join(str(d) for d in days)

    scheduler.add_job(
        send_daily_digest,
        "cron",
        id="daily_digest",
        hour=hour,
        minute=minute,
        day_of_week=day_of_week,
        args=[app],
        replace_existing=True,
    )
