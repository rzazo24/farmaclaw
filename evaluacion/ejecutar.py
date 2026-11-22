"""Evaluación del agente research: lanza casos reales con `claude -p` y genera un informe.

Uso (desde la raíz del proyecto):
    uv run python evaluacion/ejecutar.py --caso int-omeprazol-clopidogrel
    uv run python evaluacion/ejecutar.py --categoria lactancia
    uv run python evaluacion/ejecutar.py --todos
    uv run python evaluacion/ejecutar.py --rapido                  # 5 casos representativos
    uv run python evaluacion/ejecutar.py --todos --sin-juez        # solo comprobaciones automáticas
    uv run python evaluacion/ejecutar.py --solo-juez evaluacion/resultados/2026-11-21_181034

Cada ejecución crea evaluacion/resultados/AAAA-MM-DD_HHMMSS/ con:
    <id>.json      pregunta, respuesta, herramientas llamadas, duración, coste, comprobaciones y juez
    <id>.jsonl     salida stream-json completa de Claude Code (para depurar)
    informe.md     resumen, casos para revisar, comprobaciones, veredictos del juez y respuestas
"""

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
CASOS = Path(__file__).with_name("casos.yaml")
JUEZ = Path(__file__).with_name("juez.md")
ACEPTADOS = Path(__file__).with_name("aceptados.yaml")
RESULTADOS = Path(__file__).with_name("resultados")
EJECUCION = re.compile(r"\d{4}-\d{2}-\d{2}_\d{6}")  # nombre de carpeta de una ejecución real
TIMEOUT_CASO = 600  # s; una consulta normal tarda 1-2 min

# Igual que en uso real: el agente principal delega en research, que llama a los MCP.
# dontAsk deniega todo lo no permitido (Bash, edición, web...): solo cuentan las fuentes.
COMANDO = [
    "claude", "-p",
    "--output-format", "stream-json", "--verbose",
    "--permission-mode", "dontAsk",
    "--allowedTools", "Agent", "mcp__cima__*", "mcp__pubmed__*", "mcp__elactancia__*",
    "--no-session-persistence",
]

# Alias de secciones: se reutilizan los del servidor de CIMA para no desincronizarse.
_spec = importlib.util.spec_from_file_location("cima_server", RAIZ / "mcp" / "cima" / "server.py")
_cima = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cima)

CITA = re.compile(r"\[(CIMA|PMID|LactMed|e-lactancia)\b[^\]]*\]\((https?://[^)\s]+)\)", re.I)
ENLACE = re.compile(r"\]\((https?://[^)\s]+)\)")


# ───────────────────────── Ejecución ─────────────────────────

def lanzar(caso: dict, carpeta: Path) -> dict:
    pregunta = caso["pregunta"].strip()
    # Los comandos rápidos ya delegan en research; las preguntas libres lo piden explícitamente.
    prompt = pregunta if pregunta.startswith("/") else f"Usa el agente research: {pregunta}"
    crudo = carpeta / f"{caso['id']}.jsonl"
    inicio = time.monotonic()
    try:
        proc = subprocess.run(COMANDO + [prompt], cwd=RAIZ, capture_output=True, text=True, timeout=TIMEOUT_CASO,
                              stdin=subprocess.DEVNULL)
        salida, error = proc.stdout, proc.stderr.strip()
    except subprocess.TimeoutExpired as e:
        salida, error = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), f"timeout ({TIMEOUT_CASO} s)"
    crudo.write_text(salida)
    return {**analizar(salida), "duracion_s": round(time.monotonic() - inicio, 1), "error": error or None}


