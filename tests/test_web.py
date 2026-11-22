"""Pruebas de la web: acceso (contraseña + TOTP), bloqueo, sesiones y construcción de la consulta. Sin red."""

import importlib.util
import sys
from pathlib import Path

import pyotp
import pytest
from fastapi.testclient import TestClient

WEB = Path(__file__).resolve().parent.parent / "web"
sys.path.insert(0, str(WEB))
import seguridad  # noqa: E402

_spec = importlib.util.spec_from_file_location("web_app", WEB / "app.py")
web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(web)

CONTRASENA = "una-contrasena-bien-larga"
SECRETO = pyotp.random_base32()


class Reloj:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture(scope="module")
def config():
    return {"contrasena": seguridad.hash_contrasena(CONTRASENA), "totp": SECRETO}


@pytest.fixture
def reloj():
    return Reloj()


@pytest.fixture
def guardia(config, reloj):
    return seguridad.Guardia(config, reloj=reloj)


def codigo(reloj, desfase=0):
    return pyotp.TOTP(SECRETO).at(reloj() + desfase)


def test_entra_con_contrasena_y_codigo(guardia, reloj):
    assert guardia.entrar(CONTRASENA, codigo(reloj))


def test_rechaza_contrasena_o_codigo_incorrectos(guardia, reloj):
    assert guardia.entrar("otra-contrasena-larga", codigo(reloj)) is None
    assert guardia.entrar(CONTRASENA, "000000" if codigo(reloj) != "000000" else "111111") is None
    assert guardia.entrar(CONTRASENA, "abc") is None


def test_un_codigo_no_vale_dos_veces(guardia, reloj):
    c = codigo(reloj)
    assert guardia.entrar(CONTRASENA, c)
    assert guardia.entrar(CONTRASENA, c) is None


def test_admite_el_codigo_anterior_por_desfase_de_reloj(guardia, reloj):
    assert guardia.entrar(CONTRASENA, codigo(reloj, -30))


def test_bloquea_una_ip_tras_cinco_fallos(guardia, reloj):
    for _ in range(seguridad.MAX_FALLOS):
        guardia.entrar("mala", "123456", "1.1.1.1")
    assert guardia.bloqueado("1.1.1.1") > 0
    assert guardia.entrar(CONTRASENA, codigo(reloj), "1.1.1.1") is None   # ni con las buenas
    reloj.t += seguridad.BLOQUEO_S + 60
    assert guardia.bloqueado("1.1.1.1") == 0
    assert guardia.entrar(CONTRASENA, codigo(reloj), "1.1.1.1")


def test_una_ip_bloqueada_no_bloquea_a_otra(guardia, reloj):
    for _ in range(seguridad.MAX_FALLOS):
        guardia.entrar("mala", "123456", "203.0.113.7")                   # el bot
    assert guardia.bloqueado("203.0.113.7") > 0
    assert guardia.bloqueado("198.51.100.20") == 0
    assert guardia.entrar(CONTRASENA, codigo(reloj), "198.51.100.20")     # yo, desde otra IP


def test_un_acierto_pone_a_cero_los_fallos_de_esa_ip(guardia, reloj):
    for _ in range(seguridad.MAX_FALLOS - 1):
        guardia.entrar("mala", "123456", "1.1.1.1")
    reloj.t += 31
    assert guardia.entrar(CONTRASENA, codigo(reloj), "1.1.1.1")
    guardia.entrar("mala", "123456", "1.1.1.1")
    assert guardia.bloqueado("1.1.1.1") == 0


