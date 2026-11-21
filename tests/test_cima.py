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
