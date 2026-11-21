# Farmaclaw

Agente de research para consejo farmacéutico dentro de Claude Code. Responde con información de **CIMA (AEMPS)** y **PubMed**, citando cada afirmación.

> Herramienta de apoyo para profesionales. No sustituye el juicio clínico ni las bases de datos de interacciones (Bot PLUS).

## Instalación (Ubuntu)
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # si no tienes uv
cd ~/farmaclaw
uv sync
```

Opcional, para que PubMed permita 10 peticiones/s en vez de 3 (añadir a `~/.bashrc`):
```bash
export NCBI_API_KEY=tu_clave     # https://www.ncbi.nlm.nih.gov/account/settings/
export NCBI_EMAIL=tu@correo
```

## Uso
```bash
tmux new -s farmaclaw
cd ~/farmaclaw && claude
```
La primera vez, aprueba los servidores MCP del proyecto (o revisa `/mcp`). Después pregunta, por ejemplo:

- "Usa el agente research: ¿interacción entre omeprazol y clopidogrel?"
- "¿Se puede tomar ibuprofeno durante la lactancia?"
- "¿Hay problemas de suministro de citicolina?"

Desde el iPhone: activa `/remote-control` en la sesión de tmux.

### Arranque automático (siempre disponible en el móvil)
Un servicio de systemd de usuario abre al encender la VM una sesión tmux `farmaclaw` con `claude --remote-control Farmaclaw`. Si la sesión ya existe, no hace nada.
```bash
# Instalación (una vez; requiere linger: `loginctl enable-linger $USER`)
mkdir -p ~/.config/systemd/user
ln -sf ~/farmaclaw/systemd/farmaclaw.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now farmaclaw.service

tmux attach -t farmaclaw                  # entrar a la sesión desde SSH
~/farmaclaw/scripts/arrancar.sh           # recrearla a mano si la cerraste
systemctl --user status farmaclaw         # ver el estado del servicio
```

## Comandos rápidos (pensados para el móvil)
| Comando | Ejemplo | Qué hace |
|---|---|---|
| `/interaccion` | `/interaccion omeprazol clopidogrel` | Lee la 4.5 de todos los fármacos; mecanismo, relevancia y manejo |
| `/embarazo` | `/embarazo ibuprofeno tercer trimestre` | Ficha 4.6 + metaanálisis recientes |
| `/lactancia` | `/lactancia sertralina` | Ficha 4.6 + datos de paso a leche |
| `/dosis` | `/dosis ibuprofeno niños 15 kg` | Ficha 4.2, con cálculo por peso y ajustes renal/hepático |
| `/suministro` | `/suministro citicolina` | Problemas activos, fechas y alternativas comercializadas |

Están en `.claude/commands/` y todos delegan en el agente `research`.

## Herramientas
| Servidor | Herramienta | Qué hace |
|---|---|---|
| cima | `buscar_medicamento` | Busca por nombre, principio activo o código nacional (CN) |
| cima | `ficha_tecnica_seccion` | Una sección de la ficha técnica (4.2, 4.5, "interacciones"…) |
| cima | `prospecto_seccion` | Una sección del prospecto (1-6) |
| cima | `problemas_suministro` | Estado y fechas por presentación |
| cima | `notas_seguridad` | Notas informativas de seguridad de la AEMPS |
| cima | `materiales_informativos` | Guías de seguridad para profesionales y pacientes |
| pubmed | `buscar_articulos` | Búsqueda con filtros de tipo y año |
| pubmed | `obtener_resumen` | Abstract, MeSH, DOI y enlace a PMC |

## Pruebas
```bash
uv run python mcp/cima/test_cima.py
uv run python mcp/pubmed/test_pubmed.py
claude mcp list
```

## Estructura
```
.mcp.json                  servidores MCP del proyecto
CLAUDE.md                  contexto para Claude Code
.claude/agents/research.md agente farmacéutico investigador
mcp/cima/server.py         MCP de CIMA
mcp/pubmed/server.py       MCP de PubMed
```
