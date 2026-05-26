# Guardias

Aplicación web para la gestión de guardias en centros de educación secundaria. Permite registrar ausencias de profesores, asignar sustitutos, generar PDFs de tareas y hacer seguimiento de puntos por guardia cubierta.

## Funcionalidades principales

- **Panel de hoy** — vista por tramos horarios (continua o por pestañas) con estado de ausencias y guardias
- **Ausencias** — registro con motivo, grupo, aula y tareas para el sustituto
- **Guardias** — asignación automática y manual; asignación rápida desde el panel; eliminar asignaciones
- **Puntos** — sistema de puntuación por guardia cubierta con multiplicador por dificultad del grupo
- **PDFs** — hoja de tareas por ausencia y por tramo horario completo (con grupo y aula)
- **Aulas** — gestión de aulas 1-40; asignadas a cada grupo
- **Extraescolares** — registro de actividades con profesores acompañantes
- **Chat de incidencias** — en tiempo real en el panel principal (Socket.IO)
- **Pantalla sala de profesores** — vista de solo lectura para TV/proyector

## Tecnología

- **Backend**: Flask 3 · SQLAlchemy · Flask-Login · Flask-SocketIO · Flask-Migrate · Flask-Mail
- **Base de datos**: MySQL 8.4
- **Frontend**: Bootstrap 5 · Bootstrap Icons
- **PDFs**: fpdf2 con fuente DejaVuSans (soporte Unicode)
- **Despliegue**: Docker Compose

## Puesta en marcha

### 1. Clonar y configurar

```bash
git clone https://github.com/joseliza/guardias.git
cd guardias
cp .env.example .env
# Editar .env con los valores del centro
```

Variables clave en `.env`:

| Variable | Descripción |
|---|---|
| `SECRET_KEY` | Clave secreta Flask (generar con `python -c "import secrets; print(secrets.token_hex(32))"`) |
| `DATABASE_URL` | Cadena de conexión MySQL |
| `INSTITUTE_NAME` | Nombre del centro (aparece en cabeceras y PDFs) |
| `ADMIN_EMAIL` | Email del usuario administrador inicial |
| `MAIL_*` | Configuración SMTP para notificaciones |

### 2. Arrancar con Docker Compose

```bash
docker compose up -d --build
```

La aplicación queda disponible en `http://localhost:5050`.

### 3. Inicializar la base de datos

```bash
docker compose exec web flask db upgrade     # aplica migraciones
docker compose exec web python init_db.py    # crea admin, grupos y aulas 1-40
```

Credenciales iniciales: `ADMIN_EMAIL` / `admin1234` — **cambiar tras el primer acceso**.

### 4. (Opcional) Datos de prueba

```bash
docker compose exec web python seed_debug.py
```

Crea 80 profesores (`@prueba.es` / `prueba1234`), horarios con horas libres reales, y ausencias para hoy. Limpia toda la BD salvo grupos, aulas y el admin en cada ejecución.

## Estructura del proyecto

```
app/
├── models/         # User, Group, Room, Guard, Absence, Task, Schedule…
├── routes/         # dashboard, ausencias, guardias, admin, extraescolares…
├── templates/      # Jinja2 + Bootstrap
└── utils/          # guards.py (asignación), points.py, pdf helpers
init_db.py          # inicialización de grupos, aulas y admin
seed_debug.py       # datos de prueba
migrations/         # Alembic (Flask-Migrate)
```

## Roles de usuario

| Rol | Acceso |
|---|---|
| `management` | Panel completo, asignación, administración |
| `teacher` | Panel de hoy, registro de presencia, chat |
| `extracurricular` | Gestión de actividades extraescolares |
| `display` | Vista de pantalla sala de profesores (solo lectura) |

## Despliegue en producción

```bash
rsync -av --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
          --exclude='.env' --exclude='.claude' \
          ./ usuario@servidor:/ruta/guardias/

ssh usuario@servidor "cd /ruta/guardias && docker compose up -d --build"
```
