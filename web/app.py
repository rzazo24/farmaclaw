"""Web privada de Farmaclaw: una página para hacer consultas al agente research.

Cada consulta lanza `claude -p` en el proyecto (como la evaluación), así que usa
la sesión de Claude Code de la VM y gasta de la ventana de la suscripción.

Seguridad (ver web/seguridad.py):
- Escucha solo en 127.0.0.1; a internet sale por Tailscale Funnel (HTTPS).
- Contraseña + código TOTP del móvil, bloqueo por IP tras 5 fallos, freno global, sesión que caduca.
- Claude solo puede usar el agente y las herramientas MCP (que solo leen CIMA,
  PubMed y e-lactancia): ni terminal ni archivos, aunque alguien lograra entrar.

Arranque: uv run python web/app.py   (o el servicio systemd farmaclaw-web)
"""

import asyncio
import ipaddress
import json
import logging
import secrets
import sys
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt

sys.path.insert(0, str(Path(__file__).parent))
import seguridad  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
ESTATICO = Path(__file__).parent / "estatico"
PUERTO = 8787
COOKIE = "fc_sesion"
TIMEOUT_S = 300
MAX_TEXTO = 2000
COMANDOS = {"libre", "lactancia", "embarazo", "interaccion", "dosis", "suministro"}

# Solo el agente (para delegar en research) y las herramientas MCP de Farmaclaw. `--tools Agent`
# deja fuera Bash, Read, Write, WebFetch…; `--strict-mcp-config` carga solo los servidores de
# .mcp.json (no los conectores de claude.ai); `dontAsk` rechaza cualquier otra cosa sin preguntar.
CLAUDE = [
    "claude", "-p",
    "--strict-mcp-config", "--mcp-config", str(RAIZ / ".mcp.json"),
    "--output-format", "stream-json", "--verbose",
    "--permission-mode", "dontAsk",
    "--tools", "Agent",
    "--allowedTools", "Agent", "mcp__cima__*", "mcp__pubmed__*", "mcp__elactancia__*",
    "--no-session-persistence",
]

NOMBRES = {"cima": "CIMA", "pubmed": "PubMed", "elactancia": "e-lactancia"}

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("farmaclaw-web")

_md = MarkdownIt("commonmark", {"html": False}).enable("table")  # html False: el HTML crudo se escapa


def construir_prompt(comando: str, texto: str) -> str:
    """La pregunta va tal cual. Se quita una barra inicial para que nadie pueda lanzar otros comandos."""
    texto = texto.strip().lstrip("/").strip()[:MAX_TEXTO]
    if comando == "libre":
        return f"Usa el agente research: {texto}"
    return f"/{comando} {texto}"


ETIQUETAS = {"libre": "", "lactancia": "Lactancia", "embarazo": "Embarazo", "interaccion": "Interacción",
             "dosis": "Dosis", "suministro": "Suministro"}


def titulo_de(comando: str, texto: str) -> str:
    """Título corto para la lista de recientes: «Lactancia: paracetamol»."""
    texto = " ".join(texto.strip().lstrip("/").split())
    texto = texto if len(texto) <= 70 else texto[:69] + "…"
    return f"{ETIQUETAS[comando]}: {texto}" if ETIQUETAS.get(comando) else texto


PROXY_DE_CONFIANZA = "127.0.0.1"  # Tailscale Funnel entrega las peticiones desde aquí


def ip_cliente(request: Request) -> str:
    """IP real del cliente.

    Tailscale Funnel reenvía a 127.0.0.1:8787 y pone la IP original en X-Forwarded-For
    (comprobado: además sustituye la que mande el cliente). Solo se confía en esa cabecera
    si la conexión viene de 127.0.0.1; si no, cualquiera podría inventársela. Si trae varias
    IP se usa la última: es la que añade el último proxy, el nuestro; las anteriores las
    escribe el cliente y no son de fiar."""
    conexion = request.client.host if request.client else "desconocida"
    if conexion != PROXY_DE_CONFIANZA:
        return conexion
    valores = ",".join(request.headers.getlist("x-forwarded-for"))
    ips = [v.strip() for v in valores.split(",") if v.strip()]
    if not ips:
        return conexion
    try:
        return str(ipaddress.ip_address(ips[-1]))
    except ValueError:
        return conexion


