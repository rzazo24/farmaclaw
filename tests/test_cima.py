import pytest
from conftest import cima as s
from mcp.server.fastmcp.exceptions import ToolError

# ---------- Sin red ----------


def test_limpiar_html_conserva_vinetas_y_quita_vacias():
    html = "<ul><li><p>Asma</p></li><li></li><li>Rinitis &amp; urticaria</li></ul><p>Fin</p>"
    texto = s._limpiar_html(html)
    assert "• Asma" in texto
    assert "• Rinitis & urticaria" in texto
    assert "Fin" in texto
    assert "<" not in texto
    assert not any(linea.strip() == "•" for linea in texto.splitlines())


def test_fecha_en_hora_de_madrid():
    # 15/01/2024 00:00 en Madrid = 14/01/2024 23:00 UTC: en UTC saldría el día anterior.
    assert s._fecha(1705273200000) == "2024-01-15"
    assert s._fecha(None) is None


def test_partir_corta_por_parrafos_y_no_pierde_texto():
    parrafos = [f"Párrafo {i} " + "x" * 900 for i in range(20)]
    texto = "\n\n".join(parrafos)
    partes = s._partir(texto)
    assert len(partes) > 1
    assert all(len(p) <= s.MAX_CHARS for p in partes)
    assert all(p.startswith("Párrafo") for p in partes)
    assert sum(len(p) for p in partes) >= len(texto) - 2 * len(partes)


def test_partir_texto_corto_una_parte():
    assert s._partir("corto") == ["corto"]


def test_filtro_sin_receta_y_comercializados():
    meds = [{"nregistro": "1", "receta": True, "comerc": True},
            {"nregistro": "2", "receta": False, "comerc": True},
            {"nregistro": "3", "receta": False, "comerc": False},
            {"nregistro": "4", "comerc": True}]                     # sin el campo: no se da por sin receta
    assert [m["nregistro"] for m in s.filtrar(meds, solo_sin_receta=True)] == ["2", "3"]
    assert [m["nregistro"] for m in s.filtrar(meds, True, True)] == ["2"]
    assert len(s.filtrar(meds)) == 4


def test_sin_receta_se_pide_a_la_api(monkeypatch):
    pedidos = []

    def get_falso(path, params=None):
        pedidos.append((path, params))
        return {"resultados": [{"nregistro": "9", "nombre": "OMEPRAZOL X 20 MG", "receta": False, "comerc": True}]}
    monkeypatch.setattr(s, "_get", get_falso)
    r = s.buscar_medicamento("omeprazol", solo_comercializados=True, solo_sin_receta=True)
    assert all(p == {**p, "receta": 0, "comerc": 1} for _, p in pedidos) and len(pedidos) == 2
    assert r["medicamentos"][0]["necesita_receta"] is False


def _cima_paginada(monkeypatch, totales: dict, primero_en_pagina2: str | None = None):
    """Simula /medicamentos con páginas de 200: totales = {"nombre": N, "practiv1": M}."""
    pedidas = []

    def get_falso(path, params=None):
        campo = "nombre" if "nombre" in params else "practiv1"
        pagina, total = params["pagina"], totales[campo]
        pedidas.append((campo, pagina))
        inicio = (pagina - 1) * 200
        if inicio >= total:
            return {"totalFilas": 0, "pagina": pagina, "tamanioPagina": 200, "resultados": []}
        filas = [{"nregistro": f"{campo}{i}", "nombre": f"ZETA {campo} {i:04d}", "comerc": True}
                 for i in range(inicio, min(total, inicio + 200))]
        if primero_en_pagina2 and pagina == 2:
            filas[0]["nombre"] = primero_en_pagina2
        return {"totalFilas": total, "pagina": pagina, "tamanioPagina": 200, "resultados": filas}
    monkeypatch.setattr(s, "_get", get_falso)
    return pedidas


def test_lee_varias_paginas_y_ordena_sobre_todo(monkeypatch):
    pedidas = _cima_paginada(monkeypatch, {"nombre": 10, "practiv1": 350}, primero_en_pagina2="OMEPRAZOL X 20 MG")
    r = s.buscar_medicamento("omeprazol")
    assert pedidas == [("nombre", 1), ("practiv1", 1), ("practiv1", 2)]
    assert r["leidos"] == 360 and r["total_en_cima"] == {"por_nombre": 10, "por_principio_activo": 350}
    assert r["medicamentos"][0]["nombre"] == "OMEPRAZOL X 20 MG"   # estaba en la página 2: se ordena sobre todo
    assert r["aviso"].startswith("Mostrando 15 de 360.") and "Hay más resultados" not in r["aviso"]


def test_para_en_el_limite_y_lo_avisa(monkeypatch):
    pedidas = _cima_paginada(monkeypatch, {"nombre": 0, "practiv1": 1000})
    r = s.buscar_medicamento("paracetamol")
    assert [p for p in pedidas if p[0] == "practiv1"] == [("practiv1", 1), ("practiv1", 2)]   # 400 = 2 páginas
    assert r["leidos"] == s.LIMITE_POR_CAMPO == 400
    assert r["total_en_cima"]["por_principio_activo"] == 1000
    assert "Hay más resultados en CIMA" in r["aviso"]


