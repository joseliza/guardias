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

### 0. Requisitos

- Docker y Docker Compose v2 (Docker Desktop en macOS/Windows, o Docker Engine + plugin `compose` en Linux).
- El demonio de Docker debe estar **arrancado** antes de cualquier comando `docker compose`. Si ves `Cannot connect to the Docker daemon`, abre Docker Desktop (o `sudo systemctl start docker` en Linux) y repite el comando.

### 1. Clonar y configurar

```bash
git clone git@github.com:joseliza/guardias.git
cd guardias
cp .env.example .env
# Editar .env con los valores del centro
```

Variables clave en `.env`:

| Variable | Descripción |
|---|---|
| `SECRET_KEY` | Clave secreta Flask (generar con `python -c "import secrets; print(secrets.token_hex(32))"`) |
| `DATABASE_URL` | Cadena de conexión MySQL (en local, `docker-compose.yml` ya la construye a partir de `MYSQL_PASSWORD`; no hace falta tocarla) |
| `INSTITUTE_NAME` | Nombre del centro (aparece en cabeceras y PDFs) |
| `ADMIN_EMAIL` | Email del usuario administrador del sistema: **protegido** (no se puede borrar ni renombrar desde el panel) y se crea/recrea automáticamente si no existe. Si se omite, se usa `admin@ies.es` |
| `MAIL_*` | Configuración SMTP para notificaciones |

### 2. Arrancar con Docker Compose

```bash
docker compose up -d --build
```

Comprueba que el servicio `db` llega a estar `healthy` antes de seguir (puede tardar unos segundos la primera vez):

```bash
docker compose ps
```

La aplicación queda disponible en `http://localhost:5050`.

### 3. Inicializar la base de datos

Solo hace falta la primera vez (o tras borrar el volumen `mysql_data` con `docker compose down -v`):

```bash
docker compose exec web python init_db.py    # crea tablas (db.create_all), admin, grupos y aulas 1-40
docker compose exec web flask db stamp head  # marca las migraciones como aplicadas, sin re-ejecutarlas
```

> **Importante:** en una base de datos vacía, `init_db.py` va **siempre antes** que `flask db upgrade`/`stamp`. El histórico de migraciones no crea el esquema completo desde cero (la primera migración asume que `groups` ya existe); es `init_db.py` quien crea todas las tablas con `db.create_all()` según los modelos actuales. Ejecutar `flask db upgrade` primero en una BD vacía falla con `Table 'groups' doesn't exist`.
>
> El usuario admin (`ADMIN_EMAIL`) también se recrea automáticamente en cada arranque de `web` si no existe en la base de datos (ver `run.py`), así que aunque te saltes este paso podrás entrar igualmente. Pero sin `init_db.py` no tendrás grupos ni aulas cargados.

Credenciales iniciales: `ADMIN_EMAIL` (o `admin@ies.es` si no se definió) / `admin1234` — **cambiar tras el primer acceso**.

### 4. (Opcional) Datos de prueba

```bash
docker compose exec web python seed_debug.py
```

Crea 80 profesores (`@prueba.es` / `prueba1234`), horarios con horas libres reales, y ausencias para hoy. Limpia toda la BD salvo grupos, aulas y el admin en cada ejecución.

### Solución de problemas

| Síntoma | Causa / solución |
|---|---|
| `Cannot connect to the Docker daemon` | Docker no está arrancado. Ábrelo y repite el comando. |
| El puerto 5050 ya está en uso | Cambia el mapeo `"5050:5000"` en `docker-compose.yml` o libera el puerto. |
| `db` no llega a `healthy` | Revisa `docker compose logs db`; normalmente basta esperar unos segundos más en el primer arranque. |
| Cambios de código no se reflejan | El contenedor `web` no recarga en caliente (`debug=False`). Ejecuta `docker compose restart web`. Si cambiaste `Dockerfile`, `requirements.txt` o `docker-compose.yml`, hace falta reconstruir con `docker compose up -d --build`. |
| No recuerdas la contraseña del admin | Se recrea sola en el siguiente arranque de `web` si borras ese usuario de la tabla `users` (contraseña `admin1234`). |
| `flask db upgrade` falla con `Table 'groups' doesn't exist` | Ejecutaste las migraciones antes que `init_db.py` en una BD vacía. Reset con `docker compose down -v`, vuelve a levantar y sigue el orden del paso 3 (`init_db.py` antes que `flask db stamp head`). |

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
