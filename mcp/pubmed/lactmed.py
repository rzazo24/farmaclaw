"""LactMed (NCBI): índice local de fármacos y lactancia.

La web de LactMed bloquea los accesos automáticos (reCAPTCHA), así que se usa la
descarga oficial completa del FTP del NCBI y se guarda un índice comprimido en
data/lactmed.json.gz. Las consultas se resuelven en local, sin red.

Actualizar el índice (descarga ~200 MB, tarda 1-2 min):
    uv run python mcp/pubmed/lactmed.py
"""

import gzip
import io
import json
import logging
import re
import sys
import tarfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

log = logging.getLogger("lactmed")

URL_FTP = "https://ftp.ncbi.nlm.nih.gov/pub/litarch/file_list.csv"
BASE_FTP = "https://ftp.ncbi.nlm.nih.gov/pub/litarch/"
INDICE = Path(__file__).resolve().parents[2] / "data" / "lactmed.json.gz"
FUENTE = "https://www.ncbi.nlm.nih.gov/books/n/lactmed/{}/"

# Título de la sección en LactMed → clave corta
SECCIONES = {
    "Summary of Use during Lactation": "resumen",
    "Drug Levels": "niveles",
    "Effects in Breastfed Infants": "efectos_lactante",
    "Effects on Lactation and Breastmilk": "efectos_lactancia",
    "Alternate Drugs to Consider": "alternativas",
    "Drug Class": "clase",
}


def _texto(el: ET.Element, pmids: dict[str, str]) -> str:
    """Texto de un elemento, con las citas [1-9] sustituidas por sus PMIDs."""
    partes = []

    def recorrer(e: ET.Element):
        if e.tag == "xref" and e.get("ref-type") == "bibr":
            ids = [pmids[r] for r in e.get("rid", "").split() if r in pmids]
            partes.append(f"[PMID: {', '.join(ids)}]" if ids else "")
        elif e.tag in ("title", "ref-list"):
            pass
        else:
            if e.text:
                partes.append(e.text)
            for hijo in e:
                recorrer(hijo)
                if hijo.tag in ("p", "list-item"):
                    partes.append("\n")
        if e.tail:
            partes.append(e.tail)

    for hijo in el:
        recorrer(hijo)
    texto = re.sub(r"[ \t\r\n]*\n[ \t\r\n]*", "\n", "".join(partes))
    texto = re.sub(r"\[(\[PMID: [^\]]*\])\]", r"\1", texto)  # el XML ya trae corchetes alrededor de la cita
    return re.sub(r"[ \t]+", " ", texto).strip()


def _parsear(contenido: bytes) -> dict | None:
    raiz = ET.fromstring(contenido)
    titulo = raiz.findtext(".//book-part-meta/title-group/title") or raiz.findtext(".//title")
    pmids = {}
    for ref in raiz.iter("ref"):
        pmid = ref.find(".//pub-id[@pub-id-type='pmid']")
        if pmid is not None and pmid.text:
            pmids[ref.get("id")] = pmid.text.strip()

    secciones = {}
    for sec in raiz.iter("sec"):
        clave = SECCIONES.get((sec.findtext("title") or "").strip())
        if clave:
            secciones[clave] = _texto(sec, pmids)
    if "resumen" not in secciones:
        return None  # páginas auxiliares (glosario, aviso legal...), no fármacos

    rev = raiz.find(".//date[@date-type='revised']")
    revisado = None
    if rev is not None:
        revisado = f"{rev.findtext('year')}-{int(rev.findtext('month') or 1):02d}-{int(rev.findtext('day') or 1):02d}"
    return {"id": raiz.get("id"), "titulo": titulo.strip(), "revisado": revisado, "secciones": secciones}


def actualizar() -> int:
    """Descarga LactMed del FTP del NCBI y regenera el índice local."""
    with httpx.Client(timeout=120, follow_redirects=True) as c:
        lista = c.get(URL_FTP).text
        ruta = next(l.split(",")[0] for l in lista.splitlines() if "NBK501922" in l)
        log.info("descargando %s", ruta)
        tgz = c.get(BASE_FTP + ruta).content

    fichas = []
    with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tar:
        for m in tar:
            if m.isfile() and m.name.endswith(".nxml"):
                ficha = _parsear(tar.extractfile(m).read())
                if ficha:
                    fichas.append(ficha)

    INDICE.parent.mkdir(exist_ok=True)
    datos = {"generado": time.strftime("%Y-%m-%d"), "fichas": fichas}
    with gzip.open(INDICE, "wt", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False)
    return len(fichas)


# LactMed usa nombres estadounidenses (USAN); equivalencias con los europeos (DCI) habituales.
SINONIMOS = {
    "paracetamol": "acetaminophen", "salbutamol": "albuterol", "adrenaline": "epinephrine",
    "noradrenaline": "norepinephrine", "glibenclamide": "glyburide", "pethidine": "meperidine",
    "ciclosporin": "cyclosporine", "rifampicin": "rifampin", "colecalciferol": "vitamin d",
    "cholecalciferol": "vitamin d", "frusemide": "furosemide", "lignocaine": "lidocaine",
    "metamizole": "dipyrone", "metamizol": "dipyrone", "hyoscine": "scopolamine",
    "valproate": "valproic acid", "sodium valproate": "valproic acid",
    "amoxicillin clavulanate": "amoxicillin and clavulanic acid",
    "amoxicillin-clavulanate": "amoxicillin and clavulanic acid",
}

_cache: dict | None = None


def cargar() -> dict | None:
    """Carga el índice (una vez por proceso). None si no existe."""
    global _cache
    if _cache is None and INDICE.exists():
        with gzip.open(INDICE, "rt", encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


def buscar(nombre: str) -> list[dict]:
    """Coincidencia exacta del título; si no, los que empiezan por o contienen el nombre."""
    datos = cargar() or {"fichas": []}
    q = nombre.strip().lower()
    q = SINONIMOS.get(q, q)
    fichas = datos["fichas"]
    criterios = [lambda t: t == q, lambda t: t.startswith(q)]
    if len(q) >= 5:  # con textos cortos "contiene" da falsos positivos ("xyz" → Hydroxyzine)
        criterios.append(lambda t: q in t)
    for criterio in criterios:
        encontradas = [f for f in fichas if criterio(f["titulo"].lower())]
        if encontradas:
            return sorted(encontradas, key=lambda f: len(f["titulo"]))
    return []


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s lactmed %(message)s")
    n = actualizar()
    print(f"Índice LactMed actualizado: {n} fármacos en {INDICE}")
