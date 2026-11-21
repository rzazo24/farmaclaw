"""Servidor MCP de e-lactancia.org (APILAM).

e-lactancia no tiene API pública: se usa su buscador interno (/megasearch/, JSON) y se lee
la ficha HTML del producto. Contenido bajo licencia CC BY-NC-SA 4.0: uso no comercial,
siempre con atribución a e-lactancia.org.

Acceso respetuoso: una consulta solo cuando se pide (nada de descargas masivas),
al menos 1 s entre peticiones y caché de 24 h.
Transporte stdio: stdout es el canal MCP, así que los logs van a stderr.
"""

import html
import logging
import re
import sys
import threading
import time

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s elactancia %(levelname)s %(message)s")
log = logging.getLogger("elactancia")

BASE = "https://www.e-lactancia.org"
TIMEOUT = 15.0
CACHE_TTL = 24 * 3600
INTERVALO = 1.0
MAX_COMENTARIO = 6000
USER_AGENT = "farmaclaw/0.1 (+https://github.com/rzazo24/farmaclaw)"
LICENCIA = "e-lactancia.org (APILAM), licencia CC BY-NC-SA 4.0"

NIVELES = {
    0: "Riesgo muy bajo. Compatible (seguro, mejor opción)",
    1: "Riesgo bajo. Compatibilidad probable (bastante seguro)",
    2: "Riesgo alto. Compatibilidad limitada (poco seguro)",
    3: "Riesgo muy alto. Incompatible (muy inseguro)",
}

mcp = FastMCP("elactancia")
_cache: dict[str, tuple[float, object]] = {}
_client = httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT})
_lock = threading.Lock()
_ultima = 0.0


def _get(ruta: str, params: dict | None = None, json_: bool = False) -> tuple[object, str]:
    """GET con caché de 24 h, 1 petición/s, timeout de 15 s y un reintento. Devuelve (datos, url final)."""
    global _ultima
    key = ruta + repr(sorted((params or {}).items()))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    for intento in (1, 2):
        with _lock:
            espera = _ultima + INTERVALO - time.monotonic()
            if espera > 0:
                time.sleep(espera)
            _ultima = time.monotonic()
        try:
            r = _client.get(BASE + ruta, params=params)
            if r.status_code == 404:
                return None, str(r.url)
            r.raise_for_status()
            res = (r.json() if json_ else r.text, str(r.url))
            _cache[key] = (time.time(), res)
            return res
        except httpx.TimeoutException:
            log.warning("timeout en %s (intento %d)", ruta, intento)
            if intento == 2:
                raise ToolError("e-lactancia no ha respondido en 15 s (timeout). Inténtalo de nuevo más tarde.")
        except (httpx.HTTPError, ValueError) as e:
            log.warning("error en %s (intento %d): %s", ruta, intento, e)
            if intento == 2:
                raise ToolError(f"e-lactancia no está disponible o ha devuelto un error: {e}")
        time.sleep(1)


def _texto(fragmento: str) -> str:
    fragmento = re.sub(r"<sup>(.*?)</sup>", r" \1", fragmento, flags=re.S)  # referencias "(Walter 1997)"
    fragmento = re.sub(r"</p>|<br\s*/?>|</li>", "\n", fragmento)
    texto = html.unescape(re.sub(r"<[^>]+>", "", fragmento))
    texto = re.sub(r"[ \t\xa0]+", " ", texto)
    return re.sub(r"\s*\n\s*", "\n", texto).strip()


def _caja(pagina: str, clase: str) -> str:
    """Contenido del <div> cuya clase empieza por `clase` (hasta el cierre de su columna)."""
    m = re.search(rf'<div class="box grey-box squared\s+{clase}[^"]*">(.*?)</div>\s*</div>', pagina, re.S)
    return m.group(1) if m else ""


def _parsear(pagina: str) -> dict:
    nivel = re.search(r"risk-level(\d) ", pagina)
    actualizado = re.search(r'class="small last-update">\s*Última actualización:\s*([^<]+)<', pagina)
    titulo = re.search(r'<h1 class="term-header[^"]*">\s*([^<]+)</h1>', pagina)

    alternativas = []
    for href, nombre, nota in re.findall(
        r'<li\s*><a class="link_r\d" href="([^"]+)"\s*>([^<]+)</a>\s*\(([^)]*)\)', _caja(pagina, "risk-alt")
    ):
        alternativas.append({"nombre": html.unescape(nombre.strip()), "nota": nota.strip(), "fuente": BASE + href})

    comentario = _texto(_caja(pagina, r"risk-comment-level\d"))
    if len(comentario) > MAX_COMENTARIO:
        comentario = comentario[:MAX_COMENTARIO] + "\n[… texto recortado; ver la ficha completa en la fuente]"

    n = int(nivel.group(1)) if nivel else None
    return {
        "producto": html.unescape(titulo.group(1).strip()) if titulo else None,
        "nivel_riesgo": n,
        "riesgo": NIVELES.get(n, "No indicado"),
        "comentario": comentario,
        "alternativas": alternativas or "e-lactancia no propone alternativas (en riesgo muy bajo no las muestra).",
        "ultima_actualizacion": actualizado.group(1).strip() if actualizado else None,
    }


@mcp.tool()
def elactancia(producto: str) -> dict:
    """Consulta e-lactancia.org (APILAM): compatibilidad de un fármaco o producto con la lactancia.

    producto: nombre del principio activo en español (p. ej. "ibuprofeno", "sertralina",
      "amiodarona"). Para marcas comerciales, busca antes el principio activo en CIMA.
    Devuelve nivel de riesgo (0 compatible → 3 incompatible), comentario con referencias,
    alternativas más seguras si las hay, fecha de actualización y enlace a la ficha.
    """
    resultados, _ = _get("/megasearch/", {"query": producto.strip()}, json_=True)
    resultados = resultados or []
    productos = [r for r in resultados if r.get("term") == "producto"]
    if not productos:
        otros = [f"{r.get('nombre')} ({r.get('term')})" for r in resultados[:10]]
        if otros:
            raise ToolError(f"«{producto}» no es un producto en e-lactancia; coincidencias: {', '.join(otros)}. "
                            "Si es una marca, busca su principio activo en CIMA y consulta ese.")
        raise ToolError(f"No he encontrado «{producto}» en e-lactancia.")

    q = producto.strip().lower()
    exacto = [p for p in productos if p.get("nombre_es", "").lower() == q]
    if not exacto and len(productos) > 1:
        return {"aviso": "Varios productos coinciden; repite la consulta con el nombre exacto.",
                "candidatos": [p.get("nombre_es") for p in productos]}
    elegido = (exacto or productos)[0]

    pagina, url = _get(f"/producto/{elegido['id']}/")
    if not pagina:
        raise ToolError(f"No se ha podido abrir la ficha de {elegido.get('nombre_es')} en e-lactancia.")
    return {**_parsear(pagina), "fuente": url, "licencia": LICENCIA}


if __name__ == "__main__":
    mcp.run()
