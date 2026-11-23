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


# ───────────────────────── Segunda opinión ─────────────────────────

def test_segunda_opinion_solo_para_dudosos_y_solo_si_es_concluyente(monkeypatch):
    llamadas = []
    respuestas = [
        # 1ª: M1 dudoso, N1 cumple
        _salida_juez([{"id": "M1", "criterio": "a", "veredicto": "dudoso", "evidencia": ""},
                      {"id": "N1", "criterio": "b", "veredicto": "cumple", "evidencia": ""}]),
        # 2ª (solo M1): cumple con evidencia literal
        _salida_juez([{"id": "M1", "criterio": "a", "veredicto": "cumple", "evidencia": "puede administrarse con pantoprazol"}]),
    ]

    class Proc:
        stderr = ""

    def falso(cmd, **k):
        llamadas.append(cmd[cmd.index("--model") + 1])
        p = Proc()
        p.stdout = respuestas[len(llamadas) - 1]
        return p

    monkeypatch.setattr(ev.subprocess, "run", falso)
    j = ev.juzgar(CASO_JUEZ, RESPUESTA, "sonnet", segunda="opus")
    assert llamadas == ["sonnet", "opus"]
    assert j["veredictos"][0]["veredicto"] == "cumple"
    assert j["veredictos"][0]["segunda_opinion"] == {"modelo": "opus", "veredicto": "cumple"}
    assert "segunda_opinion" not in j["veredictos"][1]


def test_sin_dudosos_no_hay_segunda_llamada(monkeypatch):
    llamadas = []

    class Proc:
        stderr = ""
        stdout = _salida_juez([{"id": "M1", "criterio": "a", "veredicto": "cumple", "evidencia": "puede administrarse con pantoprazol"},
                               {"id": "N1", "criterio": "b", "veredicto": "cumple", "evidencia": ""}])

    monkeypatch.setattr(ev.subprocess, "run", lambda *a, **k: llamadas.append(1) or Proc())
    ev.juzgar(CASO_JUEZ, RESPUESTA, "sonnet", segunda="opus")
    assert len(llamadas) == 1


def test_segunda_opinion_dudosa_no_sustituye(monkeypatch):
    respuestas = iter([
        _salida_juez([{"id": "M1", "criterio": "a", "veredicto": "dudoso", "evidencia": ""},
                      {"id": "N1", "criterio": "b", "veredicto": "cumple", "evidencia": ""}]),
        _salida_juez([{"id": "M1", "criterio": "a", "veredicto": "dudoso", "evidencia": ""}]),
    ])

    class Proc:
        stderr = ""

    def falso(*a, **k):
        p = Proc()
        p.stdout = next(respuestas)
        return p

    monkeypatch.setattr(ev.subprocess, "run", falso)
    j = ev.juzgar(CASO_JUEZ, RESPUESTA, "sonnet", segunda="opus")
    assert j["veredictos"][0]["veredicto"] == "dudoso"
    assert j["veredictos"][0]["segunda_opinion"]["veredicto"] == "dudoso"


# ───────────────────────── Criterios anclados ─────────────────────────

def test_criterios_anclados_conservan_cita_y_fuente():
    caso = {"debe_mencionar": [{"criterio": "La ficha desaconseja omeprazol.", "cita": "debe desaconsejarse",
                                "fuente": "https://cima.aemps.es/x"}, "BORRADOR - revisar: otro"]}
    c = ev.criterios(caso)
    assert c[0]["criterio"] == "La ficha desaconseja omeprazol." and c[0]["fuente"] == "https://cima.aemps.es/x"
    assert c[1]["criterio"] == "otro" and "cita" not in c[1]


# ───────────────────────── Comparación y aceptados ─────────────────────────

def _res(id_, veredictos, auto_ok=True):
    return {"id": id_, "categoria": "interaccion", "caso": {"pregunta": "p"}, "respuesta": "r", "llamadas": [],
            "comprobaciones": [{"comprobacion": "Aviso de Bot PLUS", "ok": auto_ok, "detalle": ""}],
            "juez": {"estado": "juzgado", "modelo": "sonnet",
                     "veredictos": [{"id": i, "tipo": "debe_mencionar", "criterio": c, "veredicto": v, "evidencia": ""}
                                    for i, c, v in veredictos]}}


