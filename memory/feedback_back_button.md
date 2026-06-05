---
name: feedback-back-button
description: Toda plantilla nueva debe incluir botón "Atrás" arriba a la derecha en la cabecera
metadata:
  type: feedback
---

En toda plantilla nueva, el encabezado debe usar un `d-flex justify-content-between` con el título a la izquierda y el botón "Atrás" a la derecha.

**Patrón exacto:**
```html
<div class="d-flex justify-content-between align-items-center mb-3">
  <h4 class="mb-0"><i class="bi bi-ICONO"></i> Título de la página</h4>
  <a href="{{ url_for('blueprint.vista') }}" class="btn btn-outline-secondary btn-sm"><i class="bi bi-arrow-left"></i> Atrás</a>
</div>
```

**Why:** El usuario lo indicó explícitamente como convención de navegación consistente en toda la app.

**How to apply:** Aplicar en cualquier plantilla nueva desde el primer momento, antes de añadir el resto del contenido.
