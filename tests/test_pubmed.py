import xml.etree.ElementTree as ET

import pytest
from conftest import pubmed as s
from mcp.server.fastmcp.exceptions import ToolError

# ---------- Sin red ----------

ARTICULO = """
<PubmedArticle>
  <MedlineCitation>
    <PMID>12345678</PMID>
    <Article>
      <Journal><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue>
        <ISOAbbreviation>Basic Clin Pharmacol Toxicol</ISOAbbreviation></Journal>
      <ArticleTitle>Clopidogrel with and without a <i>PPI</i>.</ArticleTitle>
      <Abstract>
        <AbstractText Label="BACKGROUND">Fondo.</AbstractText>
        <AbstractText Label="RESULTS">Resultados.</AbstractText>
      </Abstract>
      <AuthorList>
        <Author><LastName>Axelsson</LastName><Initials>MAB</Initials></Author>
        <Author><LastName>Parodi</LastName><Initials>N</Initials></Author>
        <Author><LastName>Wikstrom</LastName><Initials>E</Initials></Author>
        <Author><LastName>Otro</LastName><Initials>A</Initials></Author>
      </AuthorList>
      <PublicationTypeList><PublicationType>Meta-Analysis</PublicationType></PublicationTypeList>
    </Article>
    <MeshHeadingList><MeshHeading><DescriptorName>Clopidogrel</DescriptorName></MeshHeading></MeshHeadingList>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="pubmed">12345678</ArticleId>
      <ArticleId IdType="doi">10.1111/bcpt.1</ArticleId>
      <ArticleId IdType="pmc">PMC111</ArticleId>
    </ArticleIdList>
    <ReferenceList><Reference><ArticleIdList>
      <ArticleId IdType="pmc">PMC999</ArticleId>
    </ArticleIdList></Reference></ReferenceList>
  </PubmedData>
</PubmedArticle>
"""


def test_parsear_articulo():
    a = s._parsear(ET.fromstring(ARTICULO))
    assert a["pmid"] == "12345678"
    assert a["titulo"] == "Clopidogrel with and without a PPI."
    assert a["autores"] == "Axelsson MAB, Parodi N, Wikstrom E, et al."
    assert a["abstract"] == "BACKGROUND: Fondo.\nRESULTS: Resultados."
    assert a["mesh"] == ["Clopidogrel"]
    assert a["doi_url"] == "https://doi.org/10.1111/bcpt.1"
    assert a["fuente"] == "https://pubmed.ncbi.nlm.nih.gov/12345678/"


def test_pmc_es_el_del_articulo_no_el_de_la_bibliografia():
    a = s._parsear(ET.fromstring(ARTICULO))
    assert a["texto_completo_pmc"].endswith("/PMC111/")


def test_anio():
    assert s._anio("2026 Oct 28") == "2026"
    assert s._anio("") is None


def test_tipo_invalido_falla_sin_llamar_a_la_api():
    with pytest.raises(ToolError, match="no válido"):
        s.buscar_articulos("aspirin", tipos=["Blog"])


def test_filtro_de_guias_de_practica_clinica(monkeypatch):
    pedidos = []

    def get_falso(utilidad, params, json_=True):
        pedidos.append(params["term"])
        return {"esearchresult": {"idlist": []}}
    monkeypatch.setattr(s, "_get", get_falso)
    with pytest.raises(ToolError):                       # sin resultados: lo importante es la consulta
        s.buscar_articulos("otitis media", tipos=["Practice Guideline", "guideline"], desde_anio=2018)
    assert pedidos == ['(otitis media) AND ("Practice Guideline"[pt] OR "Guideline"[pt]) AND 2018:3000[dp]']


def test_pmids_invalidos():
    with pytest.raises(ToolError, match="PMID válido"):
        s.obtener_resumen(["abc"])
    with pytest.raises(ToolError, match="Máximo"):
        s.obtener_resumen([str(i) for i in range(1, 20)])


# ---------- Con red (NCBI E-utilities) ----------


@pytest.mark.red
def test_buscar_con_filtros():
    r = s.buscar_articulos("clopidogrel omeprazole", 5, ["Meta-Analysis", "Systematic Review"], 2015)
    assert '"Meta-Analysis"[pt]' in r["consulta_pubmed"]
    assert "2015:3000[dp]" in r["consulta_pubmed"]
    assert r["articulos"]
    for a in r["articulos"]:
        assert int(a["anio"]) >= 2015
        assert {"Meta-Analysis", "Systematic Review"} & set(a["tipos"])
        assert a["fuente"] == f"https://pubmed.ncbi.nlm.nih.gov/{a['pmid']}/"


@pytest.mark.red
def test_buscar_guias_real():
    r = s.buscar_articulos("acute otitis media", 5, ["Practice Guideline", "Guideline"], 2010)
    assert r["articulos"]
    for a in r["articulos"]:
        assert {"Practice Guideline", "Guideline"} & set(a["tipos"])


@pytest.mark.red
def test_obtener_resumen_real():
    r = s.obtener_resumen(["40685887"])
    a = r["articulos"][0]
    assert "Clopidogrel" in a["titulo"]
    assert a["abstract"] and a["doi"]


@pytest.mark.red
def test_errores_reales():
    with pytest.raises(ToolError, match="No se han encontrado"):
        s.buscar_articulos("xqzwvklmnop farmaclaw")
    with pytest.raises(ToolError, match="No se ha encontrado"):
        s.obtener_resumen(["999999999"])
