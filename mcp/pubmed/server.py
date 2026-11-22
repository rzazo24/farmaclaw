"""Servidor MCP de PubMed (NCBI E-utilities).

Usa esearch (buscar PMIDs), esummary (metadatos breves), efetch (abstract, MeSH) y, para el
texto completo: PubMed Central (elink + efetch db=pmc), Europe PMC y Unpaywall (PDF abiertos).
Variables de entorno opcionales: NCBI_API_KEY, NCBI_EMAIL y UNPAYWALL_EMAIL (Unpaywall exige un correo).
Transporte stdio: stdout es el canal MCP, así que los logs van a stderr.
"""

import ipaddress
import logging
import os
import re
import socket
import sys
import threading
import time
import xml.etree.ElementTree as ET

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

import lactmed as lactmed_idx  # mismo directorio: Python añade la carpeta del script a sys.path
import pdf as pdf_txt
import pmc as pmc_xml

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s pubmed %(levelname)s %(message)s")
log = logging.getLogger("pubmed")
logging.getLogger("pypdf").setLevel(logging.ERROR)  # avisos de fuentes de los PDF: ruido sin utilidad

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
FUENTE = "https://pubmed.ncbi.nlm.nih.gov/{}/"
PMC = "https://www.ncbi.nlm.nih.gov/pmc/articles/{}/"
FUENTE_LACTMED = lactmed_idx.FUENTE
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


SECCIONES_LACTMED = {
    "resumen": "Resumen de uso en la lactancia + alternativas + clase",
    "niveles": "Niveles en leche materna y en el lactante",
    "efectos_lactante": "Efectos descritos en lactantes",
    "efectos_lactancia": "Efectos sobre la producción de leche",
    "alternativas": "Fármacos alternativos a considerar",
}
MAX_LACTMED = 8000  # caracteres por parte (LactMed y texto completo de PMC); las secciones pueden pasar de 10.000


def _partir(texto: str) -> list[str]:
    """Divide en partes de hasta MAX_LACTMED caracteres, cortando por párrafos."""
    partes = []
    while len(texto) > MAX_LACTMED:
        corte = texto.rfind("\n", 0, MAX_LACTMED)
        if corte < MAX_LACTMED // 2:
            corte = MAX_LACTMED
        partes.append(texto[:corte].strip())
        texto = texto[corte:].strip()
    partes.append(texto)
    return partes


@mcp.tool()
def lactmed(farmaco: str, seccion: str = "resumen", parte: int = 1) -> dict:
    """Consulta LactMed (NCBI), la base de datos de referencia de fármacos y lactancia.

    farmaco: nombre en INGLÉS (DCI o nombre estadounidense), p. ej. "sertraline",
      "valproic acid", "acetaminophen". Acepta también paracetamol, salbutamol,
      metamizole y otras equivalencias habituales.
    seccion: "resumen" (por defecto: resumen de uso, alternativas y clase),
      "niveles", "efectos_lactante", "efectos_lactancia" o "alternativas".
    parte: las secciones largas se devuelven por partes; si total_partes > 1,
      pide parte=2, 3...
    Las citas del texto vienen como [PMID: ...] y se pueden ampliar con obtener_resumen.
    """
    if lactmed_idx.cargar() is None:
        raise ToolError("El índice local de LactMed no existe. Créalo con: uv run python mcp/pubmed/lactmed.py")
    if seccion not in SECCIONES_LACTMED:
        raise ToolError(f"Sección no válida: {seccion}. Usa: {', '.join(SECCIONES_LACTMED)}")

    encontradas = lactmed_idx.buscar(farmaco)
    if not encontradas:
        raise ToolError(f"No he encontrado «{farmaco}» en LactMed. Prueba con el nombre en inglés (DCI o USAN).")
    q = lactmed_idx.SINONIMOS.get(farmaco.strip().lower(), farmaco.strip().lower())
    if len(encontradas) > 1 and encontradas[0]["titulo"].lower() != q:
        return {"aviso": "Varias fichas coinciden; repite la consulta con el nombre exacto.",
                "candidatos": [f["titulo"] for f in encontradas[:15]]}
    f = encontradas[0]
    sec = f["secciones"]

    if seccion == "resumen":
        texto = sec.get("resumen", "")
        partes = [texto]
        extra = {"alternativas": sec.get("alternativas") or "LactMed no propone alternativas.",
                 "clase": sec.get("clase", "").replace("\n", ", ")}
    else:
        texto = sec.get(seccion)
        if not texto:
            raise ToolError(f"La ficha de {f['titulo']} en LactMed no tiene la sección «{seccion}».")
        partes = _partir(texto)
        extra = {}
    if not 1 <= parte <= len(partes):
        raise ToolError(f"La sección {seccion} tiene {len(partes)} parte(s); has pedido la parte {parte}.")
    aviso = f"\n[… continúa: pide parte={parte + 1} de {len(partes)}]" if parte < len(partes) else ""

    return {
        "farmaco": f["titulo"],
        "seccion": seccion,
        "parte": parte,
        "total_partes": len(partes),
        "texto": partes[parte - 1] + aviso,
        **extra,
        "secciones_disponibles": [k for k in SECCIONES_LACTMED if k in sec or k == "resumen"],
        "revisado_lactmed": f["revisado"],
        "indice_local_generado": lactmed_idx.cargar()["generado"],
        "fuente": FUENTE_LACTMED.format(f["id"]),
    }


