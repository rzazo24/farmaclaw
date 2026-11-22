"""Calculadora de dosis pediátricas y conversiones: casos calculados a mano (sin red)."""

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from conftest import calculos

dosis = calculos.dosis_pediatrica
convertir = calculos.convertir


# ── dosis_pediatrica ──

def test_ibuprofeno_15_kg_a_7_5_mg_kg_con_20_mg_ml():
    r = dosis(15, 7.5, concentracion_mg_ml=20)
    assert r["mg_por_toma"] == 112.5
    assert r["ml_por_toma_exacto"] == 5.625
    assert r["ml_por_toma_redondeado"] == 5.6
    assert "redondeados a 0,1 mL" in r["redondeo"]
    assert r["calculo"] == ["15 kg × 7,5 mg/kg = 112,5 mg por toma",
                            "112,5 mg ÷ 20 mg/mL = 5,625 mL → redondeado a 0,1 mL: 5,6 mL"]
    assert r["avisos"] == [] and "limitada_por" not in r
    assert r["fuente"] == "Cálculo Farmaclaw (mcp/calculos)" and "ficha técnica" in r["nota"]
    assert r["datos_de_entrada"]["peso_kg"] == 15


def test_redondeo_al_decimo_mas_cercano():
    assert dosis(13, 5, concentracion_mg_ml=20)["ml_por_toma_redondeado"] == 3.3    # 65 ÷ 20 = 3,25 → 3,3
    assert dosis(11.3, 10, concentracion_mg_ml=20)["ml_por_toma_redondeado"] == 5.7  # 113 ÷ 20 = 5,65 → 5,7


def test_admite_coma_decimal_en_texto():
    assert dosis("15", "7,5", concentracion_mg_ml="20")["mg_por_toma"] == 112.5


def test_sin_concentracion_solo_mg():
    r = dosis(15, 10)
    assert r["mg_por_toma"] == 150 and "ml_por_toma_exacto" not in r
    assert any("Sin concentración" in a for a in r["avisos"])


def test_total_diario():
    r = dosis(15, 10, concentracion_mg_ml=20, tomas_al_dia=3)
    assert r["mg_al_dia"] == 450
    assert "150 mg × 3 tomas = 450 mg al día" in r["calculo"]


def test_supera_el_maximo_por_toma():
    r = dosis(30, 15, concentracion_mg_ml=32, max_mg_toma=400)                  # 450 mg > 400 mg
    assert r["mg_por_toma_sin_limitar"] == 450 and r["mg_por_toma"] == 400
    assert r["limitada_por"] == "máximo por toma"
    assert r["ml_por_toma_exacto"] == 12.5                                       # 400 ÷ 32
    assert any(a.startswith("SUPERA EL MÁXIMO POR TOMA") for a in r["avisos"])


def test_supera_el_maximo_diario():
    r = dosis(20, 10, concentracion_mg_ml=20, tomas_al_dia=4, max_mg_dia=600)   # 200 × 4 = 800 > 600
    assert r["mg_por_toma_sin_limitar"] == 200
    assert r["mg_por_toma"] == 150 and r["mg_al_dia"] == 600                     # 600 ÷ 4
    assert r["limitada_por"] == "máximo diario"
    assert r["ml_por_toma_exacto"] == 7.5
    assert any(a.startswith("SUPERA EL MÁXIMO DIARIO") for a in r["avisos"])


def test_supera_los_dos_maximos():
    r = dosis(40, 15, tomas_al_dia=4, max_mg_toma=500, max_mg_dia=1500)         # 600 → 500; 2000 > 1500 → 375
    assert r["mg_por_toma"] == 375 and r["limitada_por"] == "máximo por toma y máximo diario"


def test_maximo_diario_sin_tomas_avisa():
    r = dosis(15, 10, max_mg_dia=400)
    assert any("tomas_al_dia" in a for a in r["avisos"])


def test_dentro_de_los_maximos_no_avisa():
    r = dosis(15, 10, tomas_al_dia=3, max_mg_toma=400, max_mg_dia=1200)
    assert all("SUPERA" not in a for a in r["avisos"])
    assert "limitada_por" not in r


def test_aviso_de_mas_de_40_kg_sin_bloquear():
    r = dosis(45, 10, concentracion_mg_ml=20)
    assert r["mg_por_toma"] == 450
    assert any("dosis de adulto" in a for a in r["avisos"])


@pytest.mark.parametrize("args, mensaje", [
    ({"peso_kg": 0.4, "dosis_mg_kg": 10}, "entre 0,5 y 150 kg"),
    ({"peso_kg": 151, "dosis_mg_kg": 10}, "entre 0,5 y 150 kg"),
    ({"peso_kg": -5, "dosis_mg_kg": 10}, "mayor que 0"),
    ({"peso_kg": "quince", "dosis_mg_kg": 10}, "tiene que ser un número"),
    ({"peso_kg": 15, "dosis_mg_kg": 0}, "mayor que 0"),
    ({"peso_kg": 15, "dosis_mg_kg": 150}, "no es una dosis razonable"),
    ({"peso_kg": 15, "dosis_mg_kg": 10, "concentracion_mg_ml": 0}, "mayor que 0"),
    ({"peso_kg": 15, "dosis_mg_kg": 10, "concentracion_mg_ml": "x"}, "tiene que ser un número"),
    ({"peso_kg": 15, "dosis_mg_kg": 10, "tomas_al_dia": 2.5}, "número entero"),
    ({"peso_kg": 15, "dosis_mg_kg": 10, "max_mg_toma": -1}, "mayor que 0"),
    ({"peso_kg": float("nan"), "dosis_mg_kg": 10}, "finito"),
    ({"peso_kg": True, "dosis_mg_kg": 10}, "tiene que ser un número"),
])
def test_validaciones_dan_error_claro(args, mensaje):
    with pytest.raises(ToolError, match=mensaje):
        dosis(**args)