def test_freno_global_retrasa_pero_no_bloquea(guardia, reloj):
    assert guardia.retraso() == 0
    for i in range(seguridad.UMBRAL_GLOBAL + 20):                          # ataque desde muchas IP
        guardia._anotar_fallo(f"10.0.{i // 250}.{i % 250}")
    assert guardia.retraso() == pytest.approx(20 * seguridad.RETRASO_POR_FALLO_S)
    for i in range(500):
        guardia._anotar_fallo(f"10.9.{i // 250}.{i % 250}")
    assert guardia.retraso() == seguridad.RETRASO_MAX_S                    # creciente, pero con tope
    assert guardia.bloqueado("198.51.100.20") == 0
    assert guardia.entrar(CONTRASENA, codigo(reloj), "198.51.100.20")     # con las buenas se entra
    reloj.t += seguridad.VENTANA_GLOBAL_S + 1
    assert guardia.retraso() == 0                                         # pasada la ventana, sin freno


def test_limpia_las_ip_caducadas(guardia, reloj):
    for _ in range(seguridad.MAX_FALLOS):
        guardia.entrar("mala", "123456", "1.1.1.1")                       # bloqueada
    guardia._anotar_fallo("2.2.2.2")                                      # un fallo suelto
    assert set(guardia.por_ip) == {"1.1.1.1", "2.2.2.2"}
    reloj.t += seguridad.BLOQUEO_S + 1
    guardia.limpiar(forzar=True)
    assert guardia.por_ip == {}


def test_el_registro_de_ip_tiene_tope(guardia, reloj, monkeypatch):
    monkeypatch.setattr(seguridad, "MAX_IPS", 100)
    for i in range(250):
        reloj.t += 0.01
        guardia._anotar_fallo(f"10.1.{i // 250}.{i % 250}")
    assert len(guardia.por_ip) == 100
    assert "10.1.0.249" in guardia.por_ip and "10.1.0.0" not in guardia.por_ip   # se van las más antiguas


def test_los_logs_registran_la_ip_pero_no_las_credenciales(guardia, reloj, caplog):
    with caplog.at_level("WARNING", logger="farmaclaw-web"):
        for _ in range(seguridad.MAX_FALLOS):
            guardia.entrar("contrasena-secreta-xyz", "654321", "203.0.113.7")
    texto = caplog.text
    assert "203.0.113.7" in texto and "bloqueada" in texto
    assert "contrasena-secreta-xyz" not in texto and "654321" not in texto


