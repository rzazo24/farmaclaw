"""Texto completo (PMC, Europe PMC, Unpaywall): procesado del XML y del PDF (sin red) y casos reales (red)."""

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from conftest import pubmed
import pdf
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


def test_identificadores_admitidos():
    assert pubmed._identificar("41177553") == {"pmid": "41177553"}
    assert pubmed._identificar("pmc12581046") == {"pmcid": "PMC12581046"}
    assert pubmed._identificar("https://doi.org/10.1136/bmjophth-2025-002509") == {"doi": "10.1136/bmjophth-2025-002509"}


def test_no_descarga_de_la_red_local():
    assert not pubmed._url_publica("http://127.0.0.1:8787/")
    assert not pubmed._url_publica("http://localhost/x.pdf")
    assert not pubmed._url_publica("file:///etc/passwd")


TEXTO_PDF = """Fármaco X frente a placebo
Revista de Prueba 2024
Abstract
Resumen breve.
1. Introduction
El fármaco X se usa en la
enfermedad Y.
2. Methods
Ensayo aleatorizado con 100 pa-
cientes.
Results
La respuesta fue del 40 %.
References
1. Referencia que no debe salir.
Conflict of interest
Los autores declaran honorarios de Laboratorio Y."""


def test_pdf_divide_por_titulos_sin_bibliografia():
    secs = pdf.secciones(TEXTO_PDF.replace("pa-\ncientes", "pacientes"))
    titulos = [t for t, _ in secs]
    assert titulos == ["Inicio", "Abstract", "Introduction", "Methods", "Results", "Conflict of interest"]
    assert "El fármaco X se usa en la enfermedad Y." in dict(secs)["Introduction"]
    assert "Referencia que no debe salir" not in "\n".join(x for _, x in secs)


def test_pdf_rechaza_lo_que_no_es_pdf():
    with pytest.raises(ValueError, match="no es un PDF"):
        pdf.parsear(b"<!DOCTYPE html><html>Acceso denegado</html>")


@pytest.mark.red
def test_texto_completo_real():
    indice = pubmed.texto_completo("41177553")   # metaanálisis de biosimilares de aflibercept (BMJ Open Ophthalmol)
    assert indice["pmcid"] == "PMC12581046" and indice["origen"] == "PubMed Central"
    assert "Methods" in [s["titulo"] for s in indice["secciones"]]
    assert indice["tablas"] and indice["hay_declaraciones"]
    assert indice["fuente"].endswith("PMC12581046/")
    tablas = pubmed.texto_completo("PMC12581046", "tablas")
    assert "Karkhaneh" in tablas["texto"]


@pytest.mark.red
def test_texto_completo_por_doi_va_a_pmc():
    assert pubmed.texto_completo("10.1136/bmjophth-2025-002509")["cita"] == "PMC12581046"


@pytest.mark.red
def test_texto_completo_no_disponible_real():
    with pytest.raises(ToolError, match="PubMed Central: el artículo no está"):
        pubmed.texto_completo("7202185")          # artículo de 1981, sin PMC
    with pytest.raises(ToolError, match="la editorial no permite descargar el texto"):
        pubmed.texto_completo("38275741")         # Cochrane: en PMC, pero sin XML completo