def test_sin_aviso_si_se_ve_todo(monkeypatch):
    _cima_paginada(monkeypatch, {"nombre": 4, "practiv1": 6})
    r = s.buscar_medicamento("almagato")
    assert r["total"] == 10 and r["leidos"] == 10 and "aviso" not in r


def test_aviso_busqueda():
    assert s.aviso_busqueda(15, False) is None
    assert s.aviso_busqueda(16, False).startswith("Mostrando 15 de 16")
    assert "Hay más resultados" in s.aviso_busqueda(3, True)
    assert s.aviso_busqueda(20, False).endswith("o usa solo_comercializados / solo_sin_receta.")
    assert s.aviso_busqueda(20, False, comercializados=True).endswith("o usa solo_sin_receta.")
    assert s.aviso_busqueda(20, False, True, True).endswith("(nombre comercial, dosis, forma farmacéutica).")


# ---------- Con red (API de CIMA) ----------


@pytest.mark.red
def test_buscar_principio_activo_incluye_marcas():
    r = s.buscar_medicamento("valproico", solo_comercializados=True)
    nombres = [m["nombre"] for m in r["medicamentos"]]
    assert any(n.startswith("ACIDO VALPROICO") for n in nombres)
    assert any(n.startswith("DEPAKINE") for n in nombres), "la búsqueda por principio activo debe traer marcas"
    assert all(m["fuente"].startswith("https://cima.aemps.es/") for m in r["medicamentos"])


@pytest.mark.red
def test_buscar_ordena_primero_los_que_empiezan_por_la_consulta():
    r = s.buscar_medicamento("omeprazol", solo_comercializados=True)
    assert r["medicamentos"][0]["nombre"].startswith("OMEPRAZOL")


@pytest.mark.red
def test_buscar_lee_todas_las_paginas_omeprazol():
    r = s.buscar_medicamento("omeprazol")
    assert r["total_en_cima"]["por_principio_activo"] > 200      # más de una página en CIMA
    assert r["leidos"] > 200                                        # y se han leído más de 200 (1 página)
    assert r["mostrados"] == 15 and r["aviso"].startswith("Mostrando 15 de")


@pytest.mark.red
def test_buscar_sin_receta_omeprazol():
    todos = s.buscar_medicamento("omeprazol", solo_comercializados=True)
    sin = s.buscar_medicamento("omeprazol", solo_comercializados=True, solo_sin_receta=True)
    assert sin["total"] < todos["total"]
    assert sin["medicamentos"] and all(m["necesita_receta"] is False for m in sin["medicamentos"])
    assert any(m["necesita_receta"] for m in todos["medicamentos"])


@pytest.mark.red
def test_buscar_por_cn_con_digito_de_control():
    r = s.buscar_medicamento("712729.8")
    assert r["medicamentos"][0]["nregistro"] == "51347"  # Aspirina C


@pytest.mark.red
def test_interaccion_clopidogrel_omeprazol_en_ficha_45():
    r = s.ficha_tecnica_seccion("71774", "interacciones")  # Clopidogrel Almus
    assert r["seccion"] == "4.5"
    assert "omeprazol" in r["texto"].lower()
    assert r["fuente"].startswith("https://cima.aemps.es/")


@pytest.mark.red
def test_seccion_larga_por_partes():
    p1 = s.ficha_tecnica_seccion("84517", "4.6")  # Ácido valproico Aurovitas
    assert p1["total_partes"] >= 2
    assert "continúa" in p1["texto"]
    ultima = s.ficha_tecnica_seccion("84517", "4.6", p1["total_partes"])
    assert "continúa" not in ultima["texto"]


@pytest.mark.red
def test_prospecto_alias():
    r = s.prospecto_seccion("71774", "efectos adversos")
    assert r["seccion"] == "4"


@pytest.mark.red
def test_problemas_suministro_estructura():
    r = s.problemas_suministro("51347")
    assert {"consulta", "con_problemas", "resultado", "fuente"} <= r.keys()


@pytest.mark.red
def test_presentaciones_con_cn():
    r = s.presentaciones("51347")  # Aspirina C
    cns = [p["cn"] for p in r["presentaciones"]]
    assert "712729" in cns
    assert s.presentaciones("712729.8")["nregistro"] == "51347"  # también por CN


def test_presentaciones_rechaza_nombres():
    with pytest.raises(ToolError, match="buscar_medicamento"):
        s.presentaciones("aspirina")


@pytest.mark.red
def test_notas_seguridad_valproico():
    r = s.notas_seguridad("84517")
    assert r["total"] >= 1
    fechas = [n["fecha"] for n in r["notas"]]
    assert fechas == sorted(fechas, reverse=True)


@pytest.mark.red
def test_materiales_informativos_valproico():
    r = s.materiales_informativos("84517")
    assert isinstance(r["materiales"], list) and r["materiales"]


@pytest.mark.red
def test_errores_claros():
    with pytest.raises(ToolError, match="No se ha encontrado"):
        s.ficha_tecnica_seccion("999999999", "4.3")
    with pytest.raises(ToolError, match="No existe la sección"):
        s.ficha_tecnica_seccion("71774", "9.9")
    with pytest.raises(ToolError, match="parte"):
        s.ficha_tecnica_seccion("71774", "4.3", 99)
