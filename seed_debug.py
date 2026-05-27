"""Datos de prueba: 80 profesores con horarios, ausencias de hoy y tareas.

Limpia toda la BD (excepto grupos, aulas y el usuario admin) y la regenera.
Ejecutar con:
    docker compose exec web python seed_debug.py
"""
import random
from datetime import date
from app import create_app
from app.extensions import db
from app.models.user import User
from app.models.group import Group
from app.models.room import Room
from app.models.schedule import TeacherSchedule
from app.models.absence import Absence
from app.models.guard import Guard, GuardRecord
from app.models.task import Task
from app.models.activity import ExtraActivity, ExtraActivityGroup, ExtraActivityTeacher

DEBUG_DOMAIN = "@prueba.es"
SEED = 42
random.seed(SEED)

NOMBRES = [
    "Ana", "Luis", "María", "Carlos", "Laura", "José", "Elena", "Pedro",
    "Carmen", "Antonio", "Isabel", "Francisco", "Marta", "Manuel", "Sara",
    "Javier", "Rosa", "Miguel", "Paula", "Alejandro", "Teresa", "Sergio",
    "Cristina", "Raúl", "Natalia", "David", "Pilar", "Alberto", "Silvia",
    "Fernando",
]

APELLIDOS = [
    "García", "Fernández", "López", "Martínez", "Sánchez", "Pérez", "Gómez",
    "Martín", "Jiménez", "Ruiz", "Hernández", "Díaz", "Moreno", "Álvarez",
    "Romero", "Alonso", "Gutiérrez", "Navarro", "Torres", "Domínguez",
    "Vázquez", "Ramos", "Gil", "Serrano", "Blanco", "Molina", "Morales",
    "Suárez", "Ortega", "Delgado", "Castro", "Ortiz", "Rubio", "Marín",
    "Sanz", "Iglesias", "Nuñez", "Medina", "Garrido", "Cortés",
]

TAREAS = [
    "Leer el tema 5 del libro de texto y responder las preguntas de comprensión.",
    "Realizar los ejercicios del 1 al 10 de la página 78.",
    "Copiar y aprender los apuntes del día anterior.",
    "Completar la ficha de repaso entregada la semana pasada.",
    "Leer en silencio el artículo fotocopiado y subrayar las ideas principales.",
    "Realizar el problema de la página 92, apartados a), b) y c).",
    "Estudiar el vocabulario de la unidad 6 para el próximo examen.",
    "Terminar el trabajo en grupo sobre el tema 4.",
    "Hacer el resumen del capítulo indicado en el libro de lectura.",
    "Repasar los conceptos del examen del jueves.",
    "Continuar con el ejercicio de redacción iniciado en clase.",
    "Resolver los problemas del apartado de autoevaluación.",
    "Leer en voz alta el texto de la página 65 por turnos.",
    "Completar la línea del tiempo de la unidad actual.",
    "Realizar el experimento de la guía práctica, apartado B.",
]

RAZONES_AUSENCIA = [
    "Visita médica",
    "Asuntos propios",
    "Formación docente",
    "Baja por enfermedad",
    "Gestión administrativa",
    "Cita previa",
]


def slug(nombre, apellido, n):
    base = f"{nombre.lower().replace(' ', '')}.{apellido.lower().replace(' ', '')}"
    return f"{base}{n}{DEBUG_DOMAIN}"


