"""Prueba rápida del MCP de CIMA con casos reales.

Uso: uv run python mcp/cima/test_cima.py
Llama a las herramientas directamente (sin cliente MCP) e imprime un resumen.
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
        print(txt[:900] + ("\n…" if len(txt) > 900 else ""))
        return r
    except Exception as e:
        print(f"ERROR ({type(e).__name__}): {e}")


ibu = ver("buscar_medicamento('ibuprofeno')", s.buscar_medicamento, "ibuprofeno", True)
nreg = ibu["medicamentos"][0]["nregistro"] if ibu else "51347"
ver(f"ficha 4.3 de {nreg}", s.ficha_tecnica_seccion, nreg, "4.3")
ver(f"prospecto 'efectos adversos' de {nreg}", s.prospecto_seccion, nreg, "efectos adversos")

# Caso de interacción: omeprazol + clopidogrel → leer 4.5 de ambos
for farmaco in ("omeprazol", "clopidogrel"):
    r = s.buscar_medicamento(farmaco, True)
    n = r["medicamentos"][0]["nregistro"]
    res = ver(f"{farmaco}: ficha 4.5 interacciones ({n})", s.ficha_tecnica_seccion, n, "interacciones")
    if res:
        print("  ¿menciona al otro fármaco?:", ("clopidogrel" if farmaco == "omeprazol" else "omeprazol") in res["texto"].lower())

ver("problemas_suministro('80477')", s.problemas_suministro, "80477")
ver("problemas_suministro('citicolina')", s.problemas_suministro, "citicolina")
ver("buscar por CN de la caja (712729.8, Aspirina C)", s.buscar_medicamento, "712729.8")
ver("problemas_suministro por CN (730228)", s.problemas_suministro, "730228")
ver("notas_seguridad ácido valproico (68033)", s.notas_seguridad, "68033")
ver("notas_seguridad sin notas (clopidogrel 71774)", s.notas_seguridad, "71774")
ver("materiales_informativos ácido valproico (68033)", s.materiales_informativos, "68033")
ver("error esperado: nregistro inexistente", s.ficha_tecnica_seccion, "999999999", "4.3")
ver("error esperado: sección inexistente", s.ficha_tecnica_seccion, nreg, "9.9")