def analizar(salida: str) -> dict:
    """Extrae del stream-json la respuesta final y todas las llamadas a herramientas (incluidas las del subagente)."""
    llamadas: dict[str, dict] = {}
    respuesta, coste, subagentes = "", None, []
    for linea in salida.splitlines():
        try:
            ev = json.loads(linea)
        except json.JSONDecodeError:
            continue
        tipo = ev.get("type")
        contenido = ev.get("message", {}).get("content") if tipo in ("assistant", "user") else None
        for bloque in contenido if isinstance(contenido, list) else []:
            if bloque.get("type") == "tool_use":
                llamadas[bloque["id"]] = {
                    "herramienta": bloque["name"],
                    "entrada": bloque.get("input", {}),
                    "subagente": ev.get("parent_tool_use_id") is not None,
                    "resultado": "",
                }
                if bloque["name"] == "Agent":
                    subagentes.append(bloque.get("input", {}).get("subagent_type"))
            elif bloque.get("type") == "tool_result" and bloque.get("tool_use_id") in llamadas:
                c = bloque.get("content")
                texto = c if isinstance(c, str) else "".join(x.get("text", "") for x in c or [] if isinstance(x, dict))
                llamadas[bloque["tool_use_id"]]["resultado"] = texto
        if tipo == "result":
            respuesta = ev.get("result") or "\n".join(ev.get("errors", []))
            coste = ev.get("total_cost_usd")
    return {"respuesta": respuesta, "llamadas": list(llamadas.values()), "subagentes": subagentes, "coste_usd": coste}


# ───────────────────────── Comprobaciones automáticas ─────────────────────────

def _seccion(s: str) -> str:
    s = str(s).strip().lower()
    return _cima.ALIAS_FICHA.get(s) or _cima.ALIAS_PROSPECTO.get(s) or s


def _cumple(llamada: dict, req: dict) -> bool:
    if not llamada["herramienta"].endswith("__" + req["herramienta"]):
        return False
    if "seccion" in req:
        pedida = _seccion(llamada["entrada"].get("seccion", ""))
        return any(pedida == s or pedida.startswith(s + ".") for s in _secciones(req))
    return True


def _secciones(req: dict) -> list[str]:
    """`seccion` puede ser una sección ("4.2") o varias válidas (["4.1", "4.2"])."""
    s = req["seccion"]
    return [str(x) for x in s] if isinstance(s, list) else [str(s)]


def _fuentes(llamadas: list[dict]) -> str:
    """Todo el texto devuelto por las herramientas MCP: de ahí deben salir las citas."""
    return "\n".join(l["resultado"] for l in llamadas if l["herramienta"].startswith("mcp__"))


def _enlace_respaldado(url: str, fuentes: str) -> bool:
    u = url.rstrip("/.,;")
    if u in fuentes:
        return True
    pmid = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", u)
    return bool(pmid and pmid.group(1) in fuentes)  # LactMed y abstracts citan el PMID sin URL


def comprobar(caso: dict, r: dict) -> list[dict]:
    res = []

    def anotar(nombre, ok, detalle=""):
        res.append({"comprobacion": nombre, "ok": ok, "detalle": detalle})

    texto, llamadas = r["respuesta"], r["llamadas"]
    if not texto:
        anotar("Hay respuesta", False, r.get("error") or "sin evento result")
        return res

    anotar("Delegó en el agente research", "research" in r["subagentes"],
           f"subagentes: {r['subagentes'] or 'ninguno'}")

    for req in caso.get("debe_consultar", []):
        minimo = req.get("minimo", 1)
        n = sum(_cumple(l, req) for l in llamadas)
        etiqueta = req["herramienta"] + (f" {' o '.join(_secciones(req))}" if "seccion" in req else "")
        anotar(f"Consultó {etiqueta} (≥{minimo})", n >= minimo, f"{n} llamada(s)")

    citas = CITA.findall(texto)
    if caso["categoria"] != "sin_evidencia":
        anotar("Al menos una cita con enlace", bool(citas), f"{len(citas)} cita(s)")

    fuentes = _fuentes(llamadas)
    enlaces = sorted(set(ENLACE.findall(texto)))
    sin_respaldo = [u for u in enlaces if not _enlace_respaldado(u, fuentes)]
    anotar("Enlaces procedentes de las herramientas", not sin_respaldo,
           f"{len(enlaces) - len(sin_respaldo)}/{len(enlaces)} respaldados"
           + (": sin respaldo → " + ", ".join(sin_respaldo) if sin_respaldo else ""))

    if caso["categoria"] == "interaccion":
        anotar("Aviso de Bot PLUS", "bot plus" in texto.lower())

    if caso["categoria"] == "sin_evidencia":
        anotar("Dice que no hay evidencia", bool(re.search(r"no he encontrado (evidencia|ning)|no (existe|figura|consta)", texto, re.I)))

    prohibidos = [p for p in caso.get("no_debe_aparecer", []) if p.lower() in texto.lower()]
    if "no_debe_aparecer" in caso:
        anotar("No repite datos identificativos", not prohibidos, ", ".join(prohibidos))
    return res


