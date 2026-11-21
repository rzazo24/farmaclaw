"""Servidor MCP de CIMA (AEMPS).

Consulta la API REST pública de CIMA: https://cima.aemps.es/cima/rest/
Transporte stdio: stdout es el canal MCP, así que los logs van a stderr.
"""

import html
import logging
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from html.parser import HTMLParser

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s cima %(levelname)s %(message)s")
log = logging.getLogger("cima")

BASE = "https://cima.aemps.es/cima/rest"
WEB = "https://cima.aemps.es/cima/publico/detalle.html?nregistro={}"
TIMEOUT = 15.0
CACHE_TTL = 3600  # 1 h: fichas y estados cambian poco
MAX_CHARS = 8000  # límite de texto por sección para mantener respuestas compactas
MAX_RESULTADOS = 15
MADRID = ZoneInfo("Europe/Madrid")

TIPO_DOC = {"ficha": 1, "prospecto": 2}

# Alias en lenguaje natural para no tener que recordar los números de sección.
ALIAS_FICHA = {
    "indicaciones": "4.1", "posologia": "4.2", "posología": "4.2",
    "contraindicaciones": "4.3", "advertencias": "4.4", "interacciones": "4.5",
    "embarazo": "4.6", "lactancia": "4.6", "conduccion": "4.7", "conducción": "4.7",
    "reacciones adversas": "4.8", "efectos adversos": "4.8", "sobredosis": "4.9",
    "farmacodinamia": "5.1", "farmacocinetica": "5.2", "farmacocinética": "5.2",
}
ALIAS_PROSPECTO = {
    "que es": "1", "qué es": "1", "antes de tomar": "2", "advertencias": "2",
    "interacciones": "2", "embarazo": "2", "como tomar": "3", "cómo tomar": "3",
    "posologia": "3", "posología": "3", "efectos adversos": "4", "conservacion": "5",
    "conservación": "5", "composicion": "6", "composición": "6",
}

mcp = FastMCP("cima")
_cache: dict[str, tuple[float, object]] = {}
_client = httpx.Client(timeout=TIMEOUT, headers={"Accept": "application/json"})


def _get(path: str, params: dict | None = None) -> object:
    """GET a la API con caché TTL, timeout de 15 s y un reintento."""
    key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    for intento in (1, 2):
        try:
            r = _client.get(BASE + path, params=params)
            if r.status_code == 204 or not r.content:
                data = None
            elif r.status_code == 404:
                data = None
            elif r.status_code >= 500:
                raise httpx.HTTPStatusError("5xx", request=r.request, response=r)
            else:
                r.raise_for_status()
                data = r.json()
            _cache[key] = (time.time(), data)
            return data
        except httpx.TimeoutException:
            log.warning("timeout en %s (intento %d)", path, intento)
            if intento == 2:
                raise ToolError("La API de CIMA no ha respondido en 15 s (timeout). Inténtalo de nuevo más tarde.")
        except (httpx.HTTPError, ValueError) as e:
            log.warning("error en %s (intento %d): %s", path, intento, e)
            if intento == 2:
                raise ToolError(f"La API de CIMA no está disponible o ha devuelto un error: {e}")
        time.sleep(1)


