"""Prueba rápida del MCP de e-lactancia con casos reales.

Uso: uv run python mcp/elactancia/test_elactancia.py
Llama a la herramienta directamente (sin cliente MCP) e imprime un resumen.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import server as s  # noqa: E402


def ver(titulo, fn, *args):
    print(f"\n=== {titulo} ===")
    try:
        r = fn(*args)
        txt = json.dumps(r, ensure_ascii=False, indent=1)
        print(txt[:1200] + ("\n…" if len(txt) > 1200 else ""))
        return r
    except Exception as e:
        print(f"ERROR ({type(e).__name__}): {e}")


ver("ibuprofeno (riesgo 0)", s.elactancia, "ibuprofeno")
ver("amiodarona (riesgo alto, con alternativas)", s.elactancia, "amiodarona")
ver("varios candidatos", s.elactancia, "ibupro")
ver("marca comercial (debe remitir a CIMA)", s.elactancia, "Couldina con Ibuprofeno")
ver("error esperado: inexistente", s.elactancia, "farmaclawina")