# ───────────────────────── Juez ─────────────────────────

# Llamada aislada: sin herramientas integradas (--tools ""), sin MCP (--strict-mcp-config sin
# --mcp-config), sin comandos, con su propio system prompt y desde una carpeta temporal fuera
# del proyecto (no carga CLAUDE.md). --json-schema obliga a devolver JSON con esta forma.
ESQUEMA_JUEZ = {
    "type": "object",
    "properties": {"veredictos": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "criterio": {"type": "string"},
            "veredicto": {"type": "string", "enum": ["cumple", "no_cumple", "dudoso"]},
            "evidencia": {"type": "string"},
        },
        "required": ["id", "criterio", "veredicto", "evidencia"],
    }}},
    "required": ["veredictos"],
}
TIMEOUT_JUEZ = 180
PREFIJO_BORRADOR = re.compile(r"^\s*BORRADOR\s*-\s*revisar\s*:\s*", re.I)


def criterios(caso: dict) -> list[dict]:
    """Criterios a juzgar, con id M1.. (debe_mencionar) y N1.. (no_debe), sin el prefijo de borrador."""
    lista = []
    for prefijo, campo in (("M", "debe_mencionar"), ("N", "no_debe")):
        for n, item in enumerate(caso.get(campo, []), 1):
            # Texto suelto o criterio anclado a la fuente: {criterio, cita, fuente}
            texto = item["criterio"] if isinstance(item, dict) else item
            lista.append({"id": f"{prefijo}{n}", "tipo": campo, "criterio": PREFIJO_BORRADOR.sub("", texto).strip(),
                          **({"cita": item["cita"], "fuente": item["fuente"]} if isinstance(item, dict) else {})})
    return lista


def _normalizar(t: str) -> str:
    t = re.sub(r"[*_`>#]", "", t)                       # marcas de Markdown
    t = t.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return re.sub(r"\s+", " ", t).strip().lower()


def evidencia_literal(evidencia: str, respuesta: str) -> bool:
    """¿La evidencia aparece literalmente en la respuesta? (ignora Markdown, espacios y mayúsculas)"""
    e = _normalizar(evidencia).strip('"«» ')
    return len(e) >= 8 and e in _normalizar(respuesta)


def parsear_juez(salida: str, esperados: list[dict]) -> list[dict] | None:
    """Extrae los veredictos de la salida JSON de `claude -p`. None si no es válida."""
    try:
        ev = json.loads(salida)
        datos = ev.get("structured_output")
        if datos is None:  # sin salida estructurada: intentar el texto, quitando ```json
            texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", (ev.get("result") or "").strip())
            datos = json.loads(texto)
        veredictos = datos["veredictos"]
        por_id = {v["id"]: v for v in veredictos}
        if not all(c["id"] in por_id for c in esperados):
            return None
        if any(por_id[c["id"]]["veredicto"] not in ("cumple", "no_cumple", "dudoso") for c in esperados):
            return None
        return [{**c, "veredicto": por_id[c["id"]]["veredicto"], "evidencia": por_id[c["id"]].get("evidencia", "")}
                for c in esperados]
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return None


def verificar_evidencias(veredictos: list[dict], respuesta: str) -> list[dict]:
    """Un veredicto que afirma que algo ESTÁ en el texto necesita evidencia literal; si no, pasa a dudoso.

    Afirman presencia: debe_mencionar "cumple" y no_debe "no_cumple".
    (no_debe "cumple" afirma una ausencia: no hay frase que citar.)
    """
    for v in veredictos:
        afirma = (v["tipo"] == "debe_mencionar" and v["veredicto"] == "cumple") or \
                 (v["tipo"] == "no_debe" and v["veredicto"] == "no_cumple")
        if afirma and not evidencia_literal(v["evidencia"], respuesta):
            v["veredicto_juez"], v["veredicto"] = v["veredicto"], "dudoso"
            v["nota"] = "evidencia no encontrada literalmente en la respuesta"
    return veredictos


