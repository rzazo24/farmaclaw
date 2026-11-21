"""Aplica a casos.yaml las marcas de la hoja de revisión de criterios.

La hoja (artifact «Revisión de criterios») guarda un documento por caso:
    {decisiones: {M1: {decision: vale|recortar|quitar, texto}, N1: {...}},
     anadir: ["criterio nuevo", ...], validado: bool, nota: str}

Claude descarga esos documentos como JSON en una carpeta y ejecuta:
    uv run python evaluacion/aplicar_revision.py CARPETA_CON_LOS_JSON

- vale: se mantiene · recortar: se sustituye el texto (la cita y la fuente se conservan)
- quitar: se elimina · anadir: se añaden a debe_mencionar (sin cita)
- validado: true → revisado: true (solo si todos los criterios tienen marca)
Las notas se muestran en pantalla para que Claude las lea; no se escriben en el YAML.
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

from ruamel.yaml import YAML

CASOS = Path(__file__).with_name("casos.yaml")
BORRADOR = re.compile(r"^\s*BORRADOR\s*-\s*revisar\s*:\s*", re.I)


def aplicar(caso, rev: dict) -> list[str]:
    """Modifica el caso en sitio y devuelve un resumen de cambios."""
    dec = rev.get("decisiones", {})
    cambios = []
    for prefijo, campo in (("M", "debe_mencionar"), ("N", "no_debe")):
        nueva = []
        for n, item in enumerate(caso.get(campo) or [], 1):
            d = dec.get(f"{prefijo}{n}", {})
            if d.get("decision") == "quitar":
                cambios.append(f"quitado {prefijo}{n}")
                continue
            if d.get("decision") == "recortar" and (d.get("texto") or "").strip():
                texto = d["texto"].strip()
                if isinstance(item, dict):
                    item["criterio"] = texto
                else:
                    item = texto
                cambios.append(f"recortado {prefijo}{n}")
            nueva.append(item)
        if campo in caso:
            caso[campo] = nueva
    for t in rev.get("anadir") or []:
        caso.setdefault("debe_mencionar", []).append(t.strip())
        cambios.append(f"añadido «{t.strip()}»")

    total = len(caso.get("debe_mencionar") or []) + len(caso.get("no_debe") or [])
    if rev.get("validado") and total:
        # Validado por el farmacéutico: los criterios dejan de ser borradores.
        for campo in ("debe_mencionar", "no_debe"):
            for i, item in enumerate(caso.get(campo) or []):
                if isinstance(item, str) and BORRADOR.match(item):
                    caso[campo][i] = BORRADOR.sub("", item)
        caso.pop("borrador_sin_fuente", None)  # solo servía de referencia para revisar
        caso["revisado"] = True
        caso["revisado_fecha"] = date.today().isoformat()
        caso["revisado"] = caso.pop("revisado")  # revisado al final
        cambios.append("marcado revisado: true")
    return cambios


def main():
    if len(sys.argv) != 2:
        sys.exit("Uso: aplicar_revision.py CARPETA_CON_LOS_JSON")
    docs = {p.stem: json.loads(p.read_text()) for p in Path(sys.argv[1]).rglob("*.json")}
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    datos = yaml.load(CASOS)
    for caso in datos["casos"]:
        rev = docs.get(caso["id"])
        if not rev:
            continue
        rev = rev.get("data", rev)  # admite el documento tal cual o envuelto en {data: ...}
        if caso.get("revisado"):
            print(f"{caso['id']}: ya estaba revisado, no se toca")
            continue
        cambios = aplicar(caso, rev)
        print(f"{caso['id']}: {', '.join(cambios) or 'sin cambios'}")
        if (rev.get("nota") or "").strip():
            print(f"    nota: {rev['nota'].strip()}")
    yaml.dump(datos, CASOS)


if __name__ == "__main__":
    main()