def describir_paso(nombre: str, entrada: dict) -> str | None:
    """Texto corto para el progreso: «CIMA · ficha_tecnica_seccion (4.6)»."""
    if nombre == "Agent":
        return "Consultando al agente research…"
    if not nombre.startswith("mcp__"):
        return None
    _, servidor, herramienta = nombre.split("__", 2)
    if herramienta == "texto_completo":
        detalle = f"{entrada.get('articulo', '')} · {entrada.get('seccion', 'indice')}"
        return f"Texto completo ({detalle})"
    detalle = entrada.get("seccion") or entrada.get("farmaco") or entrada.get("producto") \
        or entrada.get("nombre_o_principio_activo") or entrada.get("consulta") or ""
    detalle = str(detalle)[:60]
    return f"{NOMBRES.get(servidor, servidor)} · {herramienta}" + (f" ({detalle})" if detalle else "")


def a_html(markdown: str) -> str:
    html = _md.render(markdown)
    return html.replace("<a href=", '<a target="_blank" rel="noopener noreferrer" href=')


class Consultas:
    """Una consulta a la vez (el agente tarda ~1 min y gasta de la ventana)."""

    def __init__(self):
        self.trabajos: dict[str, dict] = {}
        self.en_curso: str | None = None

    def nueva(self, prompt: str, titulo: str = "") -> str:
        if self.en_curso:
            raise HTTPException(409, "Ya hay una consulta en marcha; espera a que termine.")
        ident = secrets.token_urlsafe(8)
        self.trabajos[ident] = {"estado": "en_curso", "pasos": [], "html": "", "inicio": time.time(), "duracion": None,
                                "titulo": titulo or prompt[:80]}
        for viejo in list(self.trabajos)[:-20]:  # guarda solo las 20 últimas
            self.trabajos.pop(viejo)
        self.en_curso = ident
        asyncio.get_running_loop().create_task(self._ejecutar(ident, prompt))
        return ident

    async def _ejecutar(self, ident: str, prompt: str):
        t = self.trabajos[ident]
        respuesta, error = "", ""
        try:
            proc = await asyncio.create_subprocess_exec(
                *CLAUDE, prompt, cwd=RAIZ, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
            try:
                async with asyncio.timeout(TIMEOUT_S):
                    async for linea in proc.stdout:
                        respuesta = self._evento(t, linea) or respuesta
                    await proc.wait()
            except TimeoutError:
                proc.kill()
                error = f"La consulta ha superado {TIMEOUT_S // 60} minutos y se ha cancelado."
            if proc.returncode and not respuesta and not error:
                error = (await proc.stderr.read()).decode(errors="replace")[-500:] or "Claude terminó con error."
        except Exception as e:  # noqa: BLE001 - cualquier fallo se muestra al usuario
            log.exception("Error en la consulta")
            error = f"Error al lanzar Claude: {e}"
        t["duracion"] = round(time.time() - t["inicio"])
        t["html"] = a_html(respuesta) if respuesta else ""
        t["error"] = error or (None if respuesta else "Claude no devolvió respuesta.")
        t["estado"] = "error" if t["error"] else "hecha"
        self.en_curso = None
        log.info("Consulta %s: %s en %s s", ident, t["estado"], t["duracion"])

    @staticmethod
    def _evento(t: dict, linea: bytes) -> str | None:
        try:
            ev = json.loads(linea)
        except json.JSONDecodeError:
            return None
        if ev.get("type") == "result":
            return ev.get("result") or "\n".join(ev.get("errors", []))
        contenido = ev.get("message", {}).get("content") if ev.get("type") == "assistant" else None
        for bloque in contenido if isinstance(contenido, list) else []:
            if bloque.get("type") == "tool_use":
                paso = describir_paso(bloque["name"], bloque.get("input", {}))
                if paso:
                    t["pasos"].append(paso)
        return None


def crear_app(guardia: seguridad.Guardia, consultas: Consultas | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    consultas = consultas or Consultas()
    app.mount("/estatico", StaticFiles(directory=ESTATICO), name="estatico")

    @app.middleware("http")
    async def cabeceras(request: Request, call_next):
        # Las peticiones que cambian algo deben llevar esta cabecera: un formulario de otra web no puede ponerla.
        if request.method == "POST" and request.headers.get("x-farmaclaw") != "1":
            return JSONResponse({"detail": "Petición no válida."}, status_code=403)
        r: Response = await call_next(request)
        r.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        r.headers["X-Content-Type-Options"] = "nosniff"
        r.headers["Referrer-Policy"] = "no-referrer"
        r.headers["Cache-Control"] = "no-store"
        return r

    def exigir_sesion(request: Request):
        if not guardia.sesion_valida(request.cookies.get(COOKIE)):
            raise HTTPException(401, "Sesión caducada. Vuelve a entrar.")

    @app.get("/")
    def pagina():
        return FileResponse(ESTATICO / "index.html")

    @app.get("/api/sesion")
    def sesion(request: Request):
        return {"activa": guardia.sesion_valida(request.cookies.get(COOKIE))}

    @app.post("/api/entrar")
    async def entrar(request: Request, response: Response):
        datos = await request.json()
        ip = ip_cliente(request)
        if espera := guardia.bloqueado(ip):
            log.warning("Intento desde %s, que está bloqueada", ip)
            raise HTTPException(429, f"Demasiados intentos. Prueba dentro de {espera // 60 + 1} min.")
        token = await asyncio.to_thread(guardia.entrar, str(datos.get("contrasena", "")),
                                        str(datos.get("codigo", "")), ip)
        if not token:
            if retraso := guardia.retraso():  # freno global: muchos fallos en total → respuesta más lenta
                await asyncio.sleep(retraso)
            raise HTTPException(401, "Contraseña o código incorrectos.")
        response.set_cookie(COOKIE, token, httponly=True, secure=True, samesite="strict",
                            max_age=seguridad.DURACION_MAX_S, path="/")
        log.info("Acceso correcto desde %s", ip)
        return {"ok": True}

    @app.post("/api/salir")
    def salir(request: Request, response: Response):
        guardia.salir(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/consulta")
    async def consulta(request: Request):
        exigir_sesion(request)
        datos = await request.json()
        comando, texto = str(datos.get("comando", "libre")), str(datos.get("texto", ""))
        if comando not in COMANDOS:
            raise HTTPException(400, "Comando desconocido.")
        if not texto.strip().lstrip("/").strip():
            raise HTTPException(400, "Escribe la consulta.")
        return {"id": consultas.nueva(construir_prompt(comando, texto), titulo_de(comando, texto))}

    @app.get("/api/consultas")
    def recientes(request: Request):
        """Consultas de esta sesión del servidor (solo en memoria; se borran al reiniciar), la más nueva primero."""
        exigir_sesion(request)
        return [{"id": i, "titulo": t.get("titulo", ""), "estado": t["estado"], "inicio": t["inicio"]}
                for i, t in reversed(consultas.trabajos.items())]

    @app.get("/api/consulta/{ident}")
    def estado(ident: str, request: Request):
        exigir_sesion(request)
        t = consultas.trabajos.get(ident)
        if not t:
            raise HTTPException(404, "Consulta no encontrada.")
        return {k: t.get(k) for k in ("estado", "pasos", "html", "duracion", "error", "titulo")} | {
            "segundos": round(time.time() - t["inicio"])}

    return app


if __name__ == "__main__":
    app = crear_app(seguridad.Guardia(seguridad.cargar_config()))
    uvicorn.run(app, host="127.0.0.1", port=PUERTO, log_level="warning")