# ── dosis diaria repartida (dosis_mg_kg_dia) ──

def test_dosis_diaria_repartida_en_tomas():
    r = dosis(15, dosis_mg_kg_dia=20, tomas_al_dia=3, concentracion_mg_ml=20)    # 300 mg/día ÷ 3
    assert r["mg_al_dia"] == 300 and r["mg_por_toma"] == 100
    assert r["ml_por_toma_exacto"] == 5 and r["ml_por_toma_redondeado"] == 5
    assert r["calculo"] == ["15 kg × 20 mg/kg/día = 300 mg al día",
                            "300 mg ÷ 3 tomas = 100 mg por toma",
                            "100 mg ÷ 20 mg/mL = 5 mL → redondeado a 0,1 mL: 5 mL"]
    assert r["datos_de_entrada"]["dosis_mg_kg_dia"] == 20
    assert r["datos_de_entrada"]["dosis_mg_kg_por_toma"] is None


def test_dosis_diaria_alta_15_kg():
    r = dosis(15, dosis_mg_kg_dia=30, tomas_al_dia=3, concentracion_mg_ml=20)    # 450 ÷ 3 = 150 → 7,5 mL
    assert (r["mg_al_dia"], r["mg_por_toma"], r["ml_por_toma_exacto"]) == (450, 150, 7.5)


def test_dosis_diaria_no_exacta_sin_redondeos_del_agente():
    r = dosis(10, dosis_mg_kg_dia=20, tomas_al_dia=3, concentracion_mg_ml=20)    # 200 ÷ 3 = 66,667 mg
    assert r["mg_por_toma"] == 66.6667 and r["ml_por_toma_redondeado"] == 3.3     # 3,333 mL → 3,3


def test_dosis_diaria_con_maximo_diario():
    r = dosis(20, dosis_mg_kg_dia=40, tomas_al_dia=4, max_mg_dia=600)             # 800 > 600 → 150 por toma
    assert r["mg_por_toma"] == 150 and r["mg_al_dia"] == 600 and r["limitada_por"] == "máximo diario"


@pytest.mark.parametrize("args, mensaje", [
    ({"peso_kg": 15}, "Indica una sola dosis"),
    ({"peso_kg": 15, "dosis_mg_kg": 10, "dosis_mg_kg_dia": 30, "tomas_al_dia": 3}, "Indica una sola dosis"),
    ({"peso_kg": 15, "dosis_mg_kg_dia": 30}, "hace falta «tomas_al_dia»"),
    ({"peso_kg": 15, "dosis_mg_kg_dia": 0, "tomas_al_dia": 3}, "mayor que 0"),
    ({"peso_kg": 15, "dosis_mg_kg_dia": 400, "tomas_al_dia": 2}, "no es una dosis razonable"),
])
def test_validaciones_de_la_dosis_diaria(args, mensaje):
    with pytest.raises(ToolError, match=mensaje):
        dosis(**args)


# ── convertir ──

def test_mg_a_ml_y_vuelta():
    assert convertir(112.5, "mg", "mL", concentracion_mg_ml=20)["resultado"] == 5.625
    r = convertir(5, "ml", "mg", concentracion_mg_ml=20)
    assert r["resultado"] == 100 and r["unidad"] == "mg" and r["calculo"] == "5 mL × 20 mg/mL = 100 mg"


def test_mcg_y_mg():
    assert convertir(250, "mcg", "mg")["resultado"] == 0.25
    assert convertir(0.5, "mg", "µg")["resultado"] == 500


def test_porcentaje_a_mg_ml():
    r = convertir(2, "%", "mg/mL")
    assert r["resultado"] == 20 and "1 % = 1 g/100 mL = 10 mg/mL" in r["calculo"]


def test_gotas_solo_con_gotas_por_ml_de_la_ficha():
    with pytest.raises(ToolError, match="gotas_por_ml"):
        convertir(20, "gotas", "ml")                                            # nunca supone 20 gotas/mL
    assert convertir(20, "gotas", "ml", gotas_por_ml=40)["resultado"] == 0.5
    assert convertir(1.5, "ml", "gotas", gotas_por_ml=20)["resultado"] == 30


@pytest.mark.parametrize("args, mensaje", [
    ((10, "mg", "ml"), "concentracion_mg_ml"),
    ((10, "mg", "gotas"), "Conversión no admitida"),
    ((10, "kg", "mg"), "no reconocida"),
    ((-1, "mg", "mcg"), "mayor que 0"),
    (("diez", "mg", "mcg"), "tiene que ser un número"),
])
def test_conversiones_con_error(args, mensaje):
    with pytest.raises(ToolError, match=mensaje):
        convertir(*args)
