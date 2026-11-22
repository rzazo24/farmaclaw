"""Servidor MCP de cálculos farmacéuticos (Farmaclaw).

Solo aritmética determinista en Python: sin IA, sin red y sin datos propios.
Las cifras de partida (mg/kg, frecuencia, máximos, concentración) tienen que
salir de la ficha técnica; aquí solo se hacen las cuentas, paso a paso, para
que el agente no calcule de cabeza y el resultado se pueda comprobar a mano.

Se usa Decimal (no float) para que 15 × 7,5 sea exactamente 112,5.
Transporte stdio: stdout es el canal MCP, así que los logs van a stderr.
"""

import logging
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s calculos %(levelname)s %(message)s")

FUENTE = "Cálculo Farmaclaw (mcp/calculos)"
AVISO_FUENTE = ("Solo hace las cuentas: los datos de entrada (mg/kg, tomas, máximos, concentración) "
                "deben salir de la ficha técnica del medicamento.")
PESO_MIN, PESO_MAX, PESO_ADULTO = Decimal("0.5"), Decimal("150"), Decimal("40")
DOSIS_MAX_MG_KG = Decimal("100")

mcp = FastMCP("calculos")


# ───────────────────────── Utilidades ─────────────────────────

def _num(nombre: str, valor, minimo: Decimal | None = Decimal("0"), estricto: bool = True) -> Decimal:
    """Convierte a Decimal (admite "7,5"). Error claro en español si no es un número válido."""
    if isinstance(valor, bool) or valor is None:
        raise ToolError(f"«{nombre}» tiene que ser un número.")
    try:
        d = Decimal(str(valor).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ToolError(f"«{nombre}» tiene que ser un número (has puesto «{valor}»).")
    if not d.is_finite():
        raise ToolError(f"«{nombre}» tiene que ser un número finito.")
    if minimo is not None and (d <= minimo if estricto else d < minimo):
        raise ToolError(f"«{nombre}» tiene que ser mayor que {_fmt(minimo)} (has puesto {_fmt(d)}).")
    return d


def _fmt(d: Decimal, decimales: int = 3) -> str:
    """Número con coma decimal y sin ceros sobrantes: 112,5 · 5,625 · 300."""
    d = d.quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP).normalize()
    texto = format(d, "f")
    return texto.replace(".", ",")


def _f(d: Decimal, decimales: int = 4) -> float:
    return float(d.quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP))


def _redondear_ml(ml: Decimal) -> Decimal:
    """A 0,1 mL, redondeo «comercial» (5,625 → 5,6; 5,65 → 5,7)."""
    return ml.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _gota(mg_por_gota, gotas_por_ml, obligatorio: bool) -> tuple[Decimal | None, Decimal | None]:
    """Tamaño de la gota según la ficha: en mg por gota o en gotas por mL (uno de los dos, nunca supuesto)."""
    if mg_por_gota is not None and gotas_por_ml is not None:
        raise ToolError("Indica solo uno: «mg_por_gota» o «gotas_por_ml», el que dé la ficha técnica.")
    if obligatorio and mg_por_gota is None and gotas_por_ml is None:
        raise ToolError("Para trabajar con gotas hace falta el tamaño de la gota según la ficha técnica de ese "
                        "medicamento: «mg_por_gota» (p. ej. 4 mg/gota) o «gotas_por_ml». No se supone nunca.")
    return (_num("mg_por_gota", mg_por_gota) if mg_por_gota is not None else None,
            _num("gotas_por_ml", gotas_por_ml) if gotas_por_ml is not None else None)


def _redondear_gotas(gotas: Decimal) -> Decimal:
    return gotas.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


# ───────────────────────── Dosis pediátrica ─────────────────────────

