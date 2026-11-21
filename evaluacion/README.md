# Evaluación del agente research

Comprueba que el agente **responde bien**, no solo que las APIs funcionan (eso lo hacen las pruebas de `tests/`). Lanza preguntas reales con Claude Code, guarda las respuestas y genera un informe para revisar.

Úsalo cada vez que cambies el prompt del agente (`.claude/agents/research.md`), un comando (`.claude/commands/`) o un servidor MCP.

## Uso

Desde la raíz del proyecto:

```bash
uv run python evaluacion/ejecutar.py --caso int-omeprazol-clopidogrel       # un caso
uv run python evaluacion/ejecutar.py --caso lac-ibuprofeno --caso sum-por-cn # varios
uv run python evaluacion/ejecutar.py --categoria lactancia                   # una categoría
uv run python evaluacion/ejecutar.py --todos                                 # los 21 casos
```

- Los casos se ejecutan **de uno en uno**, para no saturar CIMA, el NCBI ni e-lactancia.
- Cada caso tarda 1-2 minutos y cuesta del orden de 0,2-0,6 USD de uso de Claude: `--todos` son unos 30-40 minutos.
- Requiere tener aprobados los servidores MCP del proyecto (ver el README principal).

Categorías: `interaccion`, `lactancia`, `embarazo`, `posologia`, `contraindicacion`, `suministro`, `reacciones_adversas`, `sin_evidencia`.

## Qué genera

Una carpeta por ejecución en `evaluacion/resultados/AAAA-MM-DD_HHMMSS/` (no se sube a git):

| Archivo | Contenido |
|---|---|
| `informe.md` | Resumen, y por cada caso: pregunta, comprobaciones automáticas ✅/❌, herramientas llamadas, casillas de revisión manual y la respuesta |
| `<id>.json` | Pregunta, respuesta, herramientas llamadas (con argumentos y resultado), duración, coste y comprobaciones |
| `<id>.jsonl` | Salida completa de Claude Code (`stream-json`), para depurar |

El informe indica el commit con el que se ejecutó, para poder comparar antes y después de un cambio.

## Cómo se ejecuta cada caso

```
claude -p "<prompt>" --output-format stream-json --verbose \
  --permission-mode dontAsk \
  --allowedTools Agent "mcp__cima__*" "mcp__pubmed__*" "mcp__elactancia__*" \
  --no-session-persistence
```

- Igual que en el uso real: el agente principal **delega en research**, que llama a los servidores MCP. Las preguntas libres se envían como `Usa el agente research: <pregunta>`; los comandos (`/lactancia …`) tal cual, porque ya delegan.
- `dontAsk` deniega cualquier herramienta no permitida (terminal, edición de archivos, web): la respuesta solo puede salir de las fuentes.
- `stream-json` incluye también las llamadas del subagente (marcadas con `parent_tool_use_id`), así que se ve exactamente qué consultó.

## Comprobaciones automáticas

| Comprobación | Cuándo |
|---|---|
| Delegó en el agente research | Siempre |
| Consultó las herramientas de `debe_consultar` (y la sección y el nº mínimo de veces, si se indican) | Si el caso lo define |
| Al menos una cita `[CIMA:…]`, `[PMID:…]`, `[LactMed:…]` o `[e-lactancia:…]` con enlace | Salvo en `sin_evidencia` |
| Todos los enlaces de la respuesta aparecen en los resultados de las herramientas de esa ejecución (detecta **citas inventadas**; un PMID cuenta si lo devolvió alguna herramienta, p. ej. LactMed) | Siempre |
| Aviso de Bot PLUS | `interaccion` |
| Dice que no hay evidencia | `sin_evidencia` |
| No repite los textos de `no_debe_aparecer` | Si el caso lo define (p. ej. datos de pacientes) |

Las comprobaciones tienen sus propias pruebas en `tests/test_evaluacion.py`.

## Revisión manual

Lo clínico lo revisa un farmacéutico en `informe.md`: marca las casillas de **Debe mencionar** y **No debe**, y anota lo que vea.

## Casos (`casos.yaml`)

```yaml
- id: int-omeprazol-clopidogrel
  categoria: interaccion
  pregunta: "Un paciente con clopidogrel me pide omeprazol…"
  debe_consultar:
    - {herramienta: ficha_tecnica_seccion, seccion: "4.5", minimo: 2}   # la 4.5 de ambos
  debe_mencionar: ["…"]      # revisión manual
  no_debe: ["…"]             # revisión manual
  no_debe_aparecer: ["…"]    # opcional, comprobación automática
  revisado: false            # true cuando un farmacéutico valide los criterios
```

- `seccion` acepta número o alias (`"4.5"` = `"interacciones"`); una subsección (`4.6.2`) cuenta para su sección (`4.6`).
- Los criterios marcados **"BORRADOR - revisar"** los propuso Claude a partir de conocimiento general: no son verdad comprobada hasta que se revisen.
- Los casos de **suministro** caducan (los problemas se resuelven): revísalos de vez en cuando.
