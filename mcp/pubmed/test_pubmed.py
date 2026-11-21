"""Prueba rápida del MCP de PubMed con casos reales.

Uso: uv run python mcp/pubmed/test_pubmed.py
Llama a las herramientas directamente (sin cliente MCP) e imprime un resumen.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import server as s  # noqa: E402


def ver(titulo, fn, *args, **kwargs):
    print(f"\n=== {titulo} ===")
    try:
        r = fn(*args, **kwargs)
        txt = json.dumps(r, ensure_ascii=False, indent=1)
        print(txt[:1200] + ("\n…" if len(txt) > 1200 else ""))
        return r
    except Exception as e:
        print(f"ERROR ({type(e).__name__}): {e}")


r = ver("buscar: clopidogrel omeprazole, metaanálisis/RS desde 2015",
        s.buscar_articulos, "clopidogrel omeprazole", 5, ["Meta-Analysis", "Systematic Review"], 2015)
ver("buscar: ibuprofen, ensayos", s.buscar_articulos, "ibuprofen pain", 3, ["Randomized Controlled Trial"])
if r:
    ver("obtener_resumen de los 2 primeros", s.obtener_resumen, [a["pmid"] for a in r["articulos"][:2]])
ver("obtener_resumen con PMC (PMID 23671339)", s.obtener_resumen, ["23671339"])
ver("error esperado: tipo inválido", s.buscar_articulos, "aspirin", tipos=["Blog"])
ver("error esperado: sin resultados", s.buscar_articulos, "xqzwvklmnop farmaclaw")
ver("error esperado: PMID inexistente", s.obtener_resumen, ["999999999"])
