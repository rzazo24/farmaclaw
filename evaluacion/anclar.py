"""Ancla los criterios de evaluación a las fuentes oficiales.

Para cada caso sin revisar, Claude consulta CIMA / LactMed / e-lactancia (solo los servidores
MCP del proyecto) y propone criterios "debe_mencionar" con la cita LITERAL que los respalda y
su URL. Python comprueba que cada cita y cada URL aparecen en lo que devolvieron las
herramientas en esa sesión; los que no, se descartan.

El resultado se escribe en casos.yaml (conservando comentarios y formato). Así la validación
del farmacéutico pasa de "¿esto es verdad?" a "¿esta frase de la fuente es la importante?".

Uso (desde la raíz del proyecto):
    uv run python evaluacion/anclar.py --caso int-omeprazol-clopidogrel
    uv run python evaluacion/anclar.py --categoria lactancia
    uv run python evaluacion/anclar.py --todos

Nunca toca casos con revisado: true. Se saltan los casos de comportamiento
(sin_evidencia, o sin criterios "BORRADOR"), cuyos criterios no salen de una fuente.
"""

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from ruamel.yaml import YAML

sys.path.insert(0, str(Path(__file__).parent))
import ejecutar as ev  # noqa: E402

PROMPT = Path(__file__).with_name("anclador.md")
TIMEOUT = 600
ESQUEMA = {
    "type": "object",
    "properties": {"criterios": {"type": "array", "items": {
        "type": "object",
        "properties": {"criterio": {"type": "string"}, "cita": {"type": "string"}, "fuente": {"type": "string"}},
        "required": ["criterio", "cita", "fuente"],
    }}},
    "required": ["criterios"],
}
COMANDO = [
    "claude", "-p",
    # Solo los servidores MCP del proyecto: ni herramientas integradas, ni web, ni otros MCP.
    "--tools", "", "--strict-mcp-config", "--mcp-config", ".mcp.json",
    "--allowedTools", "mcp__cima__*", "mcp__pubmed__*", "mcp__elactancia__*",
    "--permission-mode", "dontAsk", "--disable-slash-commands",
    "--output-format", "stream-json", "--verbose", "--json-schema", json.dumps(ESQUEMA),
    "--no-session-persistence",
]


def _cadenas(valor) -> list[str]:
    """Todas las cadenas dentro de un JSON (los resultados MCP son JSON con el texto escapado)."""
    if isinstance(valor, str):
        return [valor]
    if isinstance(valor, dict):
        return [s for v in valor.values() for s in _cadenas(v)]
    if isinstance(valor, list):
        return [s for v in valor for s in _cadenas(v)]
    return []


def texto_fuentes(llamadas: list[dict]) -> str:
    partes = []
    for l in llamadas:
        if not l["herramienta"].startswith("mcp__"):
            continue
        try:
            partes += _cadenas(json.loads(l["resultado"]))
        except json.JSONDecodeError:
            partes.append(l["resultado"])
    return "\n".join(partes)


def verificar(criterios: list[dict], fuentes: str) -> tuple[list[dict], list[dict]]:
    """Separa los criterios con cita literal y URL presentes en las fuentes de los que no."""
    validos, descartados = [], []
    fuentes_norm = ev._normalizar(fuentes)
    for c in criterios:
        url = c["fuente"].strip().strip("<>()[]")
        cita_ok = len(ev._normalizar(c["cita"])) >= 15 and ev._normalizar(c["cita"]) in fuentes_norm
        url_ok = url.startswith("http") and url.rstrip("/") in fuentes
        if cita_ok and url_ok:
            validos.append({"criterio": c["criterio"].strip(), "cita": c["cita"].strip(), "fuente": url})
        else:
            motivo = "cita no literal" if not cita_ok else "URL no devuelta por ninguna herramienta"
            descartados.append({**c, "motivo": motivo})
    return validos, descartados