def main():
    app = create_app()
    with app.app_context():
        from flask import current_app
        slots_cfg = current_app.config["TIME_SLOTS"]
        non_break_slots = [s["id"] for s in slots_cfg if not s["is_break"]]
        all_days = list(range(5))  # 0=Lunes … 4=Viernes

        groups = Group.query.filter_by(active=True).all()
        if not groups:
            print("ERROR: No hay grupos. Ejecuta init_db.py primero.")
            return

        # ── Limpieza total (respeta grupos, aulas y management) ───────────────
        print("Limpiando base de datos...")
        GuardRecord.query.delete(synchronize_session=False)
        Guard.query.delete(synchronize_session=False)
        Task.query.delete(synchronize_session=False)
        Absence.query.delete(synchronize_session=False)
        TeacherSchedule.query.delete(synchronize_session=False)
        ExtraActivityTeacher.query.delete(synchronize_session=False)
        ExtraActivityGroup.query.delete(synchronize_session=False)
        ExtraActivity.query.delete(synchronize_session=False)
        User.query.filter(User.role != "management").delete(synchronize_session=False)
        db.session.commit()
        print("Limpieza completada.")

        # Asignar aulas a grupos que no tengan
        rooms = Room.query.filter_by(active=True).all()
        if rooms:
            used_rooms = set()
            for g in groups:
                if g.room_id is None:
                    available = [r for r in rooms if r.id not in used_rooms] or rooms
                    g.room_id = random.choice(available).id
                    used_rooms.add(g.room_id)
            db.session.commit()

        # ── Profesores ────────────────────────────────────────────────────────
        combos = [(n, a) for n in NOMBRES for a in APELLIDOS]
        random.shuffle(combos)
        combos = combos[:80]

        debug_teachers = []
        for i, (nombre, apellido) in enumerate(combos):
            teacher = User(
                email=slug(nombre, apellido, i),
                name=nombre,
                surname=apellido,
                role="teacher",
                points=round(random.uniform(0, 15), 2),
            )
            teacher.set_password("prueba1234")
            db.session.add(teacher)
            debug_teachers.append(teacher)

        db.session.flush()
        db.session.commit()
        print(f"Profesores creados: {len(debug_teachers)}")

        # ── Horarios ──────────────────────────────────────────────────────────
        # Primero determinar exactamente qué 5 profesores hacen guardia en cada tramo
        guard_set = set()  # (teacher_id, day, slot_id)
        for day in all_days:
            for slot_id in non_break_slots:
                for t in random.sample(debug_teachers, k=5):
                    guard_set.add((t.id, day, slot_id))

        schedules_created = 0
        for teacher in debug_teachers:
            # Tramos que NO son de guardia para este profesor
            non_guard_slots = [
                (day, sid)
                for day in all_days
                for sid in non_break_slots
                if (teacher.id, day, sid) not in guard_set
            ]
            # Solo ~18 de esos tramos llevan clase; el resto queda libre de verdad
            n_classes = min(len(non_guard_slots), random.randint(15, 20))
            class_slots = set(map(tuple, random.sample(non_guard_slots, k=n_classes)))

            for day in all_days:
                for slot_id in non_break_slots:
                    if (teacher.id, day, slot_id) in guard_set:
                        db.session.add(TeacherSchedule(
                            teacher_id=teacher.id,
                            day_of_week=day,
                            slot_id=slot_id,
                            is_guard_slot=True,
                        ))
                        schedules_created += 1
                    elif (day, slot_id) in class_slots:
                        db.session.add(TeacherSchedule(
                            teacher_id=teacher.id,
                            group_id=random.choice(groups).id,
                            day_of_week=day,
                            slot_id=slot_id,
                            is_guard_slot=False,
                        ))
                        schedules_created += 1
                    # else: hora libre — sin entrada, aparecerá en pool secundario

        db.session.commit()
        print(f"Tramos de horario creados: {schedules_created}")

        # ── Ausencias de hoy con guardias y tareas ────────────────────────────
        today = date.today()
        day_idx = today.weekday()

        if day_idx > 4:
            print("Hoy es fin de semana — no se crean ausencias.")
            return

        # Máximo 6 ausencias por tramo
        slot_absence_count = {sid: 0 for sid in non_break_slots}

        absent_teachers = random.sample(debug_teachers, k=min(18, len(debug_teachers)))
        absences_created = 0
        tasks_created = 0

        for teacher in absent_teachers:
            # Solo tramos donde el profesor tiene clase real (con grupo asignado)
            class_entries = TeacherSchedule.query.filter_by(
                teacher_id=teacher.id,
                day_of_week=day_idx,
                is_guard_slot=False,
            ).filter(TeacherSchedule.group_id.isnot(None)).all()

            # Filtrar los que aún no han alcanzado el límite de 6
            available = [e for e in class_entries if slot_absence_count[e.slot_id] < 6]
            if not available:
                continue

            n_slots = random.randint(1, 3)
            chosen = random.sample(available, k=min(n_slots, len(available)))

            for entry in chosen:
                slot_id = entry.slot_id
                group_id = entry.group_id

                absence = Absence(
                    teacher_id=teacher.id,
                    date=today,
                    slot_id=slot_id,
                    reason=random.choice(RAZONES_AUSENCIA),
                    reported_by_role="self",
                    reported_by_id=teacher.id,
                )
                db.session.add(absence)
                db.session.flush()

                guard = Guard(
                    absence_id=absence.id,
                    date=today,
                    slot_id=slot_id,
                    group_id=group_id,
                    status="pending",
                )
                db.session.add(guard)
                slot_absence_count[slot_id] += 1
                absences_created += 1

                # Tareas: 1-2 por ausencia
                for _ in range(random.randint(1, 2)):
                    task = Task(
                        absence_id=absence.id,
                        group_id=group_id,
                        description=random.choice(TAREAS),
                    )
                    db.session.add(task)
                    tasks_created += 1

        db.session.commit()
        print(f"Ausencias creadas hoy: {absences_created}")
        print(f"Tareas creadas: {tasks_created}")
        print("Listo.")


if __name__ == "__main__":
    main()
