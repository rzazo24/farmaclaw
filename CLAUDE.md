# Farmaclaw

Agente de research para consejo farmacéutico que funciona dentro de Claude Code. Consulta fuentes fiables mediante servidores MCP propios (stdio, Python + FastMCP):

- **cima** (`mcp/cima/server.py`): API REST de CIMA (AEMPS). Medicamentos autorizados en España (por nombre, principio activo o CN), fichas técnicas, prospectos, problemas de suministro, notas de seguridad y materiales informativos.
- **pubmed** (`mcp/pubmed/server.py`): NCBI E-utilities. Búsqueda de artículos y abstracts. Incluye LactMed (`mcp/pubmed/lactmed.py`) desde un índice local en `data/lactmed.json.gz`.

Ambos se registran en `.mcp.json` y Claude Code los arranca solo.

## Consultas clínicas
Para cualquier pregunta farmacéutica, delega en el agente **research** (`.claude/agents/research.md`). Sus reglas:
- Solo información obtenida de las herramientas en la conversación; si no la hay: "No he encontrado evidencia".
- Cada afirmación clínica con cita y enlace: [CIMA: nombre, sección X] o [PMID: 12345678].
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
uv sync                                    # instalar dependencias
uv run python mcp/cima/test_cima.py        # prueba CIMA con casos reales
uv run python mcp/pubmed/lactmed.py         # crear/actualizar índice de LactMed
uv run python mcp/pubmed/test_pubmed.py    # prueba PubMed y LactMed con casos reales
claude mcp list                            # ambos deben salir "✔ Connected"
```
Consulta de ejemplo en Claude Code: "¿Interacción entre omeprazol y clopidogrel?"
