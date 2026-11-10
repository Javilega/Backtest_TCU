"""
Cálculo del coste de energía (TEV + TETA) para comparación tecnológica.

- TEV: SPOT × (1 + pérdidas_p) + fee_mediano, ponderado por kWh reales.
- TETA: constante regulatoria (peajes + cargos BOE), ponderada por el
  perfil de consumo típico de la comunidad.
- Resultado: coste unitario (€/kWh) y coste total (€) sin IVA.

Uso:
    python coste_energia.py facturas_desglosadas.csv spot_mensual.csv
"""
import sys
import numpy as np
import pandas as pd


# Coeficientes de pérdidas 3.0TD (Circular 3/2020)
PERDIDAS_30TD = {
    1: 0.166, 2: 0.175, 3: 0.165,
    4: 0.165, 5: 0.138, 6: 0.180,
}

# TETA 2026 = peaje + cargo, por periodo (€/kWh).
# Fuente: BOE-A-2025-26348 (peajes) + Orden TED/1524/2025 (cargos).
TETA_30TD_2026 = {
    1: 0.063352, 2: 0.038914, 3: 0.019279,
    4: 0.009795, 5: 0.004706, 6: 0.002898,
}

MESES_INVIERNO = {10, 11, 12, 1, 2, 3, 4, 5}


def cargar_facturas(ruta):
    """Lee el CSV de facturas y valida columnas mínimas."""
    df = pd.read_csv(ruta)
    columnas_necesarias = {"mes"} | {f"kwh_p{p}" for p in range(1, 7)}
    faltan = columnas_necesarias - set(df.columns)
    if faltan:
        raise ValueError(f"Faltan columnas en {ruta}: {faltan}")
    df["mes"] = df["mes"].astype(str)
    for p in range(1, 7):
        df[f"kwh_p{p}"] = pd.to_numeric(df[f"kwh_p{p}"], errors="coerce").fillna(0)
    df["kwh_total"] = df[[f"kwh_p{p}" for p in range(1, 7)]].sum(axis=1)
    return df.sort_values("mes").reset_index(drop=True)


def cargar_spot(ruta):
    """Lee el CSV de SPOT mensual (€/kWh)."""
    df = pd.read_csv(ruta)
    if not {"mes", "spot_medio"}.issubset(df.columns):
        raise ValueError(f"El CSV de SPOT debe tener 'mes' y 'spot_medio': {ruta}")
    df["mes"] = df["mes"].astype(str)
    df["spot_medio"] = pd.to_numeric(df["spot_medio"], errors="coerce")
    return df.sort_values("mes").reset_index(drop=True)


def fee_walk_forward(df, ventana_min=6):
    """
    Calcula el fee_mediano para cada mes con ventana EXPANSIVA estricta.

    Para el mes t, usa solo los meses 0..t-1 (nunca el propio t).
    La mediana se calcula sobre los residuales TEV_real - TEV_base,
    winsorizados al 10% por cola.

    Args:
        df: DataFrame con columnas 'mes', 'kwh_p1'..'kwh_p6',
            'coste_energia_real' (TEV real), 'spot_medio'.
        ventana_min: mínimo de meses previos para calibrar el fee.

    Returns:
        DataFrame con columna 'fee_mediano' (NaN para los meses sin
        histórico suficiente).
    """
    df = df.copy()
    fees = []
    for i in range(len(df)):
        if i < ventana_min:
            fees.append(np.nan)
            continue
        train = df.iloc[:i]  # SOLO meses anteriores
        # TEV base mensual (ponderado por kWh reales de cada periodo)
        tev_base = (
            train[[f"kwh_p{p}" for p in range(1, 7)]].values
            * np.array([1 + PERDIDAS_30TD[p] for p in range(1, 7)])
        ).sum(axis=1) * train["spot_medio"].values
        tev_base_medio = tev_base / train["kwh_total"].replace(0, np.nan)
        residuales = train["coste_energia_real"].values - tev_base_medio.values
        residuales = residuales[~np.isnan(residuales)]
        if len(residuales) < 3:
            fees.append(np.nan)
            continue
        # Winsorizar al 10% por cola
        lo, hi = np.quantile(residuales, [0.10, 0.90])
        residuales_w = np.clip(residuales, lo, hi)
        fees.append(float(np.median(residuales_w)))
    df["fee_mediano"] = fees
    return df