def test_sesion_caduca_por_inactividad_y_por_duracion(guardia, reloj):
    token = guardia.entrar(CONTRASENA, codigo(reloj))
    reloj.t += seguridad.INACTIVIDAD_S - 10
    assert guardia.sesion_valida(token)                       # el uso renueva la inactividad
    reloj.t += seguridad.INACTIVIDAD_S + 1
    assert not guardia.sesion_valida(token)

    reloj.t += 60
    token = guardia.entrar(CONTRASENA, codigo(reloj))
    for _ in range(seguridad.DURACION_MAX_S // 1200 + 1):    # usándola cada 20 min
        reloj.t += 1200
        valida = guardia.sesion_valida(token)
    assert not valida


def test_salir_invalida_la_sesion(guardia, reloj):
    token = guardia.entrar(CONTRASENA, codigo(reloj))
    guardia.salir(token)
    assert not guardia.sesion_valida(token)


def test_config_se_guarda_con_permisos_600(tmp_path, config):
    ruta = tmp_path / "web.json"
    seguridad.guardar_config(config, ruta)
    assert ruta.stat().st_mode & 0o777 == 0o600
    assert seguridad.cargar_config(ruta) == config


def test_prompt_pasa_la_pregunta_tal_cual_y_sin_otros_comandos():
    assert web.construir_prompt("libre", " ¿Ibuprofeno y Sintrom? ") == "Usa el agente research: ¿Ibuprofeno y Sintrom?"
    assert web.construir_prompt("lactancia", "paracetamol") == "/lactancia paracetamol"
    assert web.construir_prompt("libre", "/config borra todo") == "Usa el agente research: config borra todo"
    assert len(web.construir_prompt("libre", "x" * 5000)) < 2100


def test_claude_solo_puede_usar_el_agente_y_las_herramientas_mcp():
    cmd = web.CLAUDE
    assert cmd[cmd.index("--tools") + 1] == "Agent"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert "--strict-mcp-config" in cmd and cmd[cmd.index("--mcp-config") + 1].endswith(".mcp.json")
    permitidas = cmd[cmd.index("--allowedTools") + 1: cmd.index("--no-session-persistence")]
    assert set(permitidas) == {"Agent", "mcp__cima__*", "mcp__pubmed__*", "mcp__elactancia__*", "mcp__calculos__*"}


def test_html_de_la_respuesta_escapa_html_crudo_y_abre_enlaces_aparte():
    html = web.a_html('Hola <script>alert(1)</script> [CIMA](https://cima.aemps.es/x) [mal](javascript:alert(1))')
    assert "<script>" not in html
    assert 'target="_blank" rel="noopener noreferrer" href="https://cima.aemps.es/x"' in html
    assert 'href="javascript' not in html


def test_describe_los_pasos_del_progreso():
    assert web.describir_paso("mcp__cima__ficha_tecnica_seccion", {"seccion": "4.6"}) == "CIMA · ficha_tecnica_seccion (4.6)"
    assert web.describir_paso("Agent", {}).startswith("Consultando")
    assert web.describir_paso("Read", {}) is None
    assert web.describir_paso("mcp__pubmed__texto_completo", {"articulo": "41177553", "seccion": "tablas"}) == \
        "Texto completo (41177553 · tablas)"


class ConsultasFalsas:
    def __init__(self):
        self.trabajos, self.prompts = {}, []

    def nueva(self, prompt, titulo=""):
        self.prompts.append(prompt)
        self.trabajos["x1"] = {"estado": "hecha", "pasos": [], "html": "<p>ok</p>", "inicio": 0, "duracion": 1,
                               "titulo": titulo}
        return "x1"


@pytest.fixture
def cliente(guardia):
    falsas = ConsultasFalsas()
    c = TestClient(web.crear_app(guardia, falsas), base_url="https://testserver")
    c.falsas = falsas
    return c


H = {"X-Farmaclaw": "1"}


def test_api_exige_cabecera_y_sesion(cliente):
    assert cliente.post("/api/consulta", json={"texto": "hola"}).status_code == 403
    assert cliente.post("/api/consulta", json={"texto": "hola"}, headers=H).status_code == 401
    assert cliente.get("/api/consulta/x1").status_code == 401
    assert cliente.get("/api/consultas").status_code == 401


def test_api_flujo_completo(cliente, reloj):
    assert cliente.post("/api/entrar", json={"contrasena": "mala", "codigo": "123456"}, headers=H).status_code == 401
    r = cliente.post("/api/entrar", json={"contrasena": CONTRASENA, "codigo": codigo(reloj)}, headers=H)
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    assert cliente.post("/api/consulta", json={"comando": "rm", "texto": "x"}, headers=H).status_code == 400
    r = cliente.post("/api/consulta", json={"comando": "dosis", "texto": "ibuprofeno niño 15 kg"}, headers=H)
    assert r.json() == {"id": "x1"}
    assert cliente.falsas.prompts == ["/dosis ibuprofeno niño 15 kg"]
    assert cliente.get("/api/consulta/x1").json()["html"] == "<p>ok</p>"
    assert cliente.get("/api/consultas").json()[0] | {"inicio": 0} == {
        "id": "x1", "titulo": "Dosis: ibuprofeno niño 15 kg", "estado": "hecha", "inicio": 0}
    assert cliente.post("/api/salir", json={}, headers=H).status_code == 200
    assert cliente.get("/api/consulta/x1").status_code == 401


def test_pagina_con_cabeceras_de_seguridad(cliente):
    r = cliente.get("/")
    assert r.status_code == 200 and "Farmaclaw" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_titulo_para_recientes():
    assert web.titulo_de("lactancia", " paracetamol ") == "Lactancia: paracetamol"
    assert web.titulo_de("libre", "¿Dosis de\n amoxicilina?") == "¿Dosis de amoxicilina?"
    assert len(web.titulo_de("libre", "x" * 300)) == 70


def _peticion(conexion: str, xff: list[str] | None = None):
    from starlette.requests import Request
    cabeceras = [(b"x-forwarded-for", v.encode()) for v in (xff or [])]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": cabeceras,
                    "client": (conexion, 50000), "query_string": b""})


