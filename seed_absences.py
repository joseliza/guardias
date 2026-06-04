"""
seed_absences.py — Genera ausencias de prueba para el día actual.

Borra las ausencias existentes de hoy y crea nuevas basadas en el horario
de los profesores con rol teacher. Máximo 6 ausencias por tramo.

Desdobles: si un grupo de 1º-4º ESO tiene varios profesores en el mismo tramo
(cada uno con su subgrupo), cada profesor se trata de forma independiente.

Uso:
    docker compose exec web python seed_absences.py
"""
import random
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.extensions import db
from app.models.user import User
from app.models.schedule import TeacherSchedule
from app.models.absence import Absence
from app.models.guard import Guard
from app.models.guard import GuardRecord
from app.utils.guards import auto_assign_pending_guards
from datetime import date

REASONS = [
    "Enfermedad",
    "Cita médica",
    "Formación",
    "Asuntos propios",
    "Guardia sindical",
    "",
]

MAX_PER_SLOT = 6


def run():
    app = create_app()
    with app.app_context():
        today = date.today()
        day_idx = today.weekday()

        if day_idx > 4:
            print(f"Hoy es {today} (fin de semana). No se generan ausencias.")
            return

        # ── Limpiar ausencias y guardias de hoy ──────────────────────────────
        guards_hoy = Guard.query.filter_by(date=today).all()
        guard_ids = [g.id for g in guards_hoy]
        if guard_ids:
            GuardRecord.query.filter(GuardRecord.guard_id.in_(guard_ids)).delete(synchronize_session=False)
        for g in guards_hoy:
            db.session.delete(g)
        absences_hoy = Absence.query.filter_by(date=today).all()
        for a in absences_hoy:
            db.session.delete(a)
        db.session.commit()
        print(f"[✓] Limpiadas {len(absences_hoy)} ausencias y {len(guards_hoy)} guardias de {today}.")

        # ── Profesores con rol teacher activos ───────────────────────────────
        teachers = User.query.filter_by(role="teacher", active=True).all()
        teacher_ids = {t.id for t in teachers}

        # ── Entradas de horario de hoy (solo clases, no guardias) ────────────
        entries = (TeacherSchedule.query
                   .filter_by(day_of_week=day_idx, is_guard_slot=False)
                   .filter(TeacherSchedule.teacher_id.in_(teacher_ids))
                   .all())

        slots_cfg = {s["id"]: s for s in app.config["TIME_SLOTS"]}
        configured_penalty = app.config.get("ABSENCE_PENALTY", -1.0)

        # Agrupar por tramo
        by_slot: dict[int, list] = {}
        for e in entries:
            slot = slots_cfg.get(e.slot_id, {})
            if slot.get("is_break"):
                continue
            by_slot.setdefault(e.slot_id, []).append(e)

        total = 0
        normal_slots = []

        for slot_id, slot_entries in sorted(by_slot.items()):
            random.shuffle(slot_entries)

            # Entre 1 y MAX_PER_SLOT ausencias, nunca más de 1/3 del claustro
            max_posible = min(MAX_PER_SLOT, max(1, len(slot_entries) // 3))
            n = random.randint(1, max(1, max_posible))
            seleccionados = slot_entries[:n]

            for entry in seleccionados:
                reason = random.choice(REASONS)

                # Comprobar que ya no hay ausencia registrada (por si se solapa)
                if Absence.query.filter_by(
                    teacher_id=entry.teacher_id, date=today, slot_id=slot_id
                ).first():
                    continue

                absence = Absence(
                    teacher_id=entry.teacher_id,
                    date=today,
                    slot_id=slot_id,
                    reason=reason,
                    reported_by_role="self",
                    reported_by_id=entry.teacher_id,
                    penalty_points=0.0,
                )
                db.session.add(absence)
                db.session.flush()

                db.session.add(Guard(
                    absence_id=absence.id,
                    date=today,
                    slot_id=slot_id,
                    group_id=entry.group_id,
                    status="pending",
                ))
                total += 1

            if seleccionados:
                normal_slots.append(slot_id)

        db.session.commit()
        print(f"[✓] Generadas {total} ausencias para {today}.")

        # ── Auto-asignación ──────────────────────────────────────────────────
        assigned = 0
        pending = 0
        for slot_id in normal_slots:
            result = auto_assign_pending_guards(today, slot_id)
            assigned += result["assigned"]
            pending += result["pending"]

        print(f"[✓] Auto-asignación: {assigned} guardias cubiertas, {pending} pendientes.")


if __name__ == "__main__":
    run()
