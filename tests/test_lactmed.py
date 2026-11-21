import lactmed
import pytest
from conftest import pubmed as s
from mcp.server.fastmcp.exceptions import ToolError

# ---------- Sin red: lectura del XML de LactMed ----------

NXML = b"""<?xml version="1.0" encoding="UTF-8"?>
<book-part-wrapper id="LM999">
  <book-part><book-part-meta>
    <title-group><title>Testamine</title></title-group>
    <pub-history><date date-type="revised"><day>5</day><month>3</month><year>2026</year></date></pub-history>
  </book-part-meta>
  <body>
    <sec id="LM999.s1"><title>Drug Levels and Effects</title>
      <sec id="LM999.s1.1"><title>Summary of Use during Lactation</title>
        <p>Low levels in milk.<sup>[<xref ref-type="bibr" rid="r1 r2">1,2</xref>]</sup></p>
        <p>Second paragraph.</p>
      </sec>
      <sec id="LM999.s1.5"><title>Alternate Drugs to Consider</title><p>Otherdrug</p></sec>
    </sec>
    <sec><title>Substance Identification</title>
      <sec><title>Drug Class</title><p>Analgesics</p></sec>
    </sec>
  </body>
  <back><ref-list>
    <ref id="r1"><element-citation><pub-id pub-id-type="pmid">111</pub-id></element-citation></ref>
    <ref id="r2"><element-citation><pub-id pub-id-type="pmid">222</pub-id></element-citation></ref>
  </ref-list></back>
  </book-part>
</book-part-wrapper>
"""


def test_parsear_ficha():
    f = lactmed._parsear(NXML)
    assert f["id"] == "LM999"
    assert f["titulo"] == "Testamine"
    assert f["revisado"] == "2026-03-05"  # mes y día con cero delante
    assert f["secciones"]["resumen"] == "Low levels in milk.[PMID: 111, 222]\nSecond paragraph."
    assert f["secciones"]["alternativas"] == "Otherdrug"
    assert f["secciones"]["clase"] == "Analgesics"


def test_paginas_auxiliares_se_descartan():
    assert lactmed._parsear(b'<book-part-wrapper id="glos"><body><sec><title>Glossary</title></sec></body></book-part-wrapper>') is None


@pytest.fixture
def indice_falso(monkeypatch):
    fichas = [{"id": f"LM{i}", "titulo": t, "revisado": "2026-01-01", "secciones": {"resumen": t}}
              for i, t in enumerate(["Acetaminophen", "Hydroxyzine", "Amoxicillin and Clavulanic Acid",
                                     "Ticarcillin and Clavulanic Acid", "Valproic Acid"])]
    monkeypatch.setattr(lactmed, "_cache", {"generado": "2026-01-01", "fichas": fichas})


def test_buscar_sinonimos_dci(indice_falso):
    assert lactmed.buscar("paracetamol")[0]["titulo"] == "Acetaminophen"
    assert lactmed.buscar("valproate")[0]["titulo"] == "Valproic Acid"


def test_buscar_texto_corto_no_da_falsos_positivos(indice_falso):
    assert lactmed.buscar("xyz") == []


def test_herramienta_varios_candidatos(indice_falso):
    r = s.lactmed("clavulan")
    assert "candidatos" in r and len(r["candidatos"]) == 2


def test_herramienta_errores(indice_falso):
    with pytest.raises(ToolError, match="No he encontrado"):
        s.lactmed("farmaclawina")
    with pytest.raises(ToolError, match="Sección no válida"):
        s.lactmed("paracetamol", "dosis")


# ---------- Con el índice real (uv run python mcp/pubmed/lactmed.py) ----------


def test_indice_real_sertralina(indice_lactmed):
    r = s.lactmed("sertraline")
    assert r["farmaco"] == "Sertraline"
    assert "[PMID:" in r["texto"]
    assert r["fuente"].startswith("https://www.ncbi.nlm.nih.gov/books/n/lactmed/")


def test_indice_real_seccion_por_partes(indice_lactmed):
    p1 = s.lactmed("sertraline", "niveles")
    assert p1["total_partes"] >= 2
    assert s.lactmed("sertraline", "niveles", 2)["parte"] == 2
