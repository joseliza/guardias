---
name: project-smtp-relay
description: Configuración SMTP relay Google Workspace pendiente de completar
metadata:
  type: project
---

Pendiente de configurar el relay SMTP de Google Workspace para que la app envíe correos usando el dominio del instituto.

**Why:** Más robusto que contraseña de aplicación; el administrador tiene acceso a la consola de Google Workspace.

**Estado:** Completado y funcionando.

**Próximo paso:** Ir a Aplicaciones → Google Workspace → Gmail → Enrutamiento → buscar "Relay SMTP" y añadir la IP pública del servidor: `217.126.129.20`

**How to apply:** Una vez configurado el relay en Google, actualizar el `.env` del servidor (`/root/guardias/.env`) con:
```
MAIL_SERVER=smtp-relay.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=guardias@iesciudadjardin.es
MAIL_DEFAULT_SENDER=guardias@iesciudadjardin.es
```
Sin MAIL_PASSWORD (el relay autentica por IP).
