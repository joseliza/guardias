# Guardias

**Autor:** José Manuel Lizana Jiménez  
**Departamento de Informática — IES Ciudad Jardín**  
**Licencia:** [GNU GPL v3](LICENSE)

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

## Flujo de desarrollo

- **`desarrollo`** — rama de trabajo diario. Todos los commits se hacen aquí, incluido trabajo a medias.
- **`main`** — refleja únicamente lo verificado y listo para producción.

Antes de fusionar a `main`, se verifica el cambio en local levantando la app con Docker Compose (mismo `Dockerfile` que producción):

```bash
git checkout desarrollo
# ... commits ...
docker compose up -d --build   # verificar el cambio

git checkout main
git merge desarrollo           # normalmente fast-forward
git push
```

## Despliegue en producción

El servidor mantiene un clon git real del repositorio (autenticado con una deploy key de solo lectura). Desplegar consiste en:

```bash
git push                                                    # (ya hecho arriba, sube main)
ssh usuario@servidor "bash /ruta/guardias/scripts/deploy.sh"
```

`scripts/deploy.sh` hace `git fetch` + `git reset --hard origin/main`, reconstruye la imagen Docker solo si cambiaron `Dockerfile`/`requirements.txt`/`docker-compose.yml` (si no, reinicia el contenedor) y verifica el commit desplegado y el código HTTP.
