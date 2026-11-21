---
description: Interacción entre dos o más fármacos (lee la 4.5 de todos)
argument-hint: <fármaco1> <fármaco2> [fármaco3...]
---
Delega en el agente **research** esta consulta de interacciones: **$ARGUMENTS**

Si solo hay un fármaco, pide el segundo antes de delegar.

Indicaciones para el agente:
- Lee la sección 4.5 de la ficha técnica de CADA fármaco (de un medicamento comercializado del principio activo exacto).
- Analiza cada pareja posible. Para cada interacción encontrada: mecanismo, relevancia clínica y manejo práctico (alternativa, separación de tomas, vigilancia).
- Busca en PubMed metaanálisis o revisiones sistemáticas solo si la ficha indica relevancia clínica incierta o hay controversia.
- Si para una pareja ninguna ficha dice nada, indícalo así: "Sin interacción descrita en las fichas técnicas".
- Termina con: "No detectar una interacción aquí no garantiza que no exista; contrastar con Bot PLUS."

Devuelve la respuesta del agente tal cual, sin resumirla ni añadir información propia.
