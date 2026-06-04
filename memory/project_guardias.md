---
name: project-guardias
description: App Flask gestión de guardias IES Ciudad Jardín — arquitectura, roles, detalles operativos
metadata:
  type: project
---

App Flask para gestión de guardias en IES Ciudad Jardín (Sevilla). Dominio: `iesciudadjardin.es`. Servidor: `root@10.3.50.22:/root/guardias/` en Docker Compose.

**Roles:** `teacher`, `extracurricular`, `management`, `display`. Campo `track_points` (bool) en usuarios `management` para que acumulen puntos como profesores.

**Tramo recreo:** dura 30 minutos. Tenerlo en cuenta si hay cálculos de tiempo o puntos relacionados con guardias en ese tramo.

**How to apply:** Al tocar lógica de tramos horarios, duración de guardias o puntuación, recordar que el recreo es de 30 min (no 60).
