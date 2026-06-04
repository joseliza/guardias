---
name: feedback-csv-templates
description: Toda pantalla de importación CSV debe incluir un botón para descargar la plantilla CSV de ejemplo
metadata:
  type: feedback
---

Toda pantalla de importación CSV debe incluir un botón "Descargar plantilla" que descargue un CSV de ejemplo con las cabeceras y una fila de muestra.

**Why:** El usuario lo pidió explícitamente para profesores y horarios, y quiere que se aplique a cualquier importación CSV que se añada en el futuro.

**How to apply:** Cuando se cree o modifique cualquier pantalla de importación CSV, añadir siempre una ruta `/plantilla` que devuelva un CSV con cabeceras y fila de ejemplo, y un botón de descarga visible en el formulario de importación.