# ───────────────────────── Texto completo: PMC → Europe PMC → Unpaywall ─────────────────────────

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
EPMC_WEB = "https://europepmc.org/article/{}/{}"
UNPAYWALL = "https://api.unpaywall.org/v2/{}"
UNPAYWALL_EMAIL = os.environ.get("UNPAYWALL_EMAIL")
USER_AGENT = "farmaclaw/0.1 (+https://github.com/rzazo24/farmaclaw)"
MAX_PDF = 25 * 1024 * 1024
_web = httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT})
_docs: dict[str, tuple[float, dict]] = {}  # artículo → documento ya procesado (para pedir secciones sin repetir descargas)


class SinRespuesta:
    """El servicio no ha respondido (timeout o error de red): distinto de «no existe»."""

    def __bool__(self):
        return False


SIN_RESPUESTA = SinRespuesta()


def _web_get(url: str, params: dict | None = None, tipo: str = "json"):
    """GET a Europe PMC, Unpaywall o al PDF, con timeout de 15 s y un reintento.
    Devuelve None si no existe (404) y SIN_RESPUESTA si el servicio no responde o da error (5xx)."""
    for intento in (1, 2):
        try:
            r = _web.get(url, params=params)
            if 400 <= r.status_code < 500:  # no existe o no se permite (403): reintentar no sirve
                return None
            if r.status_code >= 500:  # Europe PMC da 500 a ratos con el mismo artículo: se reintenta
                log.warning("error %d en %s (intento %d)", r.status_code, url, intento)
                time.sleep(1)
                continue
            r.raise_for_status()
            if tipo == "json":
                return r.json()
            if tipo == "bytes":
                if len(r.content) > MAX_PDF:
                    raise ToolError("El PDF supera 25 MB; no lo descargo.")
                return r.content
            return r.text
        except httpx.TimeoutException:
            log.warning("timeout en %s (intento %d)", url, intento)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("error en %s (intento %d): %s", url, intento, e)
        time.sleep(1)
    return SIN_RESPUESTA


