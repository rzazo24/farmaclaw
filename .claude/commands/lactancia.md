---
description: Uso de un fármaco durante la lactancia (e-lactancia + LactMed + ficha 4.6)
argument-hint: <fármaco> [edad del lactante o contexto]
---
Delega en el agente **research** esta consulta sobre lactancia: **$ARGUMENTS**

Indicaciones para el agente:
- Consulta e-lactancia (`elactancia`, principio activo en español): nivel de riesgo, comentario y alternativas. Empieza la respuesta breve por su nivel de riesgo.
- Lee la sección 4.6 de la ficha técnica (apartado de lactancia).
- Consulta LactMed (`lactmed`, nombre en inglés): resumen de uso, alternativas y, si hace falta, niveles en leche y efectos en lactantes.
- Usa PubMed solo si LactMed no tiene ficha del fármaco o los datos son escasos.
- Si hay datos, indica: excreción en leche, efectos descritos en lactantes y recomendación práctica.
- Si e-lactancia, LactMed y la ficha técnica difieren, muestra las tres posturas.

Devuelve la respuesta del agente tal cual, sin resumirla ni añadir información propia.