@mcp.tool()
def dosis_pediatrica(
    peso_kg: float | str,
    dosis_mg_kg: float | str | None = None,
    dosis_mg_kg_dia: float | str | None = None,
    concentracion_mg_ml: float | str | None = None,
    tomas_al_dia: int | str | None = None,
    max_mg_toma: float | str | None = None,
    max_mg_dia: float | str | None = None,
    mg_por_gota: float | str | None = None,
    gotas_por_ml: float | str | None = None,
) -> dict:
    """Calcula una dosis pediátrica por peso, paso a paso (cálculo determinista, sin IA).

    Úsalo SIEMPRE para dosis por peso en lugar de calcular de cabeza.
    peso_kg: peso del niño en kg (0,5-150).
    dosis_mg_kg: dosis POR TOMA en mg/kg, si la ficha técnica (sección 4.2) la da así.
    dosis_mg_kg_dia: dosis POR DÍA en mg/kg, si la ficha la da así (p. ej. "20-30 mg/kg/día en
      3 tomas"); exige tomas_al_dia y la herramienta reparte la dosis entre las tomas.
      Indica solo una de las dos: dosis_mg_kg o dosis_mg_kg_dia.
    concentracion_mg_ml: concentración de la presentación (p. ej. 20 para 20 mg/mL);
      opcional: sin ella solo se devuelven mg.
    tomas_al_dia: opcional; con él se calcula el total diario.
    max_mg_toma, max_mg_dia: máximos de la ficha (opcionales). Si se superan, se avisa y se
      devuelve también la dosis limitada al máximo.
    mg_por_gota o gotas_por_ml (opcionales, uno de los dos, tal como lo dé la ficha): si se dan,
      devuelve también las gotas por toma (exactas y redondeadas a gota entera).
    Devuelve mg y mL por toma (exacto y redondeado a 0,1 mL), mg/día, avisos, la operación
    escrita en "calculo" y la "fuente" para citar.
    """
    peso = _num("peso_kg", peso_kg)
    if not PESO_MIN <= peso <= PESO_MAX:
        raise ToolError(f"El peso tiene que estar entre {_fmt(PESO_MIN)} y {_fmt(PESO_MAX)} kg (has puesto {_fmt(peso)} kg).")
    if (dosis_mg_kg is None) == (dosis_mg_kg_dia is None):
        raise ToolError("Indica una sola dosis: «dosis_mg_kg» (por toma) o «dosis_mg_kg_dia» (por día), "
                        "según la dé la ficha técnica.")
    conc = _num("concentracion_mg_ml", concentracion_mg_ml) if concentracion_mg_ml is not None else None
    tomas = None
    if tomas_al_dia is not None:
        tomas = _num("tomas_al_dia", tomas_al_dia)
        if tomas != tomas.to_integral_value() or tomas > 24:
            raise ToolError(f"«tomas_al_dia» tiene que ser un número entero entre 1 y 24 (has puesto {_fmt(tomas)}).")
    if dosis_mg_kg_dia is not None and tomas is None:
        raise ToolError("Con «dosis_mg_kg_dia» hace falta «tomas_al_dia» para repartir la dosis diaria.")
    if dosis_mg_kg is not None:
        dosis, dosis_dia = _num("dosis_mg_kg", dosis_mg_kg), None
    else:
        dosis_dia = _num("dosis_mg_kg_dia", dosis_mg_kg_dia)
        dosis = dosis_dia / tomas
    if dosis > DOSIS_MAX_MG_KG:
        raise ToolError(f"{_fmt(dosis)} mg/kg por toma no es una dosis razonable (máximo admitido: "
                        f"{_fmt(DOSIS_MAX_MG_KG)} mg/kg). Revisa que sea la dosis por toma y no la diaria, o las unidades.")
    max_toma = _num("max_mg_toma", max_mg_toma) if max_mg_toma is not None else None
    mg_gota, gotas_ml = _gota(mg_por_gota, gotas_por_ml, obligatorio=False)
    if gotas_ml is not None and conc is None:
        raise ToolError("Con «gotas_por_ml» hace falta «concentracion_mg_ml» para pasar de mg a mL y luego a gotas.")
    max_dia = _num("max_mg_dia", max_mg_dia) if max_mg_dia is not None else None

    avisos, pasos = [], []
    if peso > PESO_ADULTO:
        avisos.append(f"El peso ({_fmt(peso)} kg) es mayor de {_fmt(PESO_ADULTO)} kg: comprueba en la ficha "
                      "si ya corresponde la dosis de adulto.")

    if dosis_dia is not None:
        mg_dia_calculados = peso * dosis_dia
        mg_calculados = mg_dia_calculados / tomas
        pasos.append(f"{_fmt(peso)} kg × {_fmt(dosis_dia)} mg/kg/día = {_fmt(mg_dia_calculados)} mg al día")
        pasos.append(f"{_fmt(mg_dia_calculados)} mg ÷ {_fmt(tomas)} tomas = {_fmt(mg_calculados)} mg por toma")
    else:
        mg_calculados = peso * dosis
        pasos.append(f"{_fmt(peso)} kg × {_fmt(dosis)} mg/kg = {_fmt(mg_calculados)} mg por toma")
    mg_toma, limitada_por = mg_calculados, None

    if max_toma is not None and mg_toma > max_toma:
        avisos.append(f"SUPERA EL MÁXIMO POR TOMA: {_fmt(mg_toma)} mg > {_fmt(max_toma)} mg. "
                      f"Se aplica el máximo por toma: {_fmt(max_toma)} mg.")
        pasos.append(f"{_fmt(mg_toma)} mg > máximo por toma {_fmt(max_toma)} mg → se limita a {_fmt(max_toma)} mg")
        mg_toma, limitada_por = max_toma, "máximo por toma"

    mg_dia = None
    if tomas is not None:
        mg_dia = mg_toma * tomas
        if dosis_dia is None or limitada_por:  # con dosis diaria sin limitar, el total ya está en el primer paso
            pasos.append(f"{_fmt(mg_toma)} mg × {_fmt(tomas)} tomas = {_fmt(mg_dia)} mg al día")
        if max_dia is not None and mg_dia > max_dia:
            nuevo = max_dia / tomas
            avisos.append(f"SUPERA EL MÁXIMO DIARIO: {_fmt(mg_dia)} mg/día > {_fmt(max_dia)} mg/día. "
                          f"Se aplica el máximo diario repartido en {_fmt(tomas)} tomas: {_fmt(nuevo)} mg por toma.")
            pasos.append(f"{_fmt(mg_dia)} mg/día > máximo diario {_fmt(max_dia)} mg → "
                         f"{_fmt(max_dia)} mg ÷ {_fmt(tomas)} tomas = {_fmt(nuevo)} mg por toma")
            mg_toma, mg_dia = nuevo, max_dia
            limitada_por = "máximo diario" if limitada_por is None else "máximo por toma y máximo diario"
    elif max_dia is not None:
        avisos.append("No se ha comprobado el máximo diario: indica «tomas_al_dia».")

    resultado = {
        "mg_por_toma": _f(mg_toma),
        "mg_por_toma_sin_limitar": _f(mg_calculados) if limitada_por else None,
        "limitada_por": limitada_por,
        "mg_al_dia": _f(mg_dia) if mg_dia is not None else None,
    }
    if conc is not None:
        ml = mg_toma / conc
        ml_red = _redondear_ml(ml)
        pasos.append(f"{_fmt(mg_toma)} mg ÷ {_fmt(conc)} mg/mL = {_fmt(ml)} mL → redondeado a 0,1 mL: {_fmt(ml_red, 1)} mL")
        resultado |= {
            "ml_por_toma_exacto": _f(ml),
            "ml_por_toma_redondeado": _f(ml_red, 1),
            "redondeo": "mL redondeados a 0,1 mL (al décimo más cercano); el valor exacto va en ml_por_toma_exacto.",
        }
    else:
        avisos.append("Sin concentración: solo se dan mg. Indica «concentracion_mg_ml» para obtener los mL.")

    if mg_gota is not None or gotas_ml is not None:
        if mg_gota is not None:
            gotas = mg_toma / mg_gota
            paso = f"{_fmt(mg_toma)} mg ÷ {_fmt(mg_gota)} mg/gota = {_fmt(gotas)} gotas"
        else:
            gotas = (mg_toma / conc) * gotas_ml
            paso = f"{_fmt(mg_toma / conc)} mL × {_fmt(gotas_ml)} gotas/mL = {_fmt(gotas)} gotas"
        gotas_red = _redondear_gotas(gotas)
        pasos.append(f"{paso} → redondeado a gota entera: {_fmt(gotas_red, 0)} gotas")
        resultado |= {
            "gotas_por_toma_exacto": _f(gotas, 3),
            "gotas_por_toma_redondeado": int(gotas_red),
            "redondeo_gotas": "Gotas redondeadas a gota entera (a la más cercana); el valor exacto va en gotas_por_toma_exacto.",
        }

    return {k: v for k, v in resultado.items() if v is not None} | {
        "avisos": avisos,
        "calculo": pasos,
        "datos_de_entrada": {"peso_kg": _f(peso),
                             "dosis_mg_kg_por_toma": _f(dosis) if dosis_dia is None else None,
                             "dosis_mg_kg_dia": _f(dosis_dia) if dosis_dia is not None else None,
                             "concentracion_mg_ml": _f(conc) if conc is not None else None,
                             "tomas_al_dia": int(tomas) if tomas is not None else None,
                             "max_mg_toma": _f(max_toma) if max_toma is not None else None,
                             "max_mg_dia": _f(max_dia) if max_dia is not None else None,
                             "mg_por_gota": _f(mg_gota) if mg_gota is not None else None,
                             "gotas_por_ml": _f(gotas_ml) if gotas_ml is not None else None},
        "fuente": FUENTE,
        "nota": AVISO_FUENTE,
    }


