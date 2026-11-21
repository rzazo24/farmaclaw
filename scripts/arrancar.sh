#!/usr/bin/env bash
# Abre una sesión de tmux con Claude Code + Remote Control en el proyecto.
# Si la sesión ya existe no hace nada, así que se puede lanzar las veces que haga falta.
# Uso: scripts/arrancar.sh [nombre_sesion]   (por defecto: farmaclaw)
set -euo pipefail

SESION="${1:-farmaclaw}"
PROYECTO="$(cd "$(dirname "$0")/.." && pwd)"

if tmux has-session -t "$SESION" 2>/dev/null; then
    echo "La sesión tmux '$SESION' ya existe; no se hace nada."
    exit 0
fi

# Al arrancar la VM la red puede tardar: esperar hasta 2 min a llegar a Anthropic,
# porque sin red Remote Control no se conecta.
for _ in $(seq 60); do
    curl -s -o /dev/null -m 3 https://api.anthropic.com && break
    sleep 2
done

# bash -i para cargar ~/.bashrc (NCBI_API_KEY, PATH de uv y claude).
# Si Claude se cierra, la ventana queda en una shell en vez de desaparecer.
tmux new-session -d -s "$SESION" -c "$PROYECTO" \
    "bash -ic 'claude --remote-control Farmaclaw; exec bash -i'"
echo "Sesión tmux '$SESION' creada con Claude Code + Remote Control."