def teta_medio_invierno(df, meses_invierno=MESES_INVIERNO):
    """
    TETA medio regulatorio para el periodo de calefacción.

    Pondera los TETA por periodo con el perfil de consumo típico de los
    meses de invierno del histórico (media de kwh_p1..kwh_p6).
    Es una constante, no una predicción mes a mes.
    """
    df_inv = df[df["mes"].str[-2:].astype(int).isin(meses_invierno)]
    if df_inv.empty:
        raise ValueError("Sin meses de invierno en el histórico")
    total = df_inv[[f"kwh_p{p}" for p in range(1, 7)]].sum()
    perfil = total / total.sum()
    teta_medio = sum(TETA_30TD_2026[p] * perfil[f"kwh_p{p}"] for p in range(1, 7))
    return teta_medio, perfil.to_dict()


def calcular_coste(df, teta_constante):
    """
    Calcula TEV (por periodo) y coste unitario (TEV + TETA constante).

    Args:
        df: DataFrame con columnas 'mes', 'kwh_p1'..'kwh_p6', 'kwh_total',
            'spot_medio', 'fee_mediano'.
        teta_constante: TETA medio regulatorio (€/kWh).

    Returns:
        DataFrame con columnas:
        - tev_p1..tev_p6 (€/kWh por periodo)
        - tev_unitario (€/kWh, ponderado por kWh)
        - teta_constante (€/kWh)
        - coste_unitario (€/kWh, TEV + TETA)
        - coste_total (€)
    """
    df = df.copy()
    for p in range(1, 7):
        df[f"tev_p{p}"] = df["spot_medio"] * (1 + PERDIDAS_30TD[p]) + df["fee_mediano"]

    numerador = 0.0
    denominador = 0.0
    for p in range(1, 7):
        numerador = numerador + df[f"kwh_p{p}"] * df[f"tev_p{p}"]
        denominador = denominador + df[f"kwh_p{p}"]
    df["tev_unitario"] = numerador / denominador.replace(0, np.nan)
    df["teta_constante"] = teta_constante
    df["coste_unitario"] = df["tev_unitario"] + df["teta_constante"]
    df["coste_total"] = df["coste_unitario"] * df["kwh_total"]
    return df


def main(argv):
    if len(argv) < 3:
        print("Uso: python coste_energia.py <facturas.csv> <spot.csv>")
        return 1

    facturas = cargar_facturas(argv[1])
    spot = cargar_spot(argv[2])
    df = facturas.merge(spot, on="mes", how="inner")
    if df.empty:
        print("ERROR: no hay meses en común entre facturas y SPOT.")
        return 1
    if "coste_energia_real" not in df.columns:
        print("ERROR: falta columna 'coste_energia_real' para calibrar el fee.")
        return 1

    df = fee_walk_forward(df, ventana_min=6)

    teta_medio, perfil = teta_medio_invierno(df)
    print(f"\n{'='*70}\n TETA MEDIO REGULATORIO (periodo de calefacción)\n{'='*70}")
    print(f"TETA medio: {teta_medio:.5f} €/kWh")
    print(f"Perfil típico: " + ", ".join(f"P{p}={perfil[f'kwh_p{p}']:.1%}" for p in range(1, 7)))

    df = calcular_coste(df, teta_medio)

    print(f"\n{'='*70}\n COSTE DE ENERGÍA (TEV + TETA constante, sin IVA)\n{'='*70}")
    print(f"{'Mes':<10} {'kWh':>10} {'TEV ud':>10} {'TETA':>10} {'Coste ud':>10} {'Coste tot':>12}")
    for _, row in df.iterrows():
        if pd.isna(row["coste_unitario"]):
            continue
        print(f"{row['mes']:<10} {row['kwh_total']:>10.0f} {row['tev_unitario']:>10.5f} "
              f"{row['teta_constante']:>10.5f} {row['coste_unitario']:>10.5f} {row['coste_total']:>12.2f}")

    if "coste_energia_real" in df.columns and "peajes_cargos_real" in df.columns:
        df["coste_real"] = df["coste_energia_real"] + df["peajes_cargos_real"]
        df["error_pct"] = 100 * (df["coste_unitario"] / df["coste_real"] - 1)
        validos = df.dropna(subset=["coste_unitario", "coste_real"])
        if not validos.empty:
            print(f"\n{'='*70}\n VALIDACIÓN vs coste real (TEV+TETA factura)\n{'='*70}")
            print(f"MAPE: {validos['error_pct'].abs().mean():.2f} %")
            print(f"Sesgo: {validos['error_pct'].mean():+.2f} %")
            print(f"Peor mes: {validos['error_pct'].abs().max():.2f} %")
            print(f"n meses válidos: {len(validos)}")

    df.to_csv("coste_energia_resultado.csv", index=False)
    print(f"\nResultados guardados en coste_energia_resultado.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
