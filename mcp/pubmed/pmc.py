"""Texto completo de artículos de PubMed Central (PMC) en formato JATS XML.

Solo procesado (sin red): el servidor descarga el XML con efetch (db=pmc) y aquí
se convierte en secciones de texto, tablas y declaraciones (financiación y
conflictos de interés). Las referencias bibliográficas se omiten.
"""

import re
import xml.etree.ElementTree as ET

# La editorial no permite el XML completo: PMC deja solo los metadatos y este comentario.
BLOQUEADO = "does not allow downloading of the full text"

# Bloques que no se pasan al texto (se tratan aparte o no aportan).
OMITIR = {"ref-list", "graphic", "media", "object-id", "alternatives", "disp-formula", "supplementary-material"}

# Títulos de sección que corresponden a declaraciones del artículo.
DECLARACION = re.compile(r"conflict|competing|interest|disclos|funding|financ|sponsor|role of the funder", re.I)
TIPOS_FN = {"coi-statement", "conflict", "financial-disclosure", "funding", "supported-by"}
ETIQUETAS_FN = {"coi-statement": "Conflictos de interés", "conflict": "Conflictos de interés",
                "financial-disclosure": "Declaración financiera", "funding": "Financiación", "supported-by": "Financiación"}

ALIAS = {  # nombres de sección en español → inglés (los artículos suelen estar en inglés)
    "introduccion": "introduction", "metodos": "method", "metodo": "method", "material y metodos": "method",
    "resultados": "result", "discusion": "discussion", "conclusiones": "conclusion", "conclusion": "conclusion",
}


def _limpiar(texto: str) -> str:
    texto = texto.replace(" ", " ").replace("\xa0", " ")
    lineas = [re.sub(r"[ \t]+", " ", l).strip() for l in texto.split("\n")]
    texto = "\n".join(lineas)
    texto = re.sub(r"(;\s*){2,}", "; ", texto)
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def _inline(el: ET.Element, sep: str = " ") -> str:
    """Texto de un elemento. Los párrafos, saltos y elementos de lista internos se separan con `sep`."""
    trozos = [el.text or ""]
    for c in el:
        if c.tag in OMITIR or c.tag in ("table-wrap", "fig"):
            pass
        elif c.tag == "break":
            trozos.append(sep)
        elif c.tag in ("p", "list-item", "list", "title"):
            trozos.append(sep + _inline(c, sep) + sep)
        else:
            trozos.append(_inline(c, sep))
        trozos.append(c.tail or "")
    return "".join(trozos)


def _etiqueta(el: ET.Element) -> str:
    label = _inline(el.find("label")).strip() if el.find("label") is not None else ""
    caption = _inline(el.find("caption"), " ").strip() if el.find("caption") is not None else ""
    return _limpiar(f"{label}: {caption}" if label and caption else label or caption)


def _bloque(el: ET.Element, nivel: int = 0) -> str:
    """Convierte un bloque JATS en texto con títulos «## …», párrafos y viñetas."""
    if el.tag in OMITIR:
        return ""
    if el.tag == "table-wrap":
        return f"[{_etiqueta(el) or 'Tabla'} · ver seccion='tablas']\n\n"
    if el.tag == "fig":
        return f"[{_etiqueta(el) or 'Figura'}]\n\n"
    if el.tag == "sec":
        titulo = _inline(el.find("title")).strip() if el.find("title") is not None else ""
        cuerpo = "".join(_bloque(c, nivel + 1) for c in el if c.tag != "title")
        return (f"{'#' * min(nivel + 2, 4)} {titulo}\n\n" if titulo else "") + cuerpo
    if el.tag == "list":
        return "".join(f"- {_limpiar(_inline(li))}\n" for li in el.findall("list-item")) + "\n"
    if el.tag == "p":
        # Un párrafo puede contener listas o tablas: se sacan como bloques aparte.
        texto, extra = _inline(el), ""
        for c in el:
            if c.tag in ("list", "table-wrap", "fig"):
                extra += _bloque(c, nivel)
        return _limpiar(texto) + "\n\n" + extra
    hijos = list(el)
    return "".join(_bloque(c, nivel) for c in hijos) if hijos else (_limpiar(_inline(el)) + "\n\n" if (el.text or "").strip() else "")


def _tabla(tw: ET.Element) -> str:
    filas = []
    for tr in tw.iter("tr"):
        celdas = [_limpiar(_inline(c, "; ")).strip("; ") for c in tr if c.tag in ("td", "th")]
        filas.append(" | ".join(celdas))
    pie = tw.find("table-wrap-foot")
    texto = f"### {_etiqueta(tw) or 'Tabla'}\n" + "\n".join(filas)
    if pie is not None:
        texto += "\n" + _limpiar(_inline(pie, "\n"))
    return texto


