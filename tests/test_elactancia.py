import pytest
from conftest import elactancia as s
from mcp.server.fastmcp.exceptions import ToolError

# ---------- Sin red: lectura de la ficha (HTML sintético con la estructura de e-lactancia) ----------

FICHA = """
<p class="small last-update">Última actualización: 25 de Agosto de 2022</p>
<h1 class="term-header  risk-level2-prods ">
        Testamina</h1>
<div class="box grey-box squared  risk-level2 ">
    <h2 class="risk-header"><p>Compatibilidad limitada</p></h2>
</div>
            </div>
<div class="box grey-box squared  risk-comment-level2 ">
    <p>Pasa a leche.<sup>(Autor 2019)</sup></p>
    <p>Vigilar al lactante &amp; consultar.</p>
</div>
            </div>
<div class="box grey-box squared risk-alt">
    <h3 class="risk-header">Alternativas</h3>
    <ul>
        <li ><a class="link_r0" href="/breastfeeding/otra/product/"  >Otra</a> (Producto seguro.)</li>
    </ul>
</div>
            </div>
<div class="box grey-box squared risk-level0-legend"></div>
"""


def test_parsear_ficha():
    r = s._parsear(FICHA)
    assert r["producto"] == "Testamina"
    assert r["nivel_riesgo"] == 2
    assert r["riesgo"].startswith("Riesgo alto")
    assert r["comentario"] == "Pasa a leche. (Autor 2019)\nVigilar al lactante & consultar."
    assert r["alternativas"] == [{"nombre": "Otra", "nota": "Producto seguro.",
                                  "fuente": "https://www.e-lactancia.org/breastfeeding/otra/product/"}]
    assert r["ultima_actualizacion"] == "25 de Agosto de 2022"


def test_sin_alternativas():
    r = s._parsear(FICHA.replace('<li ><a class="link_r0" href="/breastfeeding/otra/product/"  >Otra</a> (Producto seguro.)</li>', ""))
    assert isinstance(r["alternativas"], str)


# ---------- Con red (e-lactancia.org; 1 petición/s) ----------


@pytest.mark.red
def test_ibuprofeno_compatible():
    r = s.elactancia("ibuprofeno")
    assert r["producto"] == "Ibuprofeno"
    assert r["nivel_riesgo"] == 0
    assert r["fuente"].startswith("https://www.e-lactancia.org/")
    assert "CC BY-NC-SA" in r["licencia"]


@pytest.mark.red
def test_amiodarona_con_alternativas():
    r = s.elactancia("amiodarona")
    assert r["nivel_riesgo"] >= 2
    assert isinstance(r["alternativas"], list) and r["alternativas"]


@pytest.mark.red
def test_marca_remite_a_cima_e_inexistente():
    with pytest.raises(ToolError, match="CIMA"):
        s.elactancia("Couldina con Ibuprofeno")
    with pytest.raises(ToolError, match="No he encontrado"):
        s.elactancia("farmaclawina")