def test_problemas_excluye_aceptados():
    r = _res("x", [("M1", "pantoprazol", "dudoso"), ("M2", "CYP2C19", "no_cumple")], auto_ok=False)
    p = ev.problemas(r, {("x", "pantoprazol")})
    assert "juez:pantoprazol" not in p
    assert "juez:CYP2C19" in p and "auto:Aviso de Bot PLUS" in p


def test_informe_separa_nuevos_de_ya_vistos(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "RESULTADOS", tmp_path)
    monkeypatch.setattr(ev, "ACEPTADOS", tmp_path / "aceptados.yaml")
    anterior = tmp_path / "2026-01-01_000000"
    anterior.mkdir()
    (anterior / "x.json").write_text(json.dumps({k: v for k, v in _res("x", [("M1", "viejo", "dudoso"), ("M2", "bien", "cumple")]).items() if k != "caso"}))
    actual = tmp_path / "2026-01-02_000000"
    actual.mkdir()
    r = _res("x", [("M1", "viejo", "dudoso"), ("M2", "bien", "no_cumple")])
    texto = ev.informe(actual, [r], "abc").read_text()
    para_revisar = texto.split("## Para revisar")[1].split("## Resumen")[0]
    nuevos = para_revisar.split("<details>")[0]
    assert "M2: bien" in nuevos            # empeoró: nuevo
    assert "M1: viejo" not in nuevos        # ya fallaba: va al desplegable
    assert "Ya fallaban en la ejecución anterior (1)" in para_revisar
    assert "Comparado con: 2026-01-01_000000" in texto


def test_linea_base_ignora_carpetas_de_prueba(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "RESULTADOS", tmp_path)
    for nombre in ("2026-01-01_000000", "2026-01-01_000000_manipulada"):
        (tmp_path / nombre).mkdir()
        (tmp_path / nombre / "x.json").write_text(json.dumps({"id": "x", "marca": nombre}))
    base, nombre = ev.linea_base(tmp_path / "2026-01-02_000000", "x")
    assert nombre == "2026-01-01_000000"


def test_aceptar_y_que_no_vuelva_a_salir(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "RESULTADOS", tmp_path)
    monkeypatch.setattr(ev, "ACEPTADOS", tmp_path / "aceptados.yaml")
    d = tmp_path / "2026-01-01_000000"
    d.mkdir()
    r = _res("x", [("N3", "Afirmar algo sin cita", "dudoso")])
    (d / "x.json").write_text(json.dumps({k: v for k, v in r.items() if k != "caso"}))
    ev.aceptar("x:N3", "recomendación práctica razonable")
    assert ("x", "Afirmar algo sin cita") in ev.cargar_aceptados()
    actual = tmp_path / "2026-01-02_000000"
    actual.mkdir()
    assert "Nada nuevo ✅" in ev.informe(actual, [r], "abc").read_text()


# ───────────────────────── Anclaje: verificación de citas ─────────────────────────

import importlib.util as _ilu  # noqa: E402
import sys as _sys  # noqa: E402

_sys.path.insert(0, str(_ruta.parent))
_spec_a = _ilu.spec_from_file_location("evaluacion_anclar", _ruta.parent / "anclar.py")
anclar = _ilu.module_from_spec(_spec_a)
_spec_a.loader.exec_module(anclar)