def anclar(caso: dict, modelo: str) -> dict:
    borradores = [ev.PREFIJO_BORRADOR.sub("", m) for m in caso.get("debe_mencionar", []) if isinstance(m, str)]
    entrada = json.dumps({"pregunta": caso["pregunta"], "categoria": caso["categoria"],
                          "criterios_borrador": borradores}, ensure_ascii=False, indent=1)
    try:
        proc = subprocess.run(COMANDO + ["--system-prompt", PROMPT.read_text(), "--model", modelo, entrada],
                              cwd=ev.RAIZ, capture_output=True, text=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL)
        salida = proc.stdout
    except subprocess.TimeoutExpired:
        return {"error": f"timeout ({TIMEOUT} s)"}
    llamadas = ev.analizar(salida)["llamadas"]
    estructurada, coste = None, 0.0
    for linea in salida.splitlines():
        try:
            e = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "result":
            estructurada, coste = e.get("structured_output"), e.get("total_cost_usd") or 0
    if not estructurada:
        return {"error": "sin salida estructurada", "coste_usd": coste}
    validos, descartados = verificar(estructurada.get("criterios", []), texto_fuentes(llamadas))
    return {"validos": validos, "descartados": descartados, "coste_usd": coste,
            "herramientas": [ev._corto(l["herramienta"]) for l in llamadas if l["herramienta"].startswith("mcp__")]}


def anclable(caso: dict) -> str | None:
    """Motivo para NO anclar un caso, o None si se puede."""
    if caso.get("revisado"):
        return "ya revisado"
    if caso["categoria"] == "sin_evidencia":
        return "caso de comportamiento (sin_evidencia)"
    if not any(isinstance(m, str) and ev.PREFIJO_BORRADOR.match(m) for m in caso.get("debe_mencionar", [])):
        return "sin criterios BORRADOR (ya anclado o criterios de comportamiento)"
    return None


def main():
    p = argparse.ArgumentParser(description="Ancla los criterios de casos.yaml a las fuentes.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--caso", action="append", metavar="ID")
    g.add_argument("--categoria", metavar="X")
    g.add_argument("--todos", action="store_true")
    p.add_argument("--modelo", default="sonnet", help="modelo del extractor (por defecto: sonnet)")
    p.add_argument("--casos", type=Path, default=ev.CASOS)
    args = p.parse_args()

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)  # misma sangría que casos.yaml
    datos = yaml.load(args.casos)
    casos = datos["casos"]
    if args.caso:
        casos = [c for c in casos if c["id"] in args.caso]
    elif args.categoria:
        casos = [c for c in casos if c["categoria"] == args.categoria]

    total = 0.0
    for i, caso in enumerate(casos, 1):
        motivo = anclable(caso)
        if motivo:
            print(f"[{i}/{len(casos)}] {caso['id']}: se salta ({motivo})")
            continue
        print(f"[{i}/{len(casos)}] {caso['id']} …", end=" ", flush=True)
        r = anclar(caso, args.modelo)
        total += r.get("coste_usd", 0)
        if "error" in r or not r["validos"]:
            print(f"sin cambios ({r.get('error') or 'ningún criterio con cita verificada'})")
            for d in r.get("descartados", []):
                print(f"    descartado ({d['motivo']}): {d['criterio']}")
            continue
        # Los borradores sin fuente se guardan aparte (no se juzgan) para que se vea qué se perdió.
        caso["borrador_sin_fuente"] = [m for m in caso["debe_mencionar"] if isinstance(m, str)]
        caso["debe_mencionar"] = r["validos"]
        caso["anclado"] = date.today().isoformat()
        caso["revisado"] = caso.pop("revisado")  # revisado al final, donde se busca para validar
        print(f"{len(r['validos'])} criterio(s) anclados, {len(r['descartados'])} descartado(s) · "
              f"fuentes: {', '.join(sorted(set(r['herramientas'])))}")
        for d in r["descartados"]:
            print(f"    descartado ({d['motivo']}): {d['criterio']}")
        yaml.dump(datos, args.casos)  # se guarda tras cada caso: si se corta, no se pierde lo hecho

    print(f"\nCoste: {total:.2f} USD. Revisa los criterios en {args.casos} y pon revisado: true.")


if __name__ == "__main__":
    main()
