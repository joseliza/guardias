---
name: feedback-rsync
description: Procedimiento de despliegue al servidor de producción con rsync y reinicio de contenedores
metadata:
  type: feedback
---

Despliegue al servidor `root@10.3.50.22`, carpeta `/root/guardias/`.

**Comando rsync:**
```bash
rsync -av --exclude='.git' --exclude='__pycache__' --exclude='.env' --exclude='.claude/' \
  /home/joseliza/python-apps/guardias/ root@10.3.50.22:/root/guardias/
```

**Tras el rsync**, reiniciar contenedores si los cambios lo requieren (código Python, plantillas, migraciones…):
```bash
ssh root@10.3.50.22 "cd /root/guardias && docker compose restart web"
```
Si hay migraciones nuevas, ejecutarlas antes del restart:
```bash
ssh root@10.3.50.22 "cd /root/guardias && docker compose exec web flask db upgrade"
```

**Why:** El usuario lo indicó como procedimiento estándar de despliegue del proyecto.

**How to apply:** Usar siempre este flujo al subir cambios. Preguntar al usuario antes de ejecutar (ver [[feedback-deploy-prompt]]).