def _llamar_juez(pregunta: str, respuesta: str, esperados: list[dict], modelo: str) -> tuple[list[dict] | None, float, str]:
    """Una consulta al juez (con un reintento). Devuelve (veredictos o None, coste, detalle del error)."""
    entrada = json.dumps({
        "pregunta": pregunta,
        "respuesta": respuesta,
        "criterios": [{"id": c["id"], "criterio": c["criterio"]} for c in esperados],
    }, ensure_ascii=False, indent=1)
    comando = [
        "claude", "-p", entrada,
        "--system-prompt", JUEZ.read_text(),
        "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
        "--model", modelo,
        "--output-format", "json", "--json-schema", json.dumps(ESQUEMA_JUEZ),
        "--no-session-persistence",
    ]
    coste, detalle = 0.0, ""
    with tempfile.TemporaryDirectory() as aislado:
        for _ in range(2):  # un reintento si la salida no es válida
            try:
                proc = subprocess.run(comando, cwd=aislado, capture_output=True, text=True, timeout=TIMEOUT_JUEZ,
                                      stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                detalle = f"timeout ({TIMEOUT_JUEZ} s)"
                continue
            try:
                coste += json.loads(proc.stdout).get("total_cost_usd") or 0
            except (json.JSONDecodeError, AttributeError):
                pass
            veredictos = parsear_juez(proc.stdout, esperados)
            if veredictos is not None:
                return verificar_evidencias(veredictos, respuesta), coste, ""
            detalle = "respuesta del juez no válida: " + (proc.stderr.strip() or proc.stdout.strip())[:200]
    return None, coste, detalle


def juzgar(caso: dict, respuesta: str, modelo: str, segunda: str | None = None) -> dict:
    """Juzga los criterios del caso. Los 🟡 se vuelven a juzgar con el modelo `segunda` (si se indica)."""
    if not caso.get("revisado"):
        return {"estado": "no_revisado"}
    esperados = criterios(caso)
    if not esperados:
        return {"estado": "sin_criterios"}
    if not respuesta:
        return {"estado": "error", "detalle": "el agente no dio respuesta"}

    veredictos, coste, detalle = _llamar_juez(caso["pregunta"], respuesta, esperados, modelo)
    if veredictos is None:
        return {"estado": "error", "detalle": detalle, "coste_usd": round(coste, 4)}

    dudosos = [v for v in veredictos if v["veredicto"] == "dudoso"]
    if segunda and dudosos:
        revisados, coste2, _ = _llamar_juez(caso["pregunta"], respuesta,
                                            [{k: v[k] for k in ("id", "tipo", "criterio")} for v in dudosos], segunda)
        coste += coste2
        for nuevo in revisados or []:
            v = next(x for x in veredictos if x["id"] == nuevo["id"])
            v["segunda_opinion"] = {"modelo": segunda, "veredicto": nuevo["veredicto"]}
            if nuevo["veredicto"] != "dudoso":  # solo sustituye si la segunda opinión es concluyente
                v.update(veredicto=nuevo["veredicto"], evidencia=nuevo["evidencia"])
                v.pop("nota", None)
                v.pop("veredicto_juez", None)
    return {"estado": "juzgado", "modelo": modelo, "segunda": segunda if dudosos else None,
            "coste_usd": round(coste, 4), "veredictos": veredictos}


def resumen_juez(j: dict | None) -> tuple[str, str]:
    """(icono, texto) para la tabla del informe."""
    if not j:
        return "—", "no ejecutado"
    if j["estado"] == "no_revisado":
        return "⚠️", "sin validar"
    if j["estado"] == "sin_criterios":
        return "—", "sin criterios"
    if j["estado"] == "error":
        return "💥", "juez: error"
    vs = [v["veredicto"] for v in j["veredictos"]]
    icono = "❌" if "no_cumple" in vs else "🟡" if "dudoso" in vs else "✅"
    return icono, f"{vs.count('cumple')}/{len(vs)}"


# ───────────────────────── Comparación y aceptados ─────────────────────────

ICONO_VEREDICTO = {"cumple": "✅", "no_cumple": "❌", "dudoso": "🟡"}

def cargar_aceptados() -> set[tuple[str, str]]:
    """(caso, criterio) dados por buenos por el farmacéutico: no vuelven a salir en «Para revisar»."""
    if not ACEPTADOS.exists():
        return set()
    return {(a["caso"], a["criterio"]) for a in yaml.safe_load(ACEPTADOS.read_text()) or []}


def problemas(r: dict, aceptados: set[tuple[str, str]]) -> dict[str, str]:
    """Problemas de un resultado, como {clave estable: texto para el informe}. Excluye los aceptados."""
    out = {}
    for c in r.get("comprobaciones", []):
        if not c["ok"]:
            out[f"auto:{c['comprobacion']}"] = f"❌ automática: {c['comprobacion']}"
    j = r.get("juez") or {}
    if j.get("estado") == "error":
        out["juez:error"] = f"💥 juez: error ({j.get('detalle', '')})"
    for v in j.get("veredictos", []):
        if v["veredicto"] != "cumple" and (r["id"], v["criterio"]) not in aceptados:
            out[f"juez:{v['criterio']}"] = f"{ICONO_VEREDICTO[v['veredicto']]} {v['id']}: {v['criterio']}"
    return out


def linea_base(carpeta: Path, id_: str, explicita: Path | None = None) -> tuple[dict | None, str | None]:
    """Resultado del mismo caso en la ejecución anterior (o en la carpeta indicada)."""
    # Solo ejecuciones reales (AAAA-MM-DD_HHMMSS): las carpetas de pruebas (_manipulada...) no cuentan.
    candidatas = [explicita] if explicita else sorted(
        (d for d in RESULTADOS.iterdir() if d.is_dir() and EJECUCION.fullmatch(d.name) and d.name < carpeta.name),
        reverse=True)
    for d in candidatas:
        f = d / f"{id_}.json"
        if f.exists():
            return json.loads(f.read_text()), d.name
    return None, None


# ───────────────────────── Informe ─────────────────────────


def _corto(nombre: str) -> str:
    return nombre.split("__")[-1] if nombre.startswith("mcp__") else nombre


def informe(carpeta: Path, resultados: list[dict], commit: str, comparar: Path | None = None) -> Path:
    total = sum(len(r["comprobaciones"]) for r in resultados)
    ok = sum(c["ok"] for r in resultados for c in r["comprobaciones"])
    duracion = sum(r.get("duracion_s") or 0 for r in resultados)
    coste = sum((r.get("coste_usd") or 0) + ((r.get("juez") or {}).get("coste_usd") or 0) for r in resultados)

    # Solo es nuevo lo que no fallaba en la ejecución anterior del mismo caso; lo aceptado no sale.
    aceptados = cargar_aceptados()
    nuevos, vistos, n_aceptados, bases = [], [], 0, set()
    for r in resultados:
        actuales = problemas(r, aceptados)
        n_aceptados += sum(1 for v in (r.get("juez") or {}).get("veredictos", [])
                           if v["veredicto"] != "cumple" and (r["id"], v["criterio"]) in aceptados)
        base, nombre = linea_base(carpeta, r["id"], comparar)
        previos = problemas(base, aceptados) if base else {}
        if nombre:
            bases.add(nombre)
        n = [t for k, t in actuales.items() if k not in previos]
        v = [t for k, t in actuales.items() if k in previos]
        if n:
            nuevos.append((r["id"], n))
        if v:
            vistos.append((r["id"], v))

    l = [
        f"# Evaluación del agente research: {carpeta.name}",
        "",
        f"- Commit: `{commit}`",
        f"- Casos: {len(resultados)} · Comprobaciones automáticas: {ok}/{total} ✅",
        f"- Duración total: {duracion / 60:.1f} min · Coste (agente + juez): {coste:.2f} USD",
        f"- Comparado con: {', '.join(sorted(bases)) or 'nada (primera ejecución de estos casos)'}",
        "",
        "## Para revisar",
        "",
    ]
    if nuevos:
        for id_, motivos in nuevos:
            l.append(f"- [{id_}](#{id_})")
            l += [f"  - {m}" for m in motivos]
    else:
        l.append("Nada nuevo ✅")
    if vistos:
        l += ["", "<details><summary>Ya fallaban en la ejecución anterior "
              f"({sum(len(m) for _, m in vistos)})</summary>", ""]
        for id_, motivos in vistos:
            l.append(f"- [{id_}](#{id_}): " + " · ".join(motivos))
        l += ["", "</details>"]
    if n_aceptados:
        l += ["", f"Aceptados por el farmacéutico (no se muestran): {n_aceptados}. Ver `evaluacion/aceptados.yaml`."]
    no_juzgados = [r["id"] for r in resultados if resumen_juez(r.get("juez"))[0] == "⚠️"]
    if no_juzgados:
        l += ["", f"⚠️ Sin juzgar por criterios sin validar ({len(no_juzgados)}): " + ", ".join(no_juzgados)]

    l += ["", "## Resumen", "",
          "| Caso | Categoría | Automáticas | Juez | Duración |",
          "|---|---|---|---|---|"]
    for r in resultados:
        c = r["comprobaciones"]
        icono, texto = resumen_juez(r.get("juez"))
        l.append(f"| [{r['id']}](#{r['id']}) | {r['categoria']} | {sum(x['ok'] for x in c)}/{len(c)} "
                 f"| {icono} {texto} | {(r.get('duracion_s') or 0):.0f} s |")

    for r in resultados:
        caso, j = r["caso"], r.get("juez")
        l += ["", "---", "", f"## {r['id']}", "",
              f"**Categoría:** {r['categoria']} · **Duración:** {(r.get('duracion_s') or 0):.0f} s"
              + (f" · **Coste:** {r['coste_usd']:.3f} USD" if r.get("coste_usd") else ""), "",
              f"**Pregunta:** {caso['pregunta']}", "",
              "### Comprobaciones automáticas", ""]
        l += [f"- {'✅' if c['ok'] else '❌'} {c['comprobacion']}" + (f" ({c['detalle']})" if c["detalle"] else "")
              for c in r["comprobaciones"]]
        l += ["", "### Herramientas llamadas", ""]
        for x in r["llamadas"]:
            if x["herramienta"] == "Agent":
                continue
            args = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in x["entrada"].items())
            fallo = " ❌ error" if x["resultado"].startswith("Error executing tool") else ""
            l.append(f"- `{_corto(x['herramienta'])}({args})`{fallo}")
        if not any(x["herramienta"] != "Agent" for x in r["llamadas"]):
            l.append("- (ninguna)")

        l += ["", "### Criterios", ""]
        if j and j["estado"] == "juzgado":
            l.append(f"Juez: `{j['modelo']}`. ✅ cumple · ❌ no cumple · 🟡 dudoso. "
                     "En «no debe», ✅ significa que el error no aparece.")
            for tipo, titulo in (("debe_mencionar", "Debe mencionar"), ("no_debe", "No debe")):
                vs = [v for v in j["veredictos"] if v["tipo"] == tipo]
                if vs:
                    l += ["", f"**{titulo}:**", ""]
                for v in vs:
                    aceptado = " (aceptado)" if v["veredicto"] != "cumple" and (r["id"], v["criterio"]) in cargar_aceptados() else ""
                    l.append(f"- {ICONO_VEREDICTO[v['veredicto']]} **{v['id']}** {v['criterio']}{aceptado}")
                    if v.get("evidencia"):
                        l.append(f"  - Evidencia: «{v['evidencia']}»")
                    if v.get("nota"):
                        l.append(f"  - ⚠️ El juez dijo «{v['veredicto_juez']}», pero {v['nota']}.")
                    if v.get("segunda_opinion"):
                        so = v["segunda_opinion"]
                        l.append(f"  - Segunda opinión ({so['modelo']}): {ICONO_VEREDICTO[so['veredicto']]} {so['veredicto']}")
                    fuente = next((c for c in criterios(caso) if c["id"] == v["id"] and c.get("cita")), None)
                    if fuente:
                        l.append(f"  - Fuente del criterio: «{fuente['cita']}» ([enlace]({fuente['fuente']}))")
        else:
            aviso = {"no_revisado": "⚠️ criterios sin validar, no juzgado",
                     "error": f"💥 juez: error ({(j or {}).get('detalle', '')})",
                     "sin_criterios": "Sin criterios que juzgar"}.get((j or {}).get("estado"), "Juez no ejecutado (--sin-juez)")
            l += [aviso, "", "**Debe mencionar:**", ""]
            for c in criterios(caso):
                if c["tipo"] == "debe_mencionar":
                    l.append(f"- {c['criterio']}")
                    if c.get("cita"):
                        l.append(f"  - Fuente: «{c['cita']}» ([enlace]({c['fuente']}))")
            l += ["", "**No debe:**", ""]
            l += [f"- {c['criterio']}" for c in criterios(caso) if c["tipo"] == "no_debe"]
        l += ["", "☐ Revisado por farmacéutico", "", "**Notas:** ", "", "### Respuesta", ""]
        l += ["> " + linea if linea else ">" for linea in (r["respuesta"] or "(sin respuesta)").splitlines()]
    ruta = carpeta / "informe.md"
    ruta.write_text("\n".join(l) + "\n")
    return ruta


