"""Evaluación del agente research: lanza casos reales con `claude -p` y genera un informe.

Uso (desde la raíz del proyecto):
    uv run python evaluacion/ejecutar.py --caso int-omeprazol-clopidogrel
    uv run python evaluacion/ejecutar.py --categoria lactancia
    uv run python evaluacion/ejecutar.py --todos

Cada ejecución crea evaluacion/resultados/AAAA-MM-DD_HHMMSS/ con:
    <id>.json      pregunta, respuesta, herramientas llamadas, duración, coste y comprobaciones
    <id>.jsonl     salida stream-json completa de Claude Code (para depurar)
    informe.md     todas las respuestas, comprobaciones automáticas y casillas de revisión manual
"""

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
CASOS = Path(__file__).with_name("casos.yaml")
RESULTADOS = Path(__file__).with_name("resultados")
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
        proc = subprocess.run(COMANDO + [prompt], cwd=RAIZ, capture_output=True, text=True, timeout=TIMEOUT_CASO)
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
        return pedida == req["seccion"] or pedida.startswith(req["seccion"] + ".")
    return True


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
        etiqueta = req["herramienta"] + (f" {req['seccion']}" if "seccion" in req else "")
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


# ───────────────────────── Informe ─────────────────────────

def _corto(nombre: str) -> str:
    return nombre.split("__")[-1] if nombre.startswith("mcp__") else nombre


def informe(carpeta: Path, resultados: list[dict], commit: str) -> Path:
    total = sum(len(r["comprobaciones"]) for r in resultados)
    ok = sum(c["ok"] for r in resultados for c in r["comprobaciones"])
    duracion = sum(r["duracion_s"] for r in resultados)
    coste = sum(r["coste_usd"] or 0 for r in resultados)
    l = [
        f"# Evaluación del agente research: {carpeta.name}",
        "",
        f"- Commit: `{commit}`",
        f"- Casos: {len(resultados)} · Comprobaciones automáticas: {ok}/{total} ✅",
        f"- Duración total: {duracion / 60:.1f} min · Coste: {coste:.2f} USD",
        "",
        "| Caso | Categoría | Automáticas | Duración | Revisión manual |",
        "|---|---|---|---|---|",
    ]
    for r in resultados:
        c = r["comprobaciones"]
        l.append(f"| [{r['id']}](#{r['id']}) | {r['categoria']} | {sum(x['ok'] for x in c)}/{len(c)} "
                 f"| {r['duracion_s']:.0f} s | ☐ |")

    for r in resultados:
        caso = r["caso"]
        l += ["", "---", "", f"## {r['id']}", "",
              f"**Categoría:** {r['categoria']} · **Duración:** {r['duracion_s']:.0f} s"
              + (f" · **Coste:** {r['coste_usd']:.3f} USD" if r["coste_usd"] else "")
              + ("" if caso.get("revisado") else " · ⚠️ criterios sin revisar"), "",
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
        l += ["", "### Revisión manual", "", "**Debe mencionar:**", ""]
        l += [f"- [ ] {m}" for m in caso.get("debe_mencionar", [])]
        l += ["", "**No debe:**", ""]
        l += [f"- [ ] Evita: {m}" for m in caso.get("no_debe", [])]
        l += ["", "**Notas:** ", "", "### Respuesta", ""]
        l += ["> " + linea if linea else ">" for linea in (r["respuesta"] or "(sin respuesta)").splitlines()]
    ruta = carpeta / "informe.md"
    ruta.write_text("\n".join(l) + "\n")
    return ruta


# ───────────────────────── Principal ─────────────────────────

def main():
    p = argparse.ArgumentParser(description="Evalúa el agente research con casos reales.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--caso", action="append", metavar="ID", help="id de un caso (repetible)")
    g.add_argument("--categoria", metavar="X", help="todos los casos de una categoría")
    g.add_argument("--todos", action="store_true", help="todos los casos")
    args = p.parse_args()

    casos = yaml.safe_load(CASOS.read_text())["casos"]
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
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=RAIZ, capture_output=True, text=True).stdout.strip()
    sucio = subprocess.run(["git", "status", "--porcelain"], cwd=RAIZ, capture_output=True, text=True).stdout.strip()
    commit += " (con cambios sin commit)" if sucio else ""

    resultados = []
    # Secuencial a propósito: no saturar CIMA, NCBI ni e-lactancia.
    for i, caso in enumerate(casos, 1):
        print(f"[{i}/{len(casos)}] {caso['id']} …", end=" ", flush=True)
        r = lanzar(caso, carpeta)
        r.update(id=caso["id"], categoria=caso["categoria"], caso=caso)
        r["comprobaciones"] = comprobar(caso, r)
        guardar = {k: v for k, v in r.items() if k != "caso"} | {"pregunta": caso["pregunta"]}
        (carpeta / f"{caso['id']}.json").write_text(json.dumps(guardar, ensure_ascii=False, indent=2))
        ok = sum(c["ok"] for c in r["comprobaciones"])
        print(f"{ok}/{len(r['comprobaciones'])} ✅ en {r['duracion_s']:.0f} s")
        resultados.append(r)

    print(f"\nInforme: {informe(carpeta, resultados, commit)}")


if __name__ == "__main__":
    main()
