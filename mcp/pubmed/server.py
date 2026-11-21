"""Servidor MCP de PubMed (NCBI E-utilities).

Usa esearch (buscar PMIDs), esummary (metadatos breves) y efetch (abstract, MeSH).
Variables de entorno opcionales: NCBI_API_KEY y NCBI_EMAIL.
Transporte stdio: stdout es el canal MCP, así que los logs van a stderr.
"""

import logging
import os
import sys
import threading
import time
import xml.etree.ElementTree as ET

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s pubmed %(levelname)s %(message)s")
log = logging.getLogger("pubmed")

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
FUENTE = "https://pubmed.ncbi.nlm.nih.gov/{}/"
PMC = "https://www.ncbi.nlm.nih.gov/pmc/articles/{}/"
TIMEOUT = 15.0
CACHE_TTL = 3600
MAX_RESULTADOS = 30
MAX_PMIDS = 10       # obtener_resumen: los abstracts ocupan mucho
MAX_ABSTRACT = 3000  # caracteres por abstract

API_KEY = os.environ.get("NCBI_API_KEY")
EMAIL = os.environ.get("NCBI_EMAIL")
# Límite del NCBI: 3 peticiones/s sin clave, 10/s con clave.
INTERVALO = 0.1 if API_KEY else 0.34

TIPOS = {
    "meta-analysis": "Meta-Analysis",
    "systematic review": "Systematic Review",
    "randomized controlled trial": "Randomized Controlled Trial",
    "review": "Review",
    "case reports": "Case Reports",
}

mcp = FastMCP("pubmed")
_cache: dict[str, tuple[float, object]] = {}
_client = httpx.Client(timeout=TIMEOUT)
_lock = threading.Lock()
_ultima = 0.0


def _esperar_turno():
    """Espacia las peticiones para no superar el límite del NCBI."""
    global _ultima
    with _lock:
        espera = _ultima + INTERVALO - time.monotonic()
        if espera > 0:
            time.sleep(espera)
        _ultima = time.monotonic()


def _get(utilidad: str, params: dict, json_: bool = True) -> object:
    """GET a una E-utility con caché TTL, timeout de 15 s y un reintento."""
    params = {"db": "pubmed", "tool": "farmaclaw", **params}
    if API_KEY:
        params["api_key"] = API_KEY
    if EMAIL:
        params["email"] = EMAIL
    key = utilidad + repr(sorted((k, v) for k, v in params.items() if k != "api_key"))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    for intento in (1, 2):
        _esperar_turno()
        try:
            r = _client.get(f"{BASE}/{utilidad}.fcgi", params=params)
            r.raise_for_status()
            data = r.json() if json_ else r.text
            _cache[key] = (time.time(), data)
            return data
        except httpx.TimeoutException:
            log.warning("timeout en %s (intento %d)", utilidad, intento)
            if intento == 2:
                raise ToolError("PubMed no ha respondido en 15 s (timeout). Inténtalo de nuevo más tarde.")
        except (httpx.HTTPError, ValueError) as e:
            # 429 = demasiadas peticiones: el reintento llega tras la espera.
            log.warning("error en %s (intento %d): %s", utilidad, intento, e)
            if intento == 2:
                raise ToolError(f"PubMed no está disponible o ha devuelto un error: {e}")
        time.sleep(1)


def _anio(pubdate: str) -> str | None:
    return pubdate[:4] if pubdate and pubdate[:4].isdigit() else None


@mcp.tool()
def buscar_articulos(
    consulta: str,
    max_resultados: int = 10,
    tipos: list[str] | None = None,
    desde_anio: int | None = None,
) -> dict:
    """Busca artículos en PubMed, ordenados por relevancia.

    consulta: términos en inglés funcionan mejor (p. ej. "clopidogrel omeprazole").
    tipos: filtra por tipo de publicación. Valores: "Meta-Analysis",
      "Systematic Review", "Randomized Controlled Trial", "Review", "Case Reports".
    desde_anio: solo artículos publicados desde ese año.
    Devuelve PMID, título, revista, año y tipos de publicación.
    """
    termino = f"({consulta.strip()})"
    if tipos:
        desconocidos = [t for t in tipos if t.strip().lower() not in TIPOS]
        if desconocidos:
            raise ToolError(f"Tipo(s) no válido(s): {', '.join(desconocidos)}. Usa: {', '.join(TIPOS.values())}")
        termino += " AND (" + " OR ".join(f'"{TIPOS[t.strip().lower()]}"[pt]' for t in tipos) + ")"
    if desde_anio:
        termino += f" AND {desde_anio}:3000[dp]"
    n = max(1, min(max_resultados, MAX_RESULTADOS))

    data = _get("esearch", {"term": termino, "retmax": n, "sort": "relevance", "retmode": "json"})
    res = data.get("esearchresult", {})
    if "ERROR" in res:
        raise ToolError(f"PubMed ha rechazado la búsqueda: {res['ERROR']}")
    pmids = res.get("idlist", [])
    if not pmids:
        raise ToolError(f"No se han encontrado artículos en PubMed para: {termino}")

    resumen = _get("esummary", {"id": ",".join(pmids), "retmode": "json"}).get("result", {})
    articulos = []
    for pmid in pmids:
        a = resumen.get(pmid, {})
        articulos.append({
            "pmid": pmid,
            "titulo": a.get("title"),
            "revista": a.get("source"),
            "anio": _anio(a.get("pubdate", "")),
            "tipos": a.get("pubtype", []),
            "fuente": FUENTE.format(pmid),
        })
    return {"consulta_pubmed": termino, "total_en_pubmed": int(res.get("count", 0)), "articulos": articulos}