# ───────────────────────── Principal ─────────────────────────

def _commit() -> str:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=RAIZ, capture_output=True, text=True).stdout.strip()
    sucio = subprocess.run(["git", "status", "--porcelain"], cwd=RAIZ, capture_output=True, text=True).stdout.strip()
    return commit + (" (con cambios sin commit)" if sucio else "")


def _guardar(carpeta: Path, r: dict):
    datos = {k: v for k, v in r.items() if k != "caso"} | {"pregunta": r["caso"]["pregunta"]}
    (carpeta / f"{r['id']}.json").write_text(json.dumps(datos, ensure_ascii=False, indent=2))


def _linea_juez(j: dict | None) -> str:
    icono, texto = resumen_juez(j)
    return "" if j is None else f" · juez {icono} {texto}"


def aceptar(clave: str, motivo: str):
    """Da por bueno un criterio 🟡/❌ de un caso (CASO:ID, p. ej. int-omeprazol-clopidogrel:N3)."""
    if ":" not in clave:
        sys.exit("Formato: --aceptar CASO:ID (p. ej. int-omeprazol-clopidogrel:N3)")
    id_caso, id_criterio = clave.split(":", 1)
    for d in sorted((d for d in RESULTADOS.iterdir() if d.is_dir()), reverse=True):
        f = d / f"{id_caso}.json"
        if f.exists():
            v = next((v for v in (json.loads(f.read_text()).get("juez") or {}).get("veredictos", [])
                      if v["id"] == id_criterio), None)
            if v:
                break
    else:
        sys.exit(f"No encuentro {clave} en ninguna ejecución juzgada.")
    lista = yaml.safe_load(ACEPTADOS.read_text()) if ACEPTADOS.exists() else []
    lista = lista or []
    if any(a["caso"] == id_caso and a["criterio"] == v["criterio"] for a in lista):
        sys.exit("Ya estaba aceptado.")
    lista.append({"caso": id_caso, "criterio": v["criterio"], "veredicto": v["veredicto"],
                  "motivo": motivo, "fecha": datetime.now().strftime("%Y-%m-%d")})
    ACEPTADOS.write_text("# Criterios dados por buenos por el farmacéutico: no vuelven a salir en «Para revisar».\n"
                         "# Si cambia el texto del criterio, la aceptación deja de aplicarse.\n"
                         + yaml.safe_dump(lista, allow_unicode=True, sort_keys=False))
    print(f"Aceptado {clave}: {v['criterio']}")