# ───────────────────────── Conversiones ─────────────────────────

UNIDADES = {
    "mg": "mg", "miligramos": "mg",
    "mcg": "mcg", "µg": "mcg", "ug": "mcg", "microgramos": "mcg",
    "ml": "ml", "mililitros": "ml",
    "gotas": "gotas", "gota": "gotas",
    "%": "%", "porcentaje": "%",
    "mg/ml": "mg/ml",
}
NOMBRE = {"mg": "mg", "mcg": "mcg", "ml": "mL", "gotas": "gotas", "%": "%", "mg/ml": "mg/mL"}


def _unidad(nombre: str, u: str) -> str:
    clave = str(u).strip().lower().replace(" ", "")
    if clave not in UNIDADES:
        raise ToolError(f"Unidad «{u}» no reconocida en «{nombre}». Usa: mg, mcg, mL, gotas, % o mg/mL.")
    return UNIDADES[clave]


@mcp.tool()
def convertir(
    valor: float | str,
    de: str,
    a: str,
    concentracion_mg_ml: float | str | None = None,
    gotas_por_ml: float | str | None = None,
    mg_por_gota: float | str | None = None,
) -> dict:
    """Conversiones simples de unidades (cálculo determinista).

    Admite: mg ↔ mL (necesita concentracion_mg_ml), mcg ↔ mg, % → mg/mL (1 % = 10 mg/mL)
    y gotas, SOLO con el tamaño de la gota que indique la ficha técnica (nunca se supone):
    - gotas ↔ mg: con mg_por_gota (p. ej. 4), o con gotas_por_ml + concentracion_mg_ml.
    - gotas ↔ mL: con gotas_por_ml, o con mg_por_gota + concentracion_mg_ml (las gotas/mL se
      calculan aquí y se muestran en "calculo").
    mg_por_gota y gotas_por_ml son alternativos: indica el que dé la ficha.
    """
    v = _num("valor", valor)
    de_u, a_u = _unidad("de", de), _unidad("a", a)
    par = (de_u, a_u)

    if par in (("mg", "ml"), ("ml", "mg")):
        if concentracion_mg_ml is None:
            raise ToolError("Para pasar entre mg y mL hace falta «concentracion_mg_ml» (de la presentación).")
        c = _num("concentracion_mg_ml", concentracion_mg_ml)
        r = v / c if par == ("mg", "ml") else v * c
        op = f"{_fmt(v)} mg ÷ {_fmt(c)} mg/mL = {_fmt(r)} mL" if par == ("mg", "ml") else \
             f"{_fmt(v)} mL × {_fmt(c)} mg/mL = {_fmt(r)} mg"
    elif par == ("mcg", "mg"):
        r, op = v / 1000, f"{_fmt(v)} mcg ÷ 1000 = {_fmt(v / 1000, 6)} mg"
    elif par == ("mg", "mcg"):
        r, op = v * 1000, f"{_fmt(v)} mg × 1000 = {_fmt(v * 1000)} mcg"
    elif par == ("%", "mg/ml"):
        r, op = v * 10, f"{_fmt(v)} % × 10 = {_fmt(v * 10)} mg/mL (1 % = 1 g/100 mL = 10 mg/mL)"
    elif "gotas" in par and par in (("gotas", "ml"), ("ml", "gotas"), ("gotas", "mg"), ("mg", "gotas")):
        mg_gota, gotas_ml = _gota(mg_por_gota, gotas_por_ml, obligatorio=True)
        otra = a_u if de_u == "gotas" else de_u          # mg o ml
        pasos = []
        # Se completa el dato que falte con la concentración, mostrándolo.
        if otra == "ml" and gotas_ml is None:
            c = _num("concentracion_mg_ml", concentracion_mg_ml) if concentracion_mg_ml is not None else None
            if c is None:
                raise ToolError("Para pasar entre gotas y mL con «mg_por_gota» hace falta también «concentracion_mg_ml».")
            gotas_ml = c / mg_gota
            pasos.append(f"{_fmt(c)} mg/mL ÷ {_fmt(mg_gota)} mg/gota = {_fmt(gotas_ml)} gotas/mL")
        if otra == "mg" and mg_gota is None:
            c = _num("concentracion_mg_ml", concentracion_mg_ml) if concentracion_mg_ml is not None else None
            if c is None:
                raise ToolError("Para pasar entre gotas y mg con «gotas_por_ml» hace falta también «concentracion_mg_ml».")
            mg_gota = c / gotas_ml
            pasos.append(f"{_fmt(c)} mg/mL ÷ {_fmt(gotas_ml)} gotas/mL = {_fmt(mg_gota)} mg/gota")
        if otra == "ml":
            r = v / gotas_ml if de_u == "gotas" else v * gotas_ml
            pasos.append(f"{_fmt(v)} gotas ÷ {_fmt(gotas_ml)} gotas/mL = {_fmt(r)} mL" if de_u == "gotas" else
                         f"{_fmt(v)} mL × {_fmt(gotas_ml)} gotas/mL = {_fmt(r)} gotas")
        else:
            r = v * mg_gota if de_u == "gotas" else v / mg_gota
            pasos.append(f"{_fmt(v)} gotas × {_fmt(mg_gota)} mg/gota = {_fmt(r)} mg" if de_u == "gotas" else
                         f"{_fmt(v)} mg ÷ {_fmt(mg_gota)} mg/gota = {_fmt(r)} gotas")
        op = "; ".join(pasos)
    else:
        raise ToolError(f"Conversión no admitida: {NOMBRE[de_u]} → {NOMBRE[a_u]}. Admitidas: mg ↔ mL "
                        "(con concentración), mcg ↔ mg, % → mg/mL y gotas ↔ mg/mL (con mg_por_gota o "
                        "gotas_por_ml de la ficha).")

    return {"resultado": _f(r, 6), "unidad": NOMBRE[a_u], "calculo": op, "fuente": FUENTE, "nota": AVISO_FUENTE}


if __name__ == "__main__":
    mcp.run()