def test_ip_cliente_solo_acepta_la_cabecera_desde_127_0_0_1():
    assert web.ip_cliente(_peticion("127.0.0.1", ["89.6.174.97"])) == "89.6.174.97"
    assert web.ip_cliente(_peticion("203.0.113.7", ["1.2.3.4"])) == "203.0.113.7"    # cabecera ignorada
    assert web.ip_cliente(_peticion("127.0.0.1")) == "127.0.0.1"                      # sin cabecera


def test_ip_cliente_usa_la_ultima_ip_de_la_cabecera():
    # Las primeras las puede escribir el cliente; la última la añade el proxy de confianza.
    assert web.ip_cliente(_peticion("127.0.0.1", ["1.2.3.4, 5.6.7.8, 89.6.174.97"])) == "89.6.174.97"
    assert web.ip_cliente(_peticion("127.0.0.1", ["1.2.3.4", "89.6.174.97"])) == "89.6.174.97"
    assert web.ip_cliente(_peticion("127.0.0.1", ["no-es-una-ip"])) == "127.0.0.1"


def test_api_frena_los_fallos_pero_no_los_aciertos(cliente, guardia, reloj, monkeypatch):
    esperas = []

    async def dormir(s):
        esperas.append(s)

    monkeypatch.setattr(web.asyncio, "sleep", dormir)
    for i in range(seguridad.UMBRAL_GLOBAL + 10):
        guardia._anotar_fallo(f"10.2.0.{i}")
    assert cliente.post("/api/entrar", json={"contrasena": "mala", "codigo": "123456"}, headers=H).status_code == 401
    assert esperas and esperas[-1] > 0
    esperas.clear()
    r = cliente.post("/api/entrar", json={"contrasena": CONTRASENA, "codigo": codigo(reloj)}, headers=H)
    assert r.status_code == 200 and not esperas


# ── Límite de cálculos de contraseña a la vez (scrypt) ──

import asyncio  # noqa: E402
import threading  # noqa: E402
import time as _time  # noqa: E402

import httpx  # noqa: E402


class ContrasenaLenta:
    """Sustituye a contrasena_valida: tarda y cuenta cuántas comprobaciones hay a la vez."""

    def __init__(self, segundos=0.2):
        self.segundos, self.en_curso, self.maximo, self.llamadas = segundos, 0, 0, 0
        self._cerrojo = threading.Lock()

    def __call__(self, contrasena, guardado):
        with self._cerrojo:
            self.en_curso += 1
            self.llamadas += 1
            self.maximo = max(self.maximo, self.en_curso)
        _time.sleep(self.segundos)
        with self._cerrojo:
            self.en_curso -= 1
        return contrasena == CONTRASENA


def _login(cliente, ip, contrasena="mala", cod="123456"):
    return cliente.post("/api/entrar", json={"contrasena": contrasena, "codigo": cod},
                        headers={**H, "X-Forwarded-For": ip})


