# Farmaclaw

[![Pruebas](https://github.com/rzazo24/farmaclaw/actions/workflows/pruebas.yml/badge.svg)](https://github.com/rzazo24/farmaclaw/actions/workflows/pruebas.yml)

Agente de research para consejo farmacéutico dentro de Claude Code. Responde con información de **CIMA (AEMPS)**, **PubMed**, **LactMed** y **e-lactancia**, citando cada afirmación.

> [!WARNING]
> **Herramienta de apoyo para profesionales sanitarios. No es un producto sanitario.**
> - No sustituye el juicio clínico ni las bases de datos de referencia (p. ej. Bot PLUS para interacciones).
> - Las respuestas las genera un modelo de IA a partir de fuentes externas: pueden contener errores, omisiones o información desactualizada. Comprueba siempre la fuente enlazada.
> - La decisión clínica es siempre responsabilidad del profesional.
> - No introduzcas datos identificativos de pacientes.

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

### Índice de LactMed
LactMed se descarga completo del FTP del NCBI (su web bloquea accesos automáticos) y se guarda en `data/lactmed.json.gz` (no se sube a git). Crearlo o actualizarlo (~1 min):
```bash
uv run python mcp/pubmed/lactmed.py
```
Actualización automática mensual (temporizador systemd de usuario):
```bash
ln -sf ~/farmaclaw/systemd/farmaclaw-lactmed.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now farmaclaw-lactmed.timer
systemctl --user list-timers farmaclaw-lactmed.timer    # próxima ejecución
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
> Los servicios de `systemd/` asumen que el proyecto está en `~/farmaclaw`. Si lo clonas en otra ruta, ajusta `%h/farmaclaw` en los `.service`.

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
| `/lactancia` | `/lactancia sertralina` | e-lactancia + LactMed + ficha 4.6 |
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
| elactancia | `elactancia` | e-lactancia.org: nivel de riesgo en lactancia (0-3), comentario y alternativas |
| pubmed | `lactmed` | LactMed: uso en lactancia, niveles en leche, efectos en el lactante, alternativas |

## Pruebas
```bash
uv sync                          # instala también pytest (dependencia de desarrollo)
uv run pytest                    # todas: sin red + llamadas reales a las APIs (~10 s)
uv run pytest -m "not red"       # solo las que no usan red (instantáneas)
claude mcp list                  # los tres servidores deben salir "✔ Connected"
```
Las pruebas de LactMed con datos reales se saltan si no existe el índice local.

En GitHub Actions (`.github/workflows/pruebas.yml`) las pruebas sin red se ejecutan en cada push y PR; las de red, en cada push a `main`, cada lunes y a mano desde la pestaña *Actions*.

## Estructura
```
.mcp.json                        servidores MCP del proyecto
CLAUDE.md                        contexto para Claude Code
.claude/agents/research.md       agente farmacéutico investigador
.claude/commands/                comandos rápidos (/interaccion, /dosis, /lactancia…)
mcp/cima/server.py               MCP de CIMA (AEMPS)
mcp/pubmed/server.py             MCP de PubMed (+ herramienta LactMed)
mcp/pubmed/lactmed.py            descarga y búsqueda del índice local de LactMed
mcp/elactancia/server.py         MCP de e-lactancia
tests/                           pruebas pytest (sin red y con APIs reales)
scripts/arrancar.sh              sesión tmux con Claude Code + Remote Control
systemd/                         servicio de arranque y temporizador mensual de LactMed
data/                            índices descargados (no se sube a git)
```

## Licencia
El código de Farmaclaw se distribuye bajo licencia [MIT](LICENSE). La licencia cubre solo el código: los datos que consultan los servidores siguen las condiciones de cada fuente (abajo).

## Licencias de las fuentes
- **e-lactancia.org** (APILAM): CC BY-NC-SA 4.0. Uso no comercial y con atribución. El servidor consulta solo bajo demanda (1 petición/s, caché 24 h), sin descargas masivas.
- **LactMed** (NIH/NICHD) y **PubMed**: dominio público / uso libre de E-utilities según las normas del NCBI.
- **CIMA** (AEMPS): API pública oficial.
