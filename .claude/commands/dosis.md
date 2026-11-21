---
description: Posología autorizada (ficha 4.2), incluida pediátrica e insuficiencia renal/hepática
argument-hint: <fármaco> [niños | peso | insuficiencia renal | indicación...]
---
Delega en el agente **research** esta consulta de posología: **$ARGUMENTS**

Indicaciones para el agente:
- Lee la sección 4.2 de la ficha técnica. Elige una presentación adecuada al contexto (p. ej. suspensión oral si se habla de niños).
- Si la consulta es pediátrica y se da un peso, calcula la dosis mostrando el cálculo paso a paso (mg/kg × peso) y la dosis máxima de la ficha. Si no se da peso, da la pauta en mg/kg.
- Si se menciona insuficiencia renal o hepática, o personas mayores, incluye el ajuste que indique la ficha.
- Solo datos de la ficha técnica; no uses PubMed salvo que se pida.

Devuelve la respuesta del agente tal cual, sin resumirla ni añadir información propia.
