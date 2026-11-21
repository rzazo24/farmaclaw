"""Pruebas sin red de las comprobaciones automáticas de evaluacion/ejecutar.py."""

import importlib.util
import json
from pathlib import Path

_ruta = Path(__file__).resolve().parents[1] / "evaluacion" / "ejecutar.py"
_spec = importlib.util.spec_from_file_location("evaluacion_ejecutar", _ruta)
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

FICHA = "https://cima.aemps.es/cima/dochtml/ft/71774/FT_71774.html"


def _stream(respuesta: str, seccion: str = "4.5", n_fichas: int = 2) -> str:
    """Salida stream-json mínima: el principal delega en research, que lee fichas."""
    eventos = [{"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
        {"type": "tool_use", "id": "A1", "name": "Agent", "input": {"subagent_type": "research"}}]}}]
    for i in range(n_fichas):
        eventos += [
            {"type": "assistant", "parent_tool_use_id": "A1", "message": {"content": [
                {"type": "tool_use", "id": f"T{i}", "name": "mcp__cima__ficha_tecnica_seccion",
                 "input": {"nregistro": "71774", "seccion": seccion}}]}},
            {"type": "user", "parent_tool_use_id": "A1", "message": {"content": [
                {"type": "tool_result", "tool_use_id": f"T{i}",
                 "content": [{"type": "text", "text": json.dumps({"fuente": FICHA, "texto": "… [PMID: 12345678] …"})}]}]}},
        ]
    eventos.append({"type": "result", "result": respuesta, "total_cost_usd": 0.1})
    return "\n".join(json.dumps(e) for e in eventos)


CASO_INT = {"id": "x", "categoria": "interaccion",
            "debe_consultar": [{"herramienta": "ficha_tecnica_seccion", "seccion": "4.5", "minimo": 2}]}


def _resultado(caso, salida):
    return {c["comprobacion"]: c["ok"] for c in ev.comprobar(caso, ev.analizar(salida))}


def test_respuesta_correcta_pasa_todo():
    texto = f"Desaconsejado [CIMA: Clopidogrel, sección 4.5]({FICHA}). Contrastar con Bot PLUS."
    assert all(_resultado(CASO_INT, _stream(texto)).values())


def test_detecta_cita_inventada():
    texto = f"Ver [PMID: 99999999](https://pubmed.ncbi.nlm.nih.gov/99999999/) y [CIMA: X]({FICHA}). Bot PLUS."
    r = _resultado(CASO_INT, _stream(texto))
    assert r["Enlaces procedentes de las herramientas"] is False


def test_pmid_citado_por_lactmed_cuenta_como_respaldado():
    texto = f"Dato [PMID: 12345678](https://pubmed.ncbi.nlm.nih.gov/12345678/) [CIMA: X]({FICHA}). Bot PLUS."
    assert _resultado(CASO_INT, _stream(texto))["Enlaces procedentes de las herramientas"] is True


def test_detecta_falta_de_cita_y_de_bot_plus():
    r = _resultado(CASO_INT, _stream("Hay interacción, mejor pantoprazol."))
    assert r["Al menos una cita con enlace"] is False
    assert r["Aviso de Bot PLUS"] is False


def test_cuenta_lecturas_minimas_y_acepta_alias():
    assert _resultado(CASO_INT, _stream(f"[CIMA: X]({FICHA}) Bot PLUS", n_fichas=1))["Consultó ficha_tecnica_seccion 4.5 (≥2)"] is False
    assert _resultado(CASO_INT, _stream(f"[CIMA: X]({FICHA}) Bot PLUS", seccion="interacciones"))["Consultó ficha_tecnica_seccion 4.5 (≥2)"] is True


def test_subseccion_cuenta_para_su_seccion():
    caso = {"id": "x", "categoria": "lactancia", "debe_consultar": [{"herramienta": "ficha_tecnica_seccion", "seccion": "4.6"}]}
    assert _resultado(caso, _stream(f"[CIMA: X]({FICHA})", seccion="4.6.2"))["Consultó ficha_tecnica_seccion 4.6 (≥1)"] is True


def test_detecta_datos_identificativos():
    caso = {"id": "x", "categoria": "lactancia", "no_debe_aparecer": ["12345678Z", "García López"]}
    r = _resultado(caso, _stream(f"Para la Sra. García López: compatible [CIMA: X]({FICHA})."))
    assert r["No repite datos identificativos"] is False


def test_sin_delegacion_y_sin_respuesta():
    salida = json.dumps({"type": "result", "result": "Compatible.", "total_cost_usd": 0})
    assert _resultado({"id": "x", "categoria": "lactancia"}, salida)["Delegó en el agente research"] is False
    assert _resultado({"id": "x", "categoria": "lactancia"}, "")["Hay respuesta"] is False


def test_trampa_reconoce_no_evidencia():
    caso = {"id": "x", "categoria": "sin_evidencia"}
    assert _resultado(caso, _stream("No he encontrado evidencia sobre ese fármaco."))["Dice que no hay evidencia"] is True
    assert _resultado(caso, _stream("Interacciona por el CYP3A4."))["Dice que no hay evidencia"] is False