class _Texto(HTMLParser):
    """Convierte el HTML de CIMA en texto plano conservando párrafos y viñetas."""

    BLOQUES = {"p", "div", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.partes.append("\n• ")
        elif tag in self.BLOQUES:
            self.partes.append("\n")
        elif tag == "td":
            self.partes.append(" | ")

    def handle_data(self, data):
        self.partes.append(data)


def _limpiar_html(contenido: str) -> str:
    p = _Texto()
    p.feed(contenido or "")
    texto = html.unescape("".join(p.partes))
    texto = re.sub(r"[ \t\r\xa0]+", " ", texto)
    texto = re.sub(r" *\n *", "\n", texto)
    texto = re.sub(r"^•\s*$", "", texto, flags=re.M)  # viñetas vacías de listas anidadas
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def _fecha(ms: int | None) -> str | None:
    if not ms:
        return None
    # CIMA da las fechas como medianoche de Madrid; en UTC caerían el día anterior.
    return datetime.fromtimestamp(ms / 1000, tz=MADRID).strftime("%Y-%m-%d")


def _medicamento(nregistro: str) -> dict:
    data = _get("/medicamento", {"nregistro": nregistro.strip()})
    if not data:
        raise ToolError(f"No se ha encontrado ningún medicamento con nº de registro {nregistro} en CIMA.")
    return data


def _buscar(texto: str) -> list[dict]:
    """Busca por código nacional o nº de registro (si son dígitos), o por nombre comercial y principio activo."""
    texto = texto.strip()
    codigo = texto.split(".")[0]  # el CN de la caja puede llevar dígito de control: "712729.8"
    if codigo.isdigit():
        # Un CN tiene 6-7 dígitos; si no casa como CN, se prueba como nº de registro.
        params = [{"cn": codigo}, {"nregistro": codigo}] if len(codigo) in (6, 7) else [{"nregistro": codigo}]
        for p in params:
            med = _get("/medicamento", p)
            if med:
                return [med]
        return []
    # Siempre ambas búsquedas: por nombre solo salen los genéricos que llevan el principio
    # activo en el nombre ("ácido valproico") y se perderían las marcas (Depakine).
    vistos: dict[str, dict] = {}
    for campo in ("nombre", "practiv1"):
        data = _get("/medicamentos", {campo: texto}) or {}
        for m in data.get("resultados", []):
            vistos.setdefault(m["nregistro"], m)
    return list(vistos.values())


def _resumen(m: dict) -> dict:
    return {
        "nregistro": m["nregistro"],
        "nombre": m["nombre"],
        "laboratorio": m.get("labtitular"),
        "comercializado": m.get("comerc"),
        "problemas_suministro": m.get("psum"),
        "notas_seguridad": m.get("notas"),
        "materiales_informativos": m.get("materialesInf"),
        "receta": m.get("cpresc"),
        "fuente": WEB.format(m["nregistro"]),
    }


@mcp.tool()
def buscar_medicamento(nombre_o_principio_activo: str, solo_comercializados: bool = False) -> dict:
    """Busca medicamentos autorizados en España por nombre comercial, principio activo,
    código nacional (CN de la caja, p. ej. "712729") o nº de registro.

    Devuelve una lista breve (máx. 15) con nregistro, nombre, laboratorio,
    si está comercializado y si tiene problemas de suministro. Usa el
    nregistro para consultar la ficha técnica o el prospecto.
    """
    resultados = _buscar(nombre_o_principio_activo)
    if solo_comercializados:
        resultados = [m for m in resultados if m.get("comerc")]
    if not resultados:
        raise ToolError(f"No se ha encontrado ningún medicamento para «{nombre_o_principio_activo}» en CIMA.")
    # Primero los que empiezan por la consulta (CIMA busca por subcadena: "omeprazol"
    # también trae "esomeprazol") y, dentro de eso, los comercializados.
    q = nombre_o_principio_activo.strip().lower()
    resultados.sort(key=lambda m: (not m["nombre"].lower().startswith(q), not m.get("comerc"), m["nombre"]))
    return {
        "total": len(resultados),
        "mostrados": min(len(resultados), MAX_RESULTADOS),
        "medicamentos": [_resumen(m) for m in resultados[:MAX_RESULTADOS]],
        "fuente": f"https://cima.aemps.es/cima/publico/lista.html",
    }


def _partir(texto: str) -> list[str]:
    """Divide el texto en partes de hasta MAX_CHARS, cortando por párrafos cuando se puede."""
    partes = []
    while len(texto) > MAX_CHARS:
        corte = texto.rfind("\n\n", 0, MAX_CHARS)
        if corte < MAX_CHARS // 2:  # sin párrafo razonable: cortar por línea o a pelo
            corte = texto.rfind("\n", 0, MAX_CHARS)
            if corte < MAX_CHARS // 2:
                corte = MAX_CHARS
        partes.append(texto[:corte].strip())
        texto = texto[corte:].strip()
    partes.append(texto)
    return partes


def _seccion(nregistro: str, seccion: str, tipo: str, parte: int = 1) -> dict:
    alias = ALIAS_FICHA if tipo == "ficha" else ALIAS_PROSPECTO
    seccion = alias.get(seccion.strip().lower(), seccion.strip())
    tipo_doc = TIPO_DOC[tipo]
    med = _medicamento(nregistro)
    doc = next((d for d in med.get("docs", []) if d.get("tipo") == tipo_doc), None)
    nombre_doc = "ficha técnica" if tipo == "ficha" else "prospecto"
    if not doc:
        raise ToolError(f"{med['nombre']} no tiene {nombre_doc} publicado en CIMA.")
    fuente = doc.get("urlHtml") or doc.get("url")
    if not doc.get("secc"):
        raise ToolError(f"La {nombre_doc} de {med['nombre']} no está segmentada por secciones; consúltala en {fuente}")

    data = _get(f"/docSegmentado/contenido/{tipo_doc}", {"nregistro": nregistro, "seccion": seccion})
    if not isinstance(data, list) or not data:  # CIMA devuelve {"error": ...} con 200 si no existe
        indice = _get(f"/docSegmentado/secciones/{tipo_doc}", {"nregistro": nregistro}) or []
        disponibles = ", ".join(f"{s['seccion']} {s['titulo']}" for s in indice)
        raise ToolError(f"No existe la sección «{seccion}» en {'la' if tipo == 'ficha' else 'el'} {nombre_doc} de {med['nombre']}. Disponibles: {disponibles}")

    texto = "\n\n".join(_limpiar_html(s.get("contenido", "")) for s in data)
    partes = _partir(texto)
    if not 1 <= parte <= len(partes):
        raise ToolError(f"La sección {seccion} tiene {len(partes)} parte(s); has pedido la parte {parte}.")
    aviso = ""
    if parte < len(partes):
        aviso = f"\n[… continúa: pide parte={parte + 1} de {len(partes)} para leer el resto]"
    return {
        "medicamento": med["nombre"],
        "nregistro": med["nregistro"],
        "documento": nombre_doc,
        "seccion": data[0].get("seccion", seccion),
        "titulo": data[0].get("titulo"),
        "parte": parte,
        "total_partes": len(partes),
        "texto": partes[parte - 1] + aviso,
        "fecha_documento": _fecha(doc.get("fecha")),
        "fuente": fuente,
    }


@mcp.tool()
def ficha_tecnica_seccion(nregistro: str, seccion: str, parte: int = 1) -> dict:
    """Devuelve el texto de una sección de la ficha técnica de un medicamento.

    seccion: número ("4.2", "4.3", "4.5", "4.6", "4.8"...) o alias
    ("posologia", "contraindicaciones", "interacciones", "embarazo",
    "reacciones adversas"...).
    parte: las secciones largas se devuelven por partes; si total_partes > 1,
    pide parte=2, 3... para leer el resto.
    """
    return _seccion(nregistro, seccion, "ficha", parte)


@mcp.tool()
def prospecto_seccion(nregistro: str, seccion: str, parte: int = 1) -> dict:
    """Devuelve el texto de una sección del prospecto de un medicamento.

    Secciones del prospecto: 1 qué es, 2 antes de tomar (advertencias,
    interacciones, embarazo), 3 cómo tomar, 4 efectos adversos,
    5 conservación, 6 composición. También acepta esos alias.
    parte: si total_partes > 1, pide parte=2, 3... para leer el resto.
    """
    return _seccion(nregistro, seccion, "prospecto", parte)


def _problemas_de(med: dict) -> list[dict]:
    """Problemas de suministro de cada presentación (CN) de un medicamento."""
    problemas = []
    for p in med.get("presentaciones", []):
        if not p.get("psum"):
            continue
        data = _get(f"/psuministro/{p['cn']}") or {}
        for ps in data.get("resultados", []):
            problemas.append({
                "cn": ps["cn"],
                "presentacion": ps.get("nombre"),
                "activo": ps.get("activo"),
                "fecha_inicio": _fecha(ps.get("fini")),
                "fecha_fin_prevista": _fecha(ps.get("ffin")),
                "observaciones": ps.get("observ"),
            })
    return problemas


@mcp.tool()
def problemas_suministro(nregistro_o_nombre: str) -> dict:
    """Consulta problemas de suministro por nº de registro, código nacional (CN) o nombre/principio activo.

    Devuelve, por presentación (código nacional), si el problema está
    activo, fechas de inicio y fin prevista y observaciones de la AEMPS.
    """
    consulta = nregistro_o_nombre.strip()
    if consulta.split(".")[0].isdigit():  # CN o nº de registro
        encontrados = _buscar(consulta)
        if not encontrados:
            raise ToolError(f"No se ha encontrado ningún medicamento con el código {consulta} en CIMA.")
        candidatos = encontrados
    else:
        encontrados = _buscar(consulta)
        if not encontrados:
            raise ToolError(f"No se ha encontrado ningún medicamento para «{consulta}» en CIMA.")
        # La búsqueda no trae presentaciones: pedimos el detalle solo de los marcados con psum.
        con_psum = [m for m in encontrados if m.get("psum")][:MAX_RESULTADOS]
        candidatos = [_medicamento(m["nregistro"]) for m in con_psum]

    salida = []
    for med in candidatos:
        problemas = _problemas_de(med)
        if problemas:
            salida.append({"medicamento": med["nombre"], "nregistro": med["nregistro"],
                           "problemas": problemas, "fuente": WEB.format(med["nregistro"])})
    return {
        "consulta": consulta,
        "con_problemas": len(salida),
        "resultado": salida or "Sin problemas de suministro registrados en CIMA.",
        "fuente": "https://cima.aemps.es/cima/publico/listadesabastecimiento.html",
    }


@mcp.tool()
def notas_seguridad(nregistro: str) -> dict:
    """Notas informativas de seguridad de la AEMPS sobre un medicamento (más recientes primero).

    Devuelve fecha, referencia, asunto y enlace a cada nota. Útil para alertas
    de farmacovigilancia (nuevos riesgos, restricciones de uso).
    """
    med = _medicamento(nregistro)
    notas = _get(f"/notas/{med['nregistro']}") or []
    notas = sorted(notas, key=lambda n: n.get("fecha") or 0, reverse=True)
    return {
        "medicamento": med["nombre"],
        "nregistro": med["nregistro"],
        "total": len(notas),
        "notas": [{
            "fecha": _fecha(n.get("fecha")),
            "referencia": n.get("referencia"),
            "asunto": n.get("asunto"),
            "fuente": n.get("url"),
        } for n in notas[:MAX_RESULTADOS]] or "Sin notas de seguridad de la AEMPS para este medicamento.",
        "fuente": WEB.format(med["nregistro"]),
    }


@mcp.tool()
def materiales_informativos(nregistro: str) -> dict:
    """Materiales informativos de seguridad aprobados por la AEMPS (guías para
    profesionales, tarjetas o guías para pacientes, vídeos) con sus enlaces.
    """
    med = _medicamento(nregistro)
    data = _get(f"/materiales/{med['nregistro']}") or {}
    materiales = []
    # La respuesta agrupa por destinatario: listaDocsProfesional, listaDocsPaciente...
    for clave, docs in data.items():
        if not isinstance(docs, list):
            continue
        destinatario = clave.removeprefix("listaDocs").removeprefix("lista").lower() or clave
        for d in docs:
            materiales.append({
                "destinatario": destinatario,
                "nombre": d.get("nombre"),
                "fecha": _fecha(d.get("fecha")),
                "fuente": d.get("url"),
            })
    return {
        "medicamento": med["nombre"],
        "nregistro": med["nregistro"],
        "materiales": materiales or "Sin materiales informativos de seguridad para este medicamento.",
        "fuente": WEB.format(med["nregistro"]),
    }


if __name__ == "__main__":
    mcp.run()