def parsear(xml: str) -> dict:
    """Devuelve metadatos, secciones del cuerpo, tablas y declaraciones de un artículo de PMC."""
    bloqueado = BLOQUEADO in xml
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ValueError(f"XML de PMC no válido: {e}")
    art = raiz if raiz.tag == "article" else raiz.find(".//article")
    if art is None:
        raise ValueError("La respuesta de PMC no contiene ningún artículo.")

    meta = art.find("front/article-meta")
    ids = {i.get("pub-id-type"): (i.text or "").strip() for i in meta.findall("article-id")} if meta is not None else {}
    pmcid = ids.get("pmcid") or (f"PMC{ids['pmc']}" if ids.get("pmc") else None) or (f"PMC{ids['pmcaid']}" if ids.get("pmcaid") else None)
    titulo = _limpiar(_inline(meta.find("title-group/article-title"))) if meta is not None and meta.find("title-group/article-title") is not None else ""
    revista = _limpiar(_inline(art.find("front/journal-meta/journal-title-group/journal-title"))) \
        if art.find("front/journal-meta/journal-title-group/journal-title") is not None else ""
    anio = next((y.text for y in meta.iter("year")), None) if meta is not None else None

    lic = meta.find("permissions/license") if meta is not None else None
    licencia, licencia_url = None, None
    if lic is not None:
        licencia_url = lic.get("{http://www.w3.org/1999/xlink}href") or next(
            (e.text for e in lic.iter() if e.tag.endswith("license_ref")), None)
        parrafos = [_limpiar(_inline(lp)) for lp in lic.iter() if lp.tag.endswith("license-p")]
        licencia = (" ".join(parrafos) or _limpiar(_inline(lic)))[:300] or None

    secciones: list[tuple[str, str]] = []
    cuerpo = art.find("body")
    if cuerpo is not None:
        sueltos = "".join(_bloque(c) for c in cuerpo if c.tag != "sec")
        if sueltos.strip():
            secciones.append(("Texto fuera de secciones", _limpiar(sueltos)))
        for s in cuerpo.findall("sec"):
            t = _inline(s.find("title")).strip() if s.find("title") is not None else ""
            secciones.append((t or f"Sección {len(secciones) + 1}", _limpiar(_bloque(s))))

    tablas = [(_etiqueta(tw) or f"Tabla {i}", _tabla(tw)) for i, tw in enumerate(art.iter("table-wrap"), 1)]

    # Declaraciones: financiación, conflictos de interés, agradecimientos.
    decl = []
    if meta is not None:
        for fg in meta.iter("funding-group"):
            decl.append("Financiación: " + _limpiar(_inline(fg)))
        for fn in meta.findall("author-notes/fn"):
            texto = _limpiar(_inline(fn))
            tipo = (fn.get("fn-type") or "").lower()
            if tipo in TIPOS_FN or DECLARACION.search(texto[:80]):
                decl.append(f"{ETIQUETAS_FN[tipo]}: {texto}" if tipo in ETIQUETAS_FN else texto)
    fondo = art.find("back")
    for zona in (cuerpo, fondo):
        if zona is None:
            continue
        for s in zona.iter("sec"):
            t = _inline(s.find("title")).strip() if s.find("title") is not None else ""
            if t and DECLARACION.search(t):
                decl.append(_limpiar(_bloque(s)))
    if fondo is not None:
        for fg in fondo.findall("fn-group"):
            decl.append(_limpiar(_inline(fg, "\n")))
        for ack in fondo.findall("ack"):
            decl.append("Agradecimientos: " + _limpiar(_inline(ack)))
    declaraciones = "\n\n".join(d for d in dict.fromkeys(decl) if d)

    return {
        "titulo": titulo, "revista": revista or None, "anio": anio, "pmid": ids.get("pmid"), "pmcid": pmcid,
        "licencia": licencia, "licencia_url": licencia_url, "bloqueado": bloqueado or cuerpo is None,
        "secciones": secciones, "tablas": tablas, "declaraciones": declaraciones,
    }


def elegir_seccion(secciones: list[tuple[str, str]], pedida: str) -> tuple[str, str] | None:
    """Sección por número (1, 2…) o por nombre, en inglés o en español (Methods, métodos…)."""
    p = pedida.strip().lower()
    if p.isdigit():
        n = int(p)
        return secciones[n - 1] if 1 <= n <= len(secciones) else None
    sin_tildes = p.translate(str.maketrans("áéíóú", "aeiou"))
    clave = ALIAS.get(sin_tildes, sin_tildes)
    for titulo, texto in secciones:
        if titulo.lower().startswith(clave) or clave in titulo.lower():
            return titulo, texto
    return None