def solo_juez(carpeta: Path, casos: list[dict], modelo: str, segunda: str | None, comparar: Path | None):
    """Vuelve a juzgar una ejecución guardada con los criterios actuales, sin repetir las consultas."""
    por_id = {c["id"]: c for c in casos}
    guardados = sorted(p for p in carpeta.glob("*.json"))
    if not guardados:
        sys.exit(f"No hay resultados (*.json) en {carpeta}")
    resultados = []
    for i, ruta in enumerate(guardados, 1):
        r = json.loads(ruta.read_text())
        if r["id"] not in por_id:
            print(f"[{i}/{len(guardados)}] {r['id']}: ya no está en casos.yaml, se omite")
            continue
        r["caso"] = por_id[r["id"]]
        r["comprobaciones"] = comprobar(r["caso"], r)  # también con los criterios actuales
        print(f"[{i}/{len(guardados)}] {r['id']} …", end=" ", flush=True)
        r["juez"] = juzgar(r["caso"], r["respuesta"], modelo, segunda)
        print(_linea_juez(r["juez"]).lstrip(" ·") or "sin juez")
        _guardar(carpeta, r)
        resultados.append(r)
    print(f"\nInforme: {informe(carpeta, resultados, _commit() + ' (rejuzgado)', comparar)}")