def _texto(el: ET.Element | None) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _parsear(art: ET.Element) -> dict:
    cit = art.find("MedlineCitation")
    pmid = _texto(cit.find("PMID"))
    a = cit.find("Article")

    autores = []
    for au in a.findall("AuthorList/Author"):
        nombre = f"{_texto(au.find('LastName'))} {_texto(au.find('Initials'))}".strip() or _texto(au.find("CollectiveName"))
        if nombre:
            autores.append(nombre)
    if len(autores) > 3:
        autores = autores[:3] + ["et al."]

    partes = []
    for t in a.findall("Abstract/AbstractText"):
        etiqueta = t.get("Label")
        partes.append(f"{etiqueta}: {_texto(t)}" if etiqueta else _texto(t))
    abstract = "\n".join(partes)
    if len(abstract) > MAX_ABSTRACT:
        abstract = abstract[:MAX_ABSTRACT] + " […]"

    anio = _texto(a.find("Journal/JournalIssue/PubDate/Year")) or _texto(a.find("Journal/JournalIssue/PubDate/MedlineDate"))[:4]

    # Solo los ids del propio artículo (PubmedData/ArticleIdList), no los de la bibliografía.
    ids = {i.get("IdType"): _texto(i) for i in art.findall("PubmedData/ArticleIdList/ArticleId")}
    doi = ids.get("doi")
    pmc = ids.get("pmc")

    return {
        "pmid": pmid,
        "titulo": _texto(a.find("ArticleTitle")),
        "autores": ", ".join(autores),
        "revista": _texto(a.find("Journal/ISOAbbreviation")) or _texto(a.find("Journal/Title")),
        "anio": anio or None,
        "tipos": [_texto(p) for p in a.findall("PublicationTypeList/PublicationType")],
        "abstract": abstract or "Sin abstract disponible en PubMed.",
        "mesh": [_texto(m.find("DescriptorName")) for m in cit.findall("MeshHeadingList/MeshHeading")],
        "doi": doi,
        "doi_url": f"https://doi.org/{doi}" if doi else None,
        "texto_completo_pmc": PMC.format(pmc) if pmc else None,
        "fuente": FUENTE.format(pmid),
    }


@mcp.tool()
def obtener_resumen(pmids: list[str]) -> dict:
    """Obtiene el abstract y metadatos de uno o varios artículos (máx. 10 PMIDs).

    Devuelve título, autores abreviados, revista, año, abstract, términos MeSH,
    DOI y enlace al texto completo en PMC si existe.
    """
    pmids = [str(p).strip() for p in pmids if str(p).strip().isdigit()]
    if not pmids:
        raise ToolError("Indica al menos un PMID válido (solo dígitos).")
    if len(pmids) > MAX_PMIDS:
        raise ToolError(f"Máximo {MAX_PMIDS} PMIDs por llamada; has pedido {len(pmids)}.")

    xml = _get("efetch", {"id": ",".join(pmids), "retmode": "xml"}, json_=False)
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError:
        raise ToolError("PubMed ha devuelto una respuesta no válida. Inténtalo de nuevo.")

    articulos = [_parsear(art) for art in raiz.findall("PubmedArticle")]
    encontrados = {a["pmid"] for a in articulos}
    no_encontrados = [p for p in pmids if p not in encontrados]
    if not articulos:
        raise ToolError(f"No se ha encontrado ningún artículo con PMID: {', '.join(pmids)}")
    salida = {"articulos": articulos}
    if no_encontrados:
        salida["no_encontrados"] = no_encontrados
    return salida


if __name__ == "__main__":
    mcp.run()
