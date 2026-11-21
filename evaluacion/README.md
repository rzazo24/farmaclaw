# Evaluación del agente research

Comprueba que el agente **responde bien**, no solo que las APIs funcionan (eso lo hacen las pruebas de `tests/`). Lanza preguntas reales con Claude Code, guarda las respuestas y genera un informe para revisar.

Úsalo cada vez que cambies el prompt del agente (`.claude/agents/research.md`), un comando (`.claude/commands/`) o un servidor MCP.

## Uso

Desde la raíz del proyecto:

```bash
uv run python evaluacion/ejecutar.py --caso int-omeprazol-clopidogrel       # un caso
uv run python evaluacion/ejecutar.py --caso lac-ibuprofeno --caso sum-por-cn # varios
uv run python evaluacion/ejecutar.py --categoria lactancia                   # una categoría
uv run python evaluacion/ejecutar.py --todos                                 # todos los casos
uv run python evaluacion/ejecutar.py --todos --sin-juez                      # sin juez: solo comprobaciones automáticas
uv run python evaluacion/ejecutar.py --solo-juez evaluacion/resultados/2026-11-21_182359   # rejuzgar sin repetir consultas
```

Otras opciones: `--modelo-juez opus` (por defecto `sonnet`), `--segunda-opinion no` (por defecto `opus`), `--comparar CARPETA` (por defecto, la ejecución anterior de cada caso) y `--casos otro.yaml`.

## Flujo de trabajo

1. **Anclar los criterios a las fuentes** (una vez por caso nuevo): `uv run python evaluacion/anclar.py --todos`
2. **Validarlos** en `casos.yaml`: para cada criterio, comprobar que la cita de la fuente es la importante y que el criterio no dice más que la cita. Poner `revisado: true`.
3. **Ejecutar** la evaluación tras cada cambio del agente, un comando o un servidor.
4. **Mirar solo «Para revisar»** del informe: lo nuevo respecto a la ejecución anterior.
5. Si un 🟡/❌ es aceptable, **darlo por bueno** para que no vuelva a salir:
   ```bash
   uv run python evaluacion/ejecutar.py --aceptar int-omeprazol-clopidogrel:N3 --motivo "recomendación práctica razonable"
   ```
   Se guarda en `evaluacion/aceptados.yaml` (se sube a git). Si cambia el texto del criterio, la aceptación deja de aplicarse.

## Criterios anclados a la fuente (`anclar.py`)

Para cada caso sin revisar, Claude consulta las fuentes con **solo** los servidores MCP del proyecto (`--tools ""`, `--strict-mcp-config --mcp-config .mcp.json`) y el prompt [`anclador.md`](anclador.md), y propone 2-5 criterios con la **cita literal** que los respalda y su URL. Python comprueba que la cita y la URL aparecen en lo que devolvieron las herramientas en esa sesión; si no, el criterio se descarta.

```yaml
debe_mencionar:
  - criterio: La ficha de clopidogrel desaconseja el uso concomitante de omeprazol o esomeprazol.
    cita: Como precaución debe desaconsejarse el uso concomitante de omeprazol o esomeprazol (ver sección 4.4).
    fuente: https://cima.aemps.es/cima/dochtml/ft/71622/FT_71622.html
borrador_sin_fuente: [...]   # borradores anteriores, solo como referencia (no se juzgan)
anclado: '2026-11-21'
```

- Nunca toca casos con `revisado: true`. Se saltan los de comportamiento (`sin_evidencia`, privacidad).
- Lo que Python **no** puede comprobar es que el criterio no diga más que su cita: eso es lo que valida el farmacéutico.
- Los casos de **suministro** suelen quedarse sin anclar: sus datos son campos (fechas, CN), no frases citables.
- Escribe en `casos.yaml` conservando comentarios y formato.

- Los casos se ejecutan **de uno en uno**, para no saturar CIMA, el NCBI ni e-lactancia.
- Cada caso tarda 1-2 minutos y cuesta del orden de 0,2-0,6 USD de uso de Claude: `--todos` son unos 30-40 minutos.
- Requiere tener aprobados los servidores MCP del proyecto (ver el README principal).

Categorías: `interaccion`, `lactancia`, `embarazo`, `posologia`, `contraindicacion`, `suministro`, `reacciones_adversas`, `sin_evidencia`.

## Qué genera

Una carpeta por ejecución en `evaluacion/resultados/AAAA-MM-DD_HHMMSS/` (no se sube a git):

| Archivo | Contenido |
|---|---|
| `informe.md` | **Para revisar** (solo lo nuevo respecto a la ejecución anterior; lo que ya fallaba, en un desplegable; lo aceptado, oculto), tabla resumen, y por cada caso: comprobaciones automáticas, herramientas llamadas, veredictos del juez con evidencia y la respuesta |
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

## Juez automático

Después de las comprobaciones automáticas, un **juez** (otra llamada a `claude -p`) revisa la respuesta contra `debe_mencionar` y `no_debe`, para que el farmacéutico solo mire los fallos.

- **Solo juzga casos con `revisado: true`.** Los demás salen como "⚠️ criterios sin validar, no juzgado".
- **Aislado:** recibe solo la pregunta, la respuesta y los criterios (sin el prefijo "BORRADOR - revisar:"). Sin herramientas (`--tools ""`), sin MCP (`--strict-mcp-config`), sin comandos, con su propio prompt ([`juez.md`](juez.md)) y desde una carpeta temporal (no ve `CLAUDE.md`). No usa conocimiento clínico: solo comprueba si el texto dice lo que pide cada criterio.
- **Salida JSON validada** con `--json-schema`. Si no es válida, un reintento; si vuelve a fallar, el caso queda como "💥 juez: error" y la ejecución sigue.
- **Segunda opinión:** los criterios que quedan 🟡 se vuelven a juzgar con `opus`; solo cambia el veredicto si la segunda opinión es concluyente (y su evidencia también se verifica).
- **Evidencia comprobada en Python:** cuando el juez afirma que algo *está* en el texto (debe_mencionar "cumple" o no_debe "no_cumple"), la frase citada tiene que aparecer literalmente en la respuesta (ignorando Markdown, espacios y mayúsculas). Si no, el veredicto pasa a 🟡 dudoso.
- En `no_debe`, ✅ significa que el error **no** aparece.

| Icono | Significado |
|---|---|
| ✅ | Todos los criterios se cumplen |
| ❌ | Algún criterio no se cumple |
| 🟡 | Algún criterio dudoso (y ninguno incumplido) |
| ⚠️ | Criterios sin validar: no juzgado |
| 💥 | Error del juez |

`--solo-juez CARPETA` vuelve a juzgar una ejecución guardada con los criterios **actuales** de `casos.yaml`, sin repetir las consultas al agente: útil después de validar o corregir criterios.

## Revisión manual

Normalmente basta con la sección **Para revisar** del informe. Cada caso termina con "☐ Revisado por farmacéutico" para revisiones puntuales.

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
