# Farmaclaw

Agente de research para consejo farmacéutico que funciona dentro de Claude Code. Consulta fuentes fiables mediante servidores MCP propios (stdio, Python + FastMCP):

- **cima** (`mcp/cima/server.py`): API REST de CIMA (AEMPS). Medicamentos autorizados en España (por nombre, principio activo o CN), presentaciones con su CN, fichas técnicas, prospectos, problemas de suministro, notas de seguridad y materiales informativos.
- **pubmed** (`mcp/pubmed/server.py`): NCBI E-utilities. Búsqueda de artículos y abstracts. Incluye LactMed (`mcp/pubmed/lactmed.py`) desde un índice local en `data/lactmed.json.gz`.
- **elactancia** (`mcp/elactancia/server.py`): e-lactancia.org (APILAM), compatibilidad con la lactancia en español. Sin API: buscador `/megasearch/` (JSON) + ficha HTML. CC BY-NC-SA 4.0: no comercial, con atribución; solo consultas bajo demanda (1 req/s, caché 24 h), nunca descargas masivas.

Todos se registran en `.mcp.json` y Claude Code los arranca solo.

## Consultas clínicas
Para cualquier pregunta farmacéutica, delega en el agente **research** (`.claude/agents/research.md`) pasándole la pregunta del usuario tal cual (sin añadir indicaciones, pistas ni lo que esperas que responda) y **devuelve su respuesta tal cual**: sin resumirla, sin reformularla ("el agente dice…") y sin añadir información propia, que no tendría cita. Si quieres comentar algo (p. ej. una mejora del proyecto), sepáralo claramente después de la respuesta. Reglas del agente:
- Solo información obtenida de las herramientas en la conversación; si no la hay: "No he encontrado evidencia".
- Cada afirmación clínica con cita y enlace: [CIMA: nombre, sección X] o [PMID: 12345678]. Nada de mecanismos o datos sin fuente; los consejos prácticos van marcados como "Consejo práctico (no procede de las fuentes)".
- Prioridad: ficha técnica CIMA > metaanálisis/revisiones sistemáticas > ensayos > casos clínicos (solo apoyo).
- Interacciones: leer la 4.5 de ambos fármacos; mecanismo, relevancia y manejo; aviso de contrastar con Bot PLUS.
- Formato móvil: respuesta breve (2-4 frases), detalle y referencias.
- Nunca pedir ni guardar datos identificativos de pacientes.

## Reglas para los servidores MCP
- Respuestas compactas: nunca documentos enteros, solo lo pedido. Secciones de más de 8.000 caracteres se sirven por partes (`parte=N`). Siempre un campo `fuente` con URL pública.
- Errores con `ToolError`, claros y en español.
- Timeout de 15 s y un reintento. Caché en memoria con TTL de 1 h.
- Logs solo a stderr (stdout es el canal MCP).
- `mcp<2` fijado en `pyproject.toml`: la v2 del SDK renombra FastMCP a MCPServer.

## Particularidades de las APIs (comprobadas)
- CIMA: `/psuministro?nregistro=` ignora el parámetro; hay que ir por presentación: `/psuministro/{cn}`.
- CIMA: una sección inexistente devuelve 200 con `{"error": ...}`.
- CIMA: la búsqueda por nombre es por subcadena ("omeprazol" trae "esomeprazol"). Por nombre no salen las marcas (Depakine): se busca siempre por nombre Y por principio activo (`practiv1`, que entiende "valproato" = "valproico").
- CIMA: las fechas son medianoche de Madrid en ms; convertir con `Europe/Madrid`, no UTC (saldría el día anterior).
- PubMed: artículos recientes pueden no tener MeSH todavía.
- LactMed: la web tiene reCAPTCHA y `efetch` no da el texto de Bookshelf; se usa el tar.gz oficial de `ftp.ncbi.nlm.nih.gov/pub/litarch/` (NBK501922). Títulos en inglés USAN (acetaminophen, albuterol, dipyrone): ver `SINONIMOS`.

## Cómo probar
```bash
uv sync                          # dependencias (incluye pytest)
uv run python mcp/pubmed/lactmed.py   # crear/actualizar índice de LactMed
uv run pytest                    # todas las pruebas (tests/)
uv run pytest -m "not red"       # solo las que no llaman a APIs externas
claude mcp list                  # los tres deben salir "✔ Connected"
```
Al tocar un servidor, añade su prueba en `tests/`: las de parseo con datos de ejemplo (sin red) y las de API marcadas con `@pytest.mark.red`.
Al cambiar el prompt del agente, un comando o un servidor, ejecuta la evaluación (`evaluacion/README.md`): `--rapido` (5 casos) o la categoría afectada. Con la suscripción Pro, 5 casos consumen ~15-18 % de la ventana de 5 h y la evaluación completa ~70-80 %: no la lances sin que el usuario lo pida. Los criterios de `evaluacion/casos.yaml` los valida un farmacéutico (`revisado: true`): no los cambies ni los marques como revisados por tu cuenta.

Consulta de ejemplo en Claude Code: "¿Interacción entre omeprazol y clopidogrel?"
