"""Tablas de peajes y cargos de energía 3.0TD (€/kWh) por periodo.

FUENTE VERIFICADA:
- 2025:
  - Peajes: Resolución CNMC de diciembre de 2024 (BOE).
  - Cargos: Orden TED/1487/2024.
- 2026:
  - Peajes: Resolución CNMC 18/12/2025 (BOE-A-2025-26348).
  - Cargos: Orden TED/1524/2025, segmento 2.
"""

# Peajes de energía 3.0TD (€/kWh)
PEAJES_ENERGIA_30TD = {
    2025: {
        1: 0.027511,
        2: 0.012376,
        3: 0.004943,
        4: 0.002627,
        5: 0.000111,
        6: 0.000031,
    },
    2026: {
        1: 0.007379,
        2: 0.003798,
        3: 0.001273,
        4: 0.000635,
        5: 0.000032,
        6: 0.000015,
    },
}

# Cargos de energía 3.0TD (€/kWh)
CARGOS_ENERGIA_30TD = {
    2025: {
        1: 0.035841,
        2: 0.026538,
        3: 0.014336,
        4: 0.007168,
        5: 0.004595,
        6: 0.002867,
    },
    2026: {
        1: 0.035841,
        2: 0.026538,
        3: 0.014336,
        4: 0.007168,
        5: 0.004595,
        6: 0.002867,
    },
}


def tarifa_acceso_energia(anio, periodo):
    """(peaje + cargo) en €/kWh para un año y periodo 3.0TD."""
    if anio not in PEAJES_ENERGIA_30TD:
        raise ValueError(f"Sin peajes para el año {anio}. Añádelos en teta_regulado.py")
    return PEAJES_ENERGIA_30TD[anio][periodo] + CARGOS_ENERGIA_30TD[anio][periodo]


def teta_estimado(anio, pesos_consumo):
    """TETA estimado (€/kWh) ponderado por pesos de consumo por periodo.

    pesos_consumo: dict {periodo: fraccion} que suma 1.
    Los pesos deben ser PREVISTOS, no los reales de la factura futura.
    """
    total = 0.0
    for p, peso in pesos_consumo.items():
        if peso > 0:
            total += tarifa_acceso_energia(anio, p) * peso
    return total
