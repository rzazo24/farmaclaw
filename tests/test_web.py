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


def test_bloquea_tras_cinco_fallos(guardia, reloj):
    for _ in range(seguridad.MAX_FALLOS):
        guardia.entrar("mala", "123456")
    assert guardia.bloqueado() > 0
    assert guardia.entrar(CONTRASENA, codigo(reloj)) is None   # ni con las buenas
    reloj.t += seguridad.BLOQUEO_S + 60
    assert guardia.bloqueado() == 0
    assert guardia.entrar(CONTRASENA, codigo(reloj))


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
    assert set(permitidas) == {"Agent", "mcp__cima__*", "mcp__pubmed__*", "mcp__elactancia__*"}


def test_html_de_la_respuesta_escapa_html_crudo_y_abre_enlaces_aparte():
    html = web.a_html('Hola <script>alert(1)</script> [CIMA](https://cima.aemps.es/x) [mal](javascript:alert(1))')
    assert "<script>" not in html
    assert 'target="_blank" rel="noopener noreferrer" href="https://cima.aemps.es/x"' in html
    assert 'href="javascript' not in html


def test_describe_los_pasos_del_progreso():
    assert web.describir_paso("mcp__cima__ficha_tecnica_seccion", {"seccion": "4.6"}) == "CIMA · ficha_tecnica_seccion (4.6)"
    assert web.describir_paso("Agent", {}).startswith("Consultando")
    assert web.describir_paso("Read", {}) is None


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
