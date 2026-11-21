---
description: Problemas de suministro de un medicamento y alternativas
argument-hint: <nombre, principio activo o nº de registro>
---
Delega en el agente **research** esta consulta de suministro: **$ARGUMENTS**

Indicaciones para el agente:
- Usa `problemas_suministro` y resume por presentación: si el problema está activo, fecha de inicio, fecha prevista de fin y observaciones de la AEMPS.
- Si hay un problema activo, usa `buscar_medicamento` con el principio activo (solo comercializados) y lista las presentaciones sin problemas de suministro como posibles alternativas, recordando que la sustitución debe respetar la normativa.
- Respuesta muy breve: es una consulta de mostrador.

Devuelve la respuesta del agente tal cual, sin resumirla ni añadir información propia.
