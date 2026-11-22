"""Texto completo de PubMed Central: procesado del XML (sin red) y casos reales (red)."""

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from conftest import pubmed
import pmc

# Artículo JATS mínimo de ejemplo (inventado, con la estructura real de PMC).
XML = """<?xml version="1.0"?>
<pmc-articleset><article xmlns:xlink="http://www.w3.org/1999/xlink">
<front>
  <journal-meta><journal-title-group><journal-title>Revista de Prueba</journal-title></journal-title-group></journal-meta>
  <article-meta>
    <article-id pub-id-type="pmid">11111111</article-id>
    <article-id pub-id-type="pmcid">PMC2222222</article-id>
    <title-group><article-title>Fármaco X frente a placebo</article-title></title-group>
    <author-notes><fn fn-type="COI-statement"><p>Dr A ha recibido honorarios de Laboratorio Y.</p></fn></author-notes>
    <pub-date><year>2024</year></pub-date>
    <permissions><license xlink:href="https://creativecommons.org/licenses/by/4.0/">
      <license-p>Artículo de acceso abierto CC BY 4.0.</license-p></license></permissions>
    <funding-group><award-group><funding-source>Laboratorio Y</funding-source></award-group></funding-group>
  </article-meta>
</front>
<body>
  <sec><title>Introduction</title><p>Primer párrafo con cita<xref ref-type="bibr">1</xref>.</p></sec>
  <sec><title>Methods</title>
    <p>Diseño aleatorizado.</p>
    <sec><title>Patients</title><p>Criterios:</p>
      <list><list-item><p>Edad 18-65</p></list-item><list-item><p>Sin insuficiencia renal</p></list-item></list>
    </sec>
  </sec>
  <sec><title>Results</title><p>La respuesta fue del 40 %.</p>
    <table-wrap><label>Table 1</label><caption><p>Resultados principales</p></caption>
      <table><tr><th>Grupo</th><th>Respuesta</th></tr>
             <tr><td>Fármaco X</td><td>40 %<break/>(IC 95 % 30-50)</td></tr></table>
      <table-wrap-foot><p>IC: intervalo de confianza.</p></table-wrap-foot>
    </table-wrap>
  </sec>
</body>
<back>
  <fn-group><fn><p>Funding: Laboratorio Y.</p></fn></fn-group>
  <ref-list><ref><mixed-citation>Referencia que no debe salir.</mixed-citation></ref></ref-list>
</back>
</article></pmc-articleset>"""

BLOQUEADO = """<?xml version="1.0"?><pmc-articleset><article><!--The publisher of this article does not allow
downloading of the full text in XML form.--><front><article-meta><article-id pub-id-type="pmcid">PMC3</article-id>
<title-group><article-title>T</article-title></title-group></article-meta></front></article></pmc-articleset>"""


def test_metadatos_y_licencia():
    d = pmc.parsear(XML)
    assert (d["titulo"], d["pmid"], d["pmcid"], d["anio"]) == ("Fármaco X frente a placebo", "11111111", "PMC2222222", "2024")
    assert d["revista"] == "Revista de Prueba"
    assert d["licencia"] == "Artículo de acceso abierto CC BY 4.0."
    assert d["licencia_url"] == "https://creativecommons.org/licenses/by/4.0/"
    assert not d["bloqueado"]


def test_secciones_con_subtitulos_listas_y_sin_bibliografia():
    d = pmc.parsear(XML)
    assert [t for t, _ in d["secciones"]] == ["Introduction", "Methods", "Results"]
    metodos = dict(d["secciones"])["Methods"]
    assert "### Patients" in metodos and "- Edad 18-65" in metodos and "- Sin insuficiencia renal" in metodos
    texto = "\n".join(x for _, x in d["secciones"])
    assert "Referencia que no debe salir" not in texto
    assert "[Table 1: Resultados principales · ver seccion='tablas']" in dict(d["secciones"])["Results"]


def test_tablas_en_filas_con_celdas_separadas():
    (etiqueta, texto), = pmc.parsear(XML)["tablas"]
    assert etiqueta == "Table 1: Resultados principales"
    assert "Grupo | Respuesta" in texto
    assert "Fármaco X | 40 %; (IC 95 % 30-50)" in texto
    assert "IC: intervalo de confianza." in texto


def test_declaraciones_de_financiacion_y_conflictos():
    decl = pmc.parsear(XML)["declaraciones"]
    assert "Financiación: Laboratorio Y" in decl
    assert "Conflictos de interés: Dr A ha recibido honorarios de Laboratorio Y." in decl
    assert "Funding: Laboratorio Y." in decl


def test_detecta_editorial_que_no_permite_el_texto():
    assert pmc.parsear(BLOQUEADO)["bloqueado"]


def test_elegir_seccion_por_numero_nombre_o_en_espanol():
    s = pmc.parsear(XML)["secciones"]
    assert pmc.elegir_seccion(s, "2")[0] == "Methods"
    assert pmc.elegir_seccion(s, "results")[0] == "Results"
    assert pmc.elegir_seccion(s, "métodos")[0] == "Methods"
    assert pmc.elegir_seccion(s, "9") is None


def test_identificador_invalido_falla_sin_llamar_a_la_api():
    with pytest.raises(ToolError, match="PMID"):
        pubmed.texto_completo("clopidogrel")


@pytest.mark.red
def test_texto_completo_real():
    indice = pubmed.texto_completo("41177553")   # metaanálisis de biosimilares de aflibercept (BMJ Open Ophthalmol)
    assert indice["pmcid"] == "PMC12581046"
    assert "Methods" in [s["titulo"] for s in indice["secciones"]]
    assert indice["tablas"] and indice["hay_declaraciones"]
    assert indice["fuente"].endswith("PMC12581046/")
    tablas = pubmed.texto_completo("PMC12581046", "tablas")
    assert "Karkhaneh" in tablas["texto"]


@pytest.mark.red
def test_texto_completo_no_disponible_real():
    with pytest.raises(ToolError, match="no está en PubMed Central"):
        pubmed.texto_completo("7202185")          # artículo de 1981, sin PMC
    with pytest.raises(ToolError, match="editorial no permite"):
        pubmed.texto_completo("38275741")         # Cochrane: en PMC, pero sin XML completo
