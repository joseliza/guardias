# Resumen del proyecto — Guardias

App Flask para la gestión de guardias, ausencias, puntos, extraescolares y chat de incidencias en el IES Ciudad Jardín.

## Raíz del proyecto

- `run.py` → Punto de entrada: aplica eventlet/ProxyFix y arranca la app con SocketIO.
- `init_db.py` → Script de inicialización: crea tablas, usuario admin, aulas 1-40 y grupos por defecto.
- `seed_absences.py` → Genera ausencias y guardias de prueba para el día actual con auto-asignación.
- `seed_debug.py` → Regenera datos de prueba completos (80 profesores, horarios, ausencias y tareas).
- `requirements.txt` → Dependencias Python del proyecto.
- `Dockerfile` → Imagen Python 3.12-slim con dependencias MySQL/PDF y arranque vía `run.py`.
- `docker-compose.yml` → Orquesta servicios `db` (MySQL 8.4) y `web` (la app Flask).
- `.env.example` → Plantilla de variables de entorno (BD, SMTP, instituto, admin).
- `.dockerignore` / `.gitignore` / `.claudeignore` → Exclusiones de build/versionado/contexto de Claude.
- `README.md` → Documentación general: funcionalidades, stack y puesta en marcha.
- `especificaciones.txt` → Especificación funcional original del programa de guardias.
- `inicio_base_datos.txt` → Notas sobre el usuario admin inicial y los grupos/cursos a crear.
- `materias.pdf` → Listado de referencia de materias del centro.
- `LICENSE` → Licencia GNU GPL v3.
- `ficheros_carga/` → CSV/ODS de ejemplo para la carga masiva de profesores y horarios.

## app/ (núcleo)

- `__init__.py` → Factoría `create_app()`: registra extensiones, blueprints, filtros Jinja, context processors (puntos, curso actual, presencia, suplantación) y arranca el scheduler de correo.
- `config.py` → Configuración leída de `.env` y de `instance/mail_config.json`: BD, SMTP, OAuth Google, tramos horarios (`TIME_SLOTS`) y días lectivos.
- `extensions.py` → Instancias compartidas de SQLAlchemy, LoginManager, Migrate, Mail, SocketIO, OAuth y BackgroundScheduler.

## app/models/

- `school_year.py` → `SchoolYear`: curso escolar activo (uno marcado `is_current`).
- `user.py` → `User`: profesores/directivos/pantallas, credenciales, rol, puntos, sustituciones y preferencias de carga de datos.
- `group.py` → `Group`: grupo de alumnos, con multiplicador de dificultad para los puntos de guardia.
- `room.py` → `Room`: aulas físicas del centro (1-40), asignadas por tramo en `TeacherSchedule`.
- `subject.py` → `Subject`: asignaturas por curso escolar, con detección de tipos especiales de guardia.
- `schedule.py` → `TeacherSchedule`: horario semanal fijo de cada profesor (día, tramo, grupo, aula, materia).
- `raw_schedule.py` → `RawScheduleRow`: filas en bruto del CSV de horarios antes de resolver abreviaturas.
- `absence.py` → `Absence`: ausencia de un profesor en un tramo, con motivo, estado, justificación y penalización.
- `guard.py` → `Guard` / `GuardRecord`: guardia generada por una ausencia y los registros de profesores que la cubren.
- `task.py` → `Task`: tarea dejada por el profesor ausente para su grupo.
- `activity.py` → `ExtraActivity` / `ExtraActivityGroup` / `ExtraActivityTeacher`: actividades extraescolares, grupos participantes y profesores acompañantes.
- `availability.py` → `AvailabilityPeriod` / `AvailabilityPeriodGroup`: periodos en que un profesor ofrece sus horas de clase para cubrir guardias.
- `chat.py` → `ChatMessage` / `ChatClear`: mensajes del chat de incidencias y registro de limpiezas.
- `presence.py` → `UserPresence`: última conexión de cada usuario (heartbeat de presencia).
- `__init__.py` → Importa y expone todos los modelos para `db.create_all()` y migraciones.

## app/routes/

- `auth.py` → Login/logout con email+contraseña y OAuth de Google Workspace (dominio del instituto).
- `dashboard.py` → Panel principal: agrega por tramo ausencias, guardias, disponibles y chat del día.
- `absences.py` → Registro de ausencias, tareas asociadas, reincorporaciones y PDFs de tareas.
- `guards.py` → Asignación manual/automática de guardias, reparto de minutos/puntos, "Mi guardia" e historial CSV.
- `activities.py` → Gestión de actividades extraescolares y emails a profesores acompañantes.
- `admin.py` → Panel de administración (solo `management`): CRUD de profesores/grupos/aulas, importación CSV de horarios e informe de puntos.
- `chat.py` → Vistas e informe del chat de incidencias en tiempo real (general y por tramo).
- `display.py` → Pantalla táctil de sala de profesores (rol `display`) con eventos SocketIO en vivo.
- `presence.py` → API de heartbeat y consulta de usuarios conectados.
- `impersonate.py` → Modo "ver como" otro usuario (solo lectura) y modo pantalla para `management`.

## app/utils/

- `__init__.py` → Helpers transversales: `points_system_enabled` y formateo de fechas en castellano.
- `guards.py` → Lógica de asignación: pools de profesores disponibles por tramo y auto-asignación de guardias pendientes.
- `points.py` → Sumar/restar puntos por guardia o ausencia y recalcular el total anual por profesor.
- `school_year.py` → Obtención/creación del curso escolar activo y utilidades de nombres/fechas y grupos/materias por curso.
- `mail_digest.py` → Envío del resumen diario de guardias por correo y recarga del job programado (APScheduler).
- `google_drive.py` → Acceso a Google Sheets/Drive vía refresh token OAuth para la importación de horarios.

## app/templates/

- `base.html` → Plantilla base (navbar, mensajes flash, bloques comunes).
- `help.html` → Página de ayuda del sistema.
- `auth/` → Formulario de login.
- `dashboard/` → Panel principal del día por tramos.
- `absences/` → Alta/listado de ausencias, tareas y PDFs (individual y por tramo).
- `guards/` → Asignación de guardias, "mi guardia", "mis puntos" e informe.
- `activities/` → Alta/edición de actividades extraescolares y plantilla de email de solicitud de tareas.
- `admin/` → CRUD de profesores/grupos/aulas/materias/cursos, configuración general, carga de datos e informes/justificantes.
- `chat/` → Canal de chat en vivo e informe imprimible.
- `display/` → Pantalla de sala de profesores y vista de impresión por tramo.

## app/static/

- `css/main.css` → Estilos propios de la aplicación (sobre Bootstrap 5).

## migrations/

- `alembic.ini`, `env.py`, `script.py.mako`, `README` → Configuración de Flask-Migrate/Alembic.
- `versions/` → Historial incremental de migraciones del esquema (cursos, sustitutos, disponibilidad, materias, desdobles, Google Drive, etc.).
