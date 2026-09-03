#!/bin/bash
# Actualiza /root/guardias al último commit de origin/main y reinicia el contenedor.
# Uso en el servidor: bash /root/guardias/scripts/deploy.sh
set -euo pipefail
cd /root/guardias

PREV=$(git rev-parse HEAD)
git fetch origin
git reset --hard origin/main
NEW=$(git rev-parse HEAD)

if git diff --name-only "$PREV" "$NEW" | grep -qE '^(Dockerfile|requirements\.txt|docker-compose\.yml)$'; then
  echo "Dependencias o Dockerfile cambiaron: reconstruyendo imagen..."
  docker compose up -d --build
else
  docker restart guardias-web-1
fi

sleep 3
echo "Desplegado: $(git rev-parse --short HEAD)"
echo "HEAD contenedor: $(docker exec guardias-web-1 cat /app/.git/refs/heads/main)"
curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:5050/