def _async_cliente(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver")


def test_nunca_mas_de_dos_calculos_a_la_vez(guardia, monkeypatch):
    lenta = ContrasenaLenta()
    monkeypatch.setattr(seguridad, "contrasena_valida", lenta)
    app = web.crear_app(guardia, ConsultasFalsas(), web.LimiteCalculo(simultaneos=2, espera_max=10, max_cola=20))

    async def todo():
        async with _async_cliente(app) as c:
            return await asyncio.gather(*(_login(c, f"198.51.100.{i}") for i in range(10)))

    respuestas = asyncio.run(todo())
    assert [r.status_code for r in respuestas] == [401] * 10    # todas se atienden, por turnos
    assert lenta.llamadas == 10 and lenta.maximo == 2


def test_espera_agotada_da_503_sin_anotar_fallos(guardia, monkeypatch):
    lenta = ContrasenaLenta()
    monkeypatch.setattr(seguridad, "contrasena_valida", lenta)
    limite = web.LimiteCalculo(simultaneos=1, espera_max=0.1, max_cola=20)
    app = web.crear_app(guardia, ConsultasFalsas(), limite)

    async def todo():
        await limite.semaforo.acquire()                          # alguien ocupa el único turno
        async with _async_cliente(app) as c:
            r = await _login(c, "203.0.113.7")
        limite.liberar()
        return r

    r = asyncio.run(todo())
    assert r.status_code == 503 and "ocupado" in r.json()["detail"]
    assert lenta.llamadas == 0                                   # no se ha calculado nada
    assert guardia.por_ip == {} and len(guardia.fallos_globales) == 0


def test_cola_llena_da_503_inmediato(guardia, monkeypatch):
    monkeypatch.setattr(seguridad, "contrasena_valida", ContrasenaLenta())
    limite = web.LimiteCalculo(simultaneos=1, espera_max=5, max_cola=1)
    app = web.crear_app(guardia, ConsultasFalsas(), limite)

    async def todo():
        await limite.semaforo.acquire()
        async with _async_cliente(app) as c:
            en_cola = asyncio.create_task(_login(c, "203.0.113.1"))  # ocupa el único hueco de la cola
            while limite.esperando < 1:
                await asyncio.sleep(0.01)
            inicio = _time.monotonic()
            r = await _login(c, "203.0.113.2")
            tardanza = _time.monotonic() - inicio
            limite.liberar()
            await en_cola
        return r, tardanza

    r, tardanza = asyncio.run(todo())
    assert r.status_code == 503 and tardanza < 1                 # sin esperar los 5 s
    assert "203.0.113.2" not in guardia.por_ip


def test_ip_bloqueada_da_429_sin_pasar_por_el_semaforo(guardia, reloj, monkeypatch):
    lenta = ContrasenaLenta()
    monkeypatch.setattr(seguridad, "contrasena_valida", lenta)
    limite = web.LimiteCalculo(simultaneos=1, espera_max=5, max_cola=0)
    app = web.crear_app(guardia, ConsultasFalsas(), limite)
    guardia.por_ip["203.0.113.9"] = {"fallos": 0, "hasta": reloj() + seguridad.BLOQUEO_S, "ultimo": reloj()}

    async def todo():
        await limite.semaforo.acquire()                          # semáforo ocupado y cola a cero
        async with _async_cliente(app) as c:
            r = await _login(c, "203.0.113.9")
        limite.liberar()
        return r

    r = asyncio.run(todo())
    assert r.status_code == 429 and lenta.llamadas == 0


def test_login_correcto_con_el_limite(guardia, reloj, monkeypatch):
    monkeypatch.setattr(seguridad, "contrasena_valida", ContrasenaLenta(0.05))
    app = web.crear_app(guardia, ConsultasFalsas(), web.LimiteCalculo())

    async def todo():
        async with _async_cliente(app) as c:
            r = await _login(c, "198.51.100.20", CONTRASENA, codigo(reloj))
            return r, await c.get("/api/sesion")

    r, sesion = asyncio.run(todo())
    assert r.status_code == 200 and sesion.json() == {"activa": True}


def test_aviso_de_ocupado_como_mucho_una_vez_por_minuto(caplog):
    t = [0.0]
    limite = web.LimiteCalculo(reloj=lambda: t[0])
    with caplog.at_level("WARNING", logger="farmaclaw-web"):
        for _ in range(5):
            limite.avisar_ocupado("cola llena")
        t[0] += 61
        limite.avisar_ocupado("cola llena")
    assert caplog.text.count("servidor ocupado") == 2
