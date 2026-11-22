"""Texto de artículos en PDF (copias de acceso abierto que encuentra Unpaywall).

Solo procesado (sin red). Un PDF no tiene estructura: las secciones se detectan por
los títulos habituales (Methods, Results…) y las tablas no se recuperan de forma fiable.
"""

import io
import re

from pypdf import PdfReader

import pmc

MAX_PAGINAS = 60

# Títulos de sección habituales, solos en su línea (con o sin numeración: «2. Methods»).
TITULOS = [
    "abstract", "summary", "background", "introduction", "methods", "method", "materials and methods",
    "patients and methods", "methodology", "results", "discussion", "conclusion", "conclusions",
    "limitations", "acknowledgements", "acknowledgments", "funding", "conflict of interest",
    "conflicts of interest", "competing interests", "declaration of competing interest",
    "declaration of interests", "disclosures", "disclosure", "references", "bibliography",
]
TITULO = re.compile(r"^\s*(?:\d{1,2}(?:\.\d)*\.?\s+)?(" + "|".join(map(re.escape, TITULOS)) + r")\s*:?\s*$", re.I)
BIBLIOGRAFIA = {"references", "bibliography"}


def texto_pdf(datos: bytes) -> str:
    """Texto plano del PDF (como mucho MAX_PAGINAS páginas), con los guiones de fin de línea unidos."""
    lector = PdfReader(io.BytesIO(datos))
    paginas = [p.extract_text() or "" for p in lector.pages[:MAX_PAGINAS]]
    texto = "\n".join(paginas)
    texto = re.sub(r"(\w)-\n(\w)", r"\1\2", texto)           # pala-\nbra → palabra
    return texto.replace("­", "").replace("\r", "")


def secciones(texto: str) -> list[tuple[str, str]]:
    """Divide por títulos de sección. Omite la bibliografía pero conserva las declaraciones que vengan detrás."""
    trozos: list[tuple[str, list[str]]] = [("Inicio", [])]
    for linea in texto.split("\n"):
        m = TITULO.match(linea)
        if m:
            trozos.append((m.group(1).strip().capitalize(), []))
        else:
            trozos[-1][1].append(linea)
    resultado = []
    for titulo, lineas in trozos:
        cuerpo = _parrafos(lineas)
        if titulo.lower() in BIBLIOGRAFIA or not cuerpo:
            continue
        resultado.append((titulo, f"## {titulo}\n\n{cuerpo}" if titulo != "Inicio" else cuerpo))
    return resultado


def _parrafos(lineas: list[str]) -> str:
    """Une las líneas cortadas del PDF en párrafos (corte cuando la línea termina en punto y la siguiente empieza en mayúscula)."""
    texto = "\n".join(l.strip() for l in lineas).strip()
    texto = re.sub(r"(?<![.:;?!])\n(?!\n)", " ", texto)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{2,}", "\n\n", texto)).strip()


def parsear(datos: bytes) -> dict:
    """Mismo formato que pmc.parsear: secciones, tablas (ninguna) y declaraciones."""
    if not datos.startswith(b"%PDF"):
        raise ValueError("el archivo descargado no es un PDF")
    texto = texto_pdf(datos)
    if len(texto.strip()) < 500:
        raise ValueError("el PDF no tiene texto extraíble (puede ser una imagen escaneada)")
    secs = secciones(texto)
    if len(secs) <= 1:  # sin títulos reconocibles: todo como un único bloque
        secs = [("Texto", _parrafos(texto.split("\n")))]
    declaraciones = "\n\n".join(x for t, x in secs if pmc.DECLARACION.search(t) or t.lower().startswith("acknowledg"))
    return {"secciones": secs, "tablas": [], "declaraciones": declaraciones, "bloqueado": False}
