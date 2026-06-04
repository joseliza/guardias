---
name: feedback-list-search
description: Toda página con listado de registros debe incluir un buscador rápido que filtre filas al escribir
metadata:
  type: feedback
---

Toda página que muestre una lista/tabla de registros debe incluir un campo de búsqueda en la parte superior que filtre las filas en tiempo real al escribir.

**Why:** El usuario lo pidió para profesores, grupos y aulas, y quiere que se aplique a cualquier lista futura.

**How to apply:** Usar el snippet reutilizable ya integrado en `base.html`: añadir un `<input class="table-filter form-control" data-target="#id-de-la-tabla" placeholder="Buscar...">` encima de la tabla. El JS en base.html lo activa automáticamente.
