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


# ───────────────────────── Juez ─────────────────────────

RESPUESTA = ("La ficha **desaconseja** el uso de omeprazol con clopidogrel. "
             "Como alternativa, la ficha indica que clopidogrel puede administrarse con pantoprazol.")
CASO_JUEZ = {"id": "x", "categoria": "interaccion", "pregunta": "¿omeprazol y clopidogrel?", "revisado": True,
             "debe_mencionar": ["BORRADOR - revisar: pantoprazol como alternativa"],
             "no_debe": ["Recomendar separar las tomas"]}


def _salida_juez(veredictos, estructurada=True):
    datos = {"veredictos": veredictos}
    if estructurada:
        return json.dumps({"type": "result", "structured_output": datos, "total_cost_usd": 0.01})
    return json.dumps({"type": "result", "result": "```json\n" + json.dumps(datos) + "\n```"})


def test_criterios_quitan_prefijo_borrador_y_numeran():
    c = ev.criterios(CASO_JUEZ)
    assert [x["id"] for x in c] == ["M1", "N1"]
    assert c[0]["criterio"] == "pantoprazol como alternativa"


def test_parsear_juez_estructurado_y_en_texto():
    esperados = ev.criterios(CASO_JUEZ)
    vs = [{"id": "M1", "criterio": "x", "veredicto": "cumple", "evidencia": "puede administrarse con pantoprazol"},
          {"id": "N1", "criterio": "y", "veredicto": "cumple", "evidencia": ""}]
    for estructurada in (True, False):
        r = ev.parsear_juez(_salida_juez(vs, estructurada), esperados)
        assert [v["veredicto"] for v in r] == ["cumple", "cumple"]
        assert r[0]["tipo"] == "debe_mencionar"


def test_parsear_juez_invalido():
    esperados = ev.criterios(CASO_JUEZ)
    assert ev.parsear_juez("no es json", esperados) is None
    assert ev.parsear_juez(_salida_juez([{"id": "M1", "criterio": "x", "veredicto": "cumple", "evidencia": "a"}]), esperados) is None  # falta N1
    assert ev.parsear_juez(_salida_juez([{"id": "M1", "criterio": "x", "veredicto": "quizá", "evidencia": ""},
                                         {"id": "N1", "criterio": "y", "veredicto": "cumple", "evidencia": ""}]), esperados) is None


def test_evidencia_literal_tolera_markdown_y_espacios_pero_no_inventos():
    assert ev.evidencia_literal("La ficha desaconseja el uso", RESPUESTA)          # ignora **negrita**
    assert ev.evidencia_literal("  puede   administrarse con PANTOPRAZOL ", RESPUESTA)
    assert not ev.evidencia_literal("se recomienda pantoprazol 40 mg", RESPUESTA)
    assert not ev.evidencia_literal("", RESPUESTA)
    assert not ev.evidencia_literal("La", RESPUESTA)                               # demasiado corta


def _veredictos(m, ev_m, n, ev_n):
    return [{"id": "M1", "tipo": "debe_mencionar", "criterio": "a", "veredicto": m, "evidencia": ev_m},
            {"id": "N1", "tipo": "no_debe", "criterio": "b", "veredicto": n, "evidencia": ev_n}]


def test_cumple_sin_evidencia_literal_pasa_a_dudoso():
    r = ev.verificar_evidencias(_veredictos("cumple", "recomienda pantoprazol 40 mg", "cumple", ""), RESPUESTA)
    assert r[0]["veredicto"] == "dudoso" and r[0]["veredicto_juez"] == "cumple"
    assert r[1]["veredicto"] == "cumple"  # no_debe cumple = ausencia del error: no hay nada que citar


def test_cumple_con_evidencia_literal_se_mantiene():
    r = ev.verificar_evidencias(_veredictos("cumple", "puede administrarse con pantoprazol", "cumple", ""), RESPUESTA)
    assert r[0]["veredicto"] == "cumple"


def test_no_debe_no_cumple_exige_citar_el_error():
    r = ev.verificar_evidencias(_veredictos("no_cumple", "", "no_cumple", "separe las tomas 12 horas"), RESPUESTA)
    assert r[0]["veredicto"] == "no_cumple"   # un no_cumple de debe_mencionar no necesita cita
    assert r[1]["veredicto"] == "dudoso"      # afirma que el error está, pero no lo cita literalmente


def test_casos_no_revisados_no_se_juzgan(monkeypatch):
    def prohibido(*a, **k):
        raise AssertionError("no debe llamar al juez")
    monkeypatch.setattr(ev.subprocess, "run", prohibido)
    j = ev.juzgar({**CASO_JUEZ, "revisado": False}, RESPUESTA, "sonnet")
    assert j == {"estado": "no_revisado"}
    assert ev.resumen_juez(j) == ("⚠️", "sin validar")


def test_juez_reintenta_y_marca_error_sin_romper(monkeypatch):
    llamadas = []

    class Proc:
        stdout, stderr = "basura", ""

    monkeypatch.setattr(ev.subprocess, "run", lambda *a, **k: llamadas.append(1) or Proc())
    j = ev.juzgar(CASO_JUEZ, RESPUESTA, "sonnet")
    assert len(llamadas) == 2  # un reintento
    assert j["estado"] == "error"
    assert ev.resumen_juez(j)[1] == "juez: error"


def test_juez_correcto_y_iconos(monkeypatch):
    class Proc:
        stderr = ""
        stdout = _salida_juez([{"id": "M1", "criterio": "a", "veredicto": "cumple", "evidencia": "puede administrarse con pantoprazol"},
                               {"id": "N1", "criterio": "b", "veredicto": "cumple", "evidencia": ""}])

    monkeypatch.setattr(ev.subprocess, "run", lambda *a, **k: Proc())
    j = ev.juzgar(CASO_JUEZ, RESPUESTA, "sonnet")
    assert ev.resumen_juez(j) == ("✅", "2/2")
    j["veredictos"][1]["veredicto"] = "dudoso"
    assert ev.resumen_juez(j)[0] == "🟡"
    j["veredictos"][0]["veredicto"] = "no_cumple"
    assert ev.resumen_juez(j)[0] == "❌"