def main():
    p = argparse.ArgumentParser(description="Evalúa el agente research con casos reales.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--caso", action="append", metavar="ID", help="id de un caso (repetible)")
    g.add_argument("--categoria", metavar="X", help="todos los casos de una categoría")
    g.add_argument("--todos", action="store_true", help="todos los casos")
    g.add_argument("--rapido", action="store_true", help="test rápido: los casos de la lista «rapido» de casos.yaml")
    g.add_argument("--solo-juez", metavar="CARPETA", type=Path,
                   help="vuelve a juzgar una ejecución guardada, sin repetir las consultas al agente")
    g.add_argument("--aceptar", metavar="CASO:ID",
                   help="da por bueno un criterio dudoso/incumplido para que no vuelva a salir")
    p.add_argument("--sin-juez", action="store_true", help="solo las comprobaciones automáticas")
    p.add_argument("--modelo-juez", default="sonnet", help="modelo del juez (por defecto: sonnet)")
    p.add_argument("--segunda-opinion", default="opus", metavar="MODELO",
                   help="modelo que vuelve a juzgar los dudosos (por defecto: opus; 'no' para desactivar)")
    p.add_argument("--comparar", type=Path, metavar="CARPETA",
                   help="ejecución con la que comparar (por defecto: la anterior de cada caso)")
    p.add_argument("--motivo", default="", help="con --aceptar: por qué se da por bueno")
    p.add_argument("--casos", type=Path, default=CASOS, help="archivo de casos (por defecto: evaluacion/casos.yaml)")
    args = p.parse_args()
    if args.solo_juez and args.sin_juez:
        sys.exit("--solo-juez y --sin-juez no tienen sentido juntos.")

    if args.aceptar:
        return aceptar(args.aceptar, args.motivo)
    segunda = None if args.segunda_opinion.lower() == "no" else args.segunda_opinion
    datos = yaml.safe_load(args.casos.read_text())
    casos = datos["casos"]
    if args.solo_juez:
        return solo_juez(args.solo_juez, casos, args.modelo_juez, segunda, args.comparar)
    if args.rapido:
        args.caso = datos.get("rapido") or sys.exit("casos.yaml no tiene lista «rapido».")
    if args.caso:
        desconocidos = set(args.caso) - {c["id"] for c in casos}
        if desconocidos:
            sys.exit(f"Caso(s) desconocido(s): {', '.join(desconocidos)}")
        casos = [c for c in casos if c["id"] in args.caso]
    elif args.categoria:
        casos = [c for c in casos if c["categoria"] == args.categoria]
        if not casos:
            sys.exit(f"No hay casos de la categoría «{args.categoria}».")

    carpeta = RESULTADOS / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    carpeta.mkdir(parents=True)
    commit = _commit()

    resultados = []
    # Secuencial a propósito: no saturar CIMA, NCBI ni e-lactancia.
    for i, caso in enumerate(casos, 1):
        print(f"[{i}/{len(casos)}] {caso['id']} …", end=" ", flush=True)
        r = lanzar(caso, carpeta)
        r.update(id=caso["id"], categoria=caso["categoria"], caso=caso)
        r["comprobaciones"] = comprobar(caso, r)
        r["juez"] = None if args.sin_juez else juzgar(caso, r["respuesta"], args.modelo_juez, segunda)
        _guardar(carpeta, r)
        ok = sum(c["ok"] for c in r["comprobaciones"])
        print(f"{ok}/{len(r['comprobaciones'])} ✅ en {r['duracion_s']:.0f} s{_linea_juez(r['juez'])}")
        resultados.append(r)

    print(f"\nInforme: {informe(carpeta, resultados, commit, args.comparar)}")


if __name__ == "__main__":
    main()