def test_anclaje_acepta_solo_citas_literales_con_url_devuelta():
    resultado = json.dumps({"fuente": "https://cima.aemps.es/ft/1.html",
                            "texto": "Como precaución debe desaconsejarse el uso concomitante de omeprazol.\nOtra frase."})
    fuentes = anclar.texto_fuentes([{"herramienta": "mcp__cima__ficha_tecnica_seccion", "resultado": resultado}])
    validos, descartados = anclar.verificar([
        {"criterio": "ok", "cita": "debe desaconsejarse el uso concomitante de omeprazol", "fuente": "https://cima.aemps.es/ft/1.html"},
        {"criterio": "inventada", "cita": "está totalmente contraindicado con omeprazol", "fuente": "https://cima.aemps.es/ft/1.html"},
        {"criterio": "url ajena", "cita": "debe desaconsejarse el uso concomitante de omeprazol", "fuente": "https://otra.web/x"},
    ], fuentes)
    assert [v["criterio"] for v in validos] == ["ok"]
    assert [d["motivo"] for d in descartados] == ["cita no literal", "URL no devuelta por ninguna herramienta"]


def test_lista_rapido_apunta_a_casos_existentes():
    import yaml
    datos = yaml.safe_load(ev.CASOS.read_text())
    ids = {c["id"] for c in datos["casos"]}
    assert datos["rapido"] and set(datos["rapido"]) <= ids


def test_seccion_admite_varias_validas():
    req = {"herramienta": "ficha_tecnica_seccion", "seccion": ["4.1", "4.2"]}
    llamada = lambda s: {"herramienta": "mcp__cima__ficha_tecnica_seccion", "entrada": {"seccion": s}}
    assert ev._cumple(llamada("4.1"), req)
    assert ev._cumple(llamada("4.2"), req)
    assert not ev._cumple(llamada("4.3"), req)


def test_la_evaluacion_lanza_claude_como_la_web():
    """Con --strict-mcp-config Claude Code espera a que conecten los MCP (sin él arrancan en «pending»)."""
    import importlib.util
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("web_app_cmp", raiz / "web" / "app.py")
    import sys
    sys.path.insert(0, str(raiz / "web"))
    web = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(web)
    cmd = ev.COMANDO
    assert "--strict-mcp-config" in cmd and cmd[cmd.index("--mcp-config") + 1].endswith(".mcp.json")
    permitidas = lambda c: c[c.index("--allowedTools") + 1: c.index("--no-session-persistence")]
    assert set(permitidas(cmd)) - {"Agent"} == set(permitidas(web.CLAUDE)) - {"Agent"}


def test_evaluacion_lanza_claude_con_margen_para_los_mcp(monkeypatch):
    monkeypatch.delenv("MCP_TIMEOUT", raising=False)
    assert ev.entorno_claude()["MCP_TIMEOUT"] == "60000"


def test_todas_las_categorias_de_casos_yaml_son_conocidas():
    import yaml
    casos = yaml.safe_load((Path(__file__).resolve().parent.parent / "evaluacion" / "casos.yaml").read_text())["casos"]
    assert ev.categorias_desconocidas(casos) == []
    assert ev.categorias_desconocidas([{"categoria": "diagnostico"}]) == ["diagnostico"]


def test_debe_consultar_puede_exigir_tipos_de_busqueda():
    req = {"herramienta": "buscar_articulos", "tipos": ["Practice Guideline", "Guideline"]}
    llamada = lambda tipos: {"herramienta": "mcp__pubmed__buscar_articulos", "entrada": {"consulta": "x", "tipos": tipos}}
    assert ev._cumple(llamada(["Guideline"]), req)
    assert ev._cumple(llamada(["Systematic Review", "practice guideline"]), req)
    assert not ev._cumple(llamada(["Meta-Analysis"]), req)
    assert not ev._cumple({"herramienta": "mcp__pubmed__buscar_articulos", "entrada": {"consulta": "x"}}, req)


def test_derivacion_comprueba_que_remite_al_medico():
    caso = {"id": "x", "categoria": "derivacion"}
    def ok(texto):
        r = {"respuesta": texto, "llamadas": [], "subagentes": ["research"]}
        return {c["comprobacion"]: c["ok"] for c in ev.comprobar(caso, r)}["Remite al médico o al pediatra"]
    assert ok("Conviene que lo valore su médico [CIMA: X, sección 4.8](https://cima.aemps.es/x)")
    assert ok("Hay que derivarlo al pediatra.")
    assert not ok("Puede tomar ibuprofeno y esperar.")