def _url_publica(url: str) -> bool:
    """Evita que un enlace externo apunte a la propia máquina o a la red local."""
    try:
        p = httpx.URL(url)
        if p.scheme not in ("http", "https") or not p.host:
            return False
        for info in socket.getaddrinfo(p.host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        return True
    except (OSError, ValueError):
        return False


def _identificar(articulo: str) -> dict:
    """Normaliza el identificador: PMID, PMCID o DOI."""
    a = articulo.strip().replace(" ", "")
    a = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", a, flags=re.I)
    if a.upper().startswith("PMC") and a[3:].isdigit():
        return {"pmcid": a.upper()}
    if a.isdigit():
        return {"pmid": a}
    if a.startswith("10.") and "/" in a:
        return {"doi": a}
    raise ToolError("Indica un PMID (solo dígitos), un PMCID (p. ej. PMC12581046) o un DOI (10.xxxx/…).")


def _desde_pmc(ids: dict, intentos: list[str]) -> dict | None:
    if "pmcid" in ids:
        numero = ids["pmcid"][3:]
    elif "pmid" in ids:
        data = _get("elink", {"dbfrom": "pubmed", "db": "pmc", "linkname": "pubmed_pmc", "id": ids["pmid"], "retmode": "json"})
        enlaces = (data.get("linksets") or [{}])[0].get("linksetdbs") or []
        numero = str(enlaces[0]["links"][0]) if enlaces and enlaces[0].get("links") else None
        if not numero:
            intentos.append("PubMed Central: el artículo no está")
            return None
    else:
        return None
    xml = _get("efetch", {"db": "pmc", "id": numero, "retmode": "xml"}, json_=False)
    try:
        d = pmc_xml.parsear(xml)
    except ValueError:
        intentos.append("PubMed Central: respuesta no válida")
        return None
    pmcid = d["pmcid"] or f"PMC{numero}"
    ids.setdefault("pmcid", pmcid)
    ids["pmid"] = ids.get("pmid") or d["pmid"]
    if d["bloqueado"]:
        intentos.append(f"PubMed Central: está ({pmcid}), pero la editorial no permite descargar el texto")
        ids["pmc_web"] = PMC.format(pmcid)
        return None
    return d | {"origen": "PubMed Central", "fuente": PMC.format(pmcid), "cita": pmcid}


def _ficha_europepmc(ids: dict) -> dict | None:
    """Registro de Europe PMC del artículo (sirve también para pasar de DOI a PMID/PMCID). Se guarda en ids."""
    if "_epmc" in ids:
        return ids["_epmc"]
    if "pmcid" in ids:
        consulta = f"PMCID:{ids['pmcid']}"
    elif "pmid" in ids:
        consulta = f"EXT_ID:{ids['pmid']} AND SRC:MED"
    else:
        consulta = f'DOI:"{ids["doi"]}"'
    data = _web_get(f"{EPMC}/search", {"query": consulta, "format": "json", "resultType": "core"})
    ids["_epmc_caido"] = data is SIN_RESPUESTA
    res = ((data or {}).get("resultList") or {}).get("result") or []
    ids["_epmc"] = res[0] if res else None
    return ids["_epmc"]


def _desde_europepmc(ids: dict, intentos: list[str]) -> dict | None:
    r = _ficha_europepmc(ids)
    if not r:
        intentos.append("Europe PMC: no ha respondido a tiempo" if ids.get("_epmc_caido") else "Europe PMC: no encontrado")
        return None
    ids["doi"] = ids.get("doi") or r.get("doi")
    ids["titulo"] = r.get("title")
    candidato = r.get("pmcid") or (r.get("id") if r.get("source") == "PPR" else None)  # PPR = preprint
    if not candidato or (r.get("inEPMC") != "Y" and r.get("isOpenAccess") != "Y"):
        intentos.append("Europe PMC: sin texto completo abierto")
        return None
    xml = _web_get(f"{EPMC}/{candidato}/fullTextXML", tipo="text")
    if xml is SIN_RESPUESTA:
        intentos.append("Europe PMC: no ha devuelto el texto (el servicio falla a ratos o la editorial no lo permite; "
                        "se puede reintentar más tarde)")
        return None
    if not xml:
        intentos.append("Europe PMC: la editorial no permite descargar el texto")
        return None
    try:
        d = pmc_xml.parsear(xml)
    except ValueError:
        intentos.append("Europe PMC: respuesta no válida")
        return None
    if d["bloqueado"]:
        intentos.append("Europe PMC: la editorial no permite descargar el texto")
        return None
    origen = "Europe PMC (preprint, sin revisión por pares)" if r.get("source") == "PPR" else "Europe PMC"
    fuente = EPMC_WEB.format("PPR" if r.get("source") == "PPR" else "PMC", candidato)
    return d | {"origen": origen, "fuente": fuente, "cita": f"Europe PMC {candidato}",
                "titulo": d["titulo"] or r.get("title"), "licencia": d["licencia"] or r.get("license")}


VERSIONES = {"publishedVersion": "versión publicada", "acceptedVersion": "manuscrito aceptado (sin maquetar)",
             "submittedVersion": "preprint (versión enviada, sin revisión por pares)"}


def _desde_unpaywall(ids: dict, intentos: list[str]) -> dict | None:
    if not UNPAYWALL_EMAIL:
        intentos.append("Unpaywall: no configurado (falta la variable UNPAYWALL_EMAIL)")
        return None
    if not ids.get("doi") and ids.get("pmid"):
        art = obtener_resumen([ids["pmid"]])["articulos"][0]
        ids["doi"], ids["titulo"] = art.get("doi"), ids.get("titulo") or art.get("titulo")
    if not ids.get("doi"):
        intentos.append("Unpaywall: el artículo no tiene DOI")
        return None
    data = _web_get(UNPAYWALL.format(ids["doi"]), {"email": UNPAYWALL_EMAIL})
    if data is SIN_RESPUESTA:
        intentos.append("Unpaywall: no ha respondido a tiempo")
        return None
    if not data or not data.get("is_oa"):
        intentos.append("Unpaywall: no hay copia legal de acceso abierto")
        return None
    orden = ["publishedVersion", "acceptedVersion", "submittedVersion"]
    lugares = sorted((l for l in data.get("oa_locations") or [] if l.get("url_for_pdf")),
                     key=lambda l: orden.index(l.get("version")) if l.get("version") in orden else 9)
    for lugar in lugares[:3]:
        url = lugar["url_for_pdf"]
        if not _url_publica(url):
            continue
        datos = _web_get(url, tipo="bytes")
        if not datos:
            continue
        try:
            d = pdf_txt.parsear(datos)
        except Exception as e:  # noqa: BLE001 - PDF dañado, escaneado o que no es PDF (p. ej. página de aviso)
            log.warning("PDF no legible en %s: %s", url, e)
            continue
        version = VERSIONES.get(lugar.get("version"), "versión no indicada")
        donde = "repositorio" if lugar.get("host_type") == "repository" else "editorial"
        return d | {"origen": f"Unpaywall: copia de acceso abierto ({donde}, {version})",
                    "fuente": lugar.get("url_for_landing_page") or url, "pdf": url, "cita": "Texto completo vía Unpaywall",
                    "titulo": data.get("title") or ids.get("titulo"), "revista": data.get("journal_name"),
                    "anio": str(data.get("year") or "") or None, "pmid": ids.get("pmid"), "pmcid": None,
                    "licencia": lugar.get("license"), "licencia_url": None,
                    "aviso": "Texto extraído de PDF: las tablas no se recuperan y los títulos de sección se detectan "
                             "de forma aproximada."}
    intentos.append("Unpaywall: hay copia abierta, pero no se ha podido descargar ni leer como PDF "
                    "(muchas editoriales bloquean las descargas automáticas; puede abrirse en el navegador)"
                    + (f" (enlace: {data.get('best_oa_location', {}).get('url')})" if data.get("best_oa_location") else ""))
    return None


def _documento(articulo: str) -> dict:
    """Busca el texto completo en PMC, luego Europe PMC y luego Unpaywall. Caché de 1 h por artículo."""
    clave = articulo.strip().lower()
    hit = _docs.get(clave)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    ids, intentos = _identificar(articulo), []
    if "doi" in ids:  # con DOI, primero se averiguan PMID y PMCID para probar PubMed Central
        r = _ficha_europepmc(ids) or {}
        if r.get("source") == "MED" and r.get("pmid"):
            ids["pmid"] = r["pmid"]
        if r.get("pmcid"):
            ids["pmcid"] = r["pmcid"]
    d = _desde_pmc(ids, intentos) or _desde_europepmc(ids, intentos) or _desde_unpaywall(ids, intentos)
    if not d:
        extra = f" Puede leerse en la web: {ids['pmc_web']}." if ids.get("pmc_web") else ""
        raise ToolError("No he encontrado texto completo legal y descargable de este artículo. Buscado en: "
                        + "; ".join(intentos) + f".{extra} Usa obtener_resumen para el abstract.")
    d["no_disponible_en"] = intentos
    _docs[clave] = (time.time(), d)
    return d


@mcp.tool()
def texto_completo(articulo: str, seccion: str = "indice", parte: int = 1) -> dict:
    """Lee el TEXTO COMPLETO de un artículo de acceso abierto (copias legales).

    Busca en este orden: PubMed Central, Europe PMC (incluye preprints) y Unpaywall
    (PDF de acceso abierto en la editorial o en un repositorio).
    Úsalo solo cuando el abstract no basta (tablas, métodos, subgrupos, dosis,
    conflictos de interés) y para pocos artículos clave: los textos son largos.
    articulo: PMID ("41177553"), PMCID ("PMC12581046") o DOI ("10.1136/bmjophth-2025-002509").
    seccion: "indice" (por defecto: origen, título, licencia y lista de secciones y tablas),
      número o nombre de sección ("2", "Methods", "resultados"), "tablas"
      o "declaraciones" (financiación y conflictos de interés).
    parte: las secciones largas se devuelven por partes; si total_partes > 1, pide parte=2, 3...
    El campo "origen" indica de dónde sale el texto y qué versión es (publicada, manuscrito aceptado
    o preprint); "cita" es cómo citarlo.
    """
    d = _documento(articulo)
    base = {k: d.get(k) for k in ("origen", "titulo", "revista", "anio", "pmid", "pmcid", "licencia",
                                  "licencia_url", "fuente", "cita", "aviso")}
    base = {k: v for k, v in base.items() if v}

    pedida = seccion.strip().lower()
    if pedida in ("indice", "índice", ""):
        return base | {
            "secciones": [{"n": i, "titulo": t, "caracteres": len(x)} for i, (t, x) in enumerate(d["secciones"], 1)],
            "tablas": [t for t, _ in d["tablas"]],
            "hay_declaraciones": bool(d["declaraciones"]),
            "siguiente_paso": "Pide una sección por número o nombre, 'tablas' o 'declaraciones'.",
        }
    if pedida == "tablas":
        if not d["tablas"]:
            raise ToolError("Este texto no tiene tablas extraíbles" + (" (viene de un PDF)." if d.get("pdf") else "."))
        nombre, texto = "tablas", "\n\n".join(x for _, x in d["tablas"])
    elif pedida == "declaraciones":
        if not d["declaraciones"]:
            raise ToolError("No encuentro declaraciones de financiación ni de conflictos de interés en este texto.")
        nombre, texto = "declaraciones", d["declaraciones"]
    else:
        elegida = pmc_xml.elegir_seccion(d["secciones"], seccion)
        if not elegida:
            disponibles = ", ".join(f"{i}. {t}" for i, (t, _) in enumerate(d["secciones"], 1))
            raise ToolError(f"No encuentro la sección «{seccion}». Disponibles: {disponibles}; o 'tablas', 'declaraciones'.")
        nombre, texto = elegida

    partes = _partir(texto)
    if not 1 <= parte <= len(partes):
        raise ToolError(f"La sección «{nombre}» tiene {len(partes)} parte(s); has pedido la parte {parte}.")
    aviso = f"\n[… continúa: pide parte={parte + 1} de {len(partes)}]" if parte < len(partes) else ""
    return base | {"seccion": nombre, "parte": parte, "total_partes": len(partes), "texto": partes[parte - 1] + aviso}


if __name__ == "__main__":
    mcp.run()
