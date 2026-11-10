"""Tablas de peajes y cargos de energía 3.0TD (€/kWh) por periodo.

FUENTE VERIFICADA:
- Peajes: Resolución de 18 de diciembre de 2025, de la CNMC, por la que se
  establecen los valores de los peajes de acceso a las redes de transporte y
  distribución de electricidad de aplicación a partir del 1 de enero de 2026.
  (BOE-A-2025-26348). Anexo, peaje 3.0TD, término de energía.
- Cargos: Orden TED/1524/2025, de 23 de diciembre, por la que se establecen
  los precios de los cargos del sistema eléctrico para 2026. Segmento 2.
"""

# Peajes de energía 3.0TD (€/kWh) — Resolución CNMC 18/12/2025 (BOE-A-2025-26348)
PEAJES_ENERGIA_30TD = {
    2026: {
        1: 0.007379,
        2: 0.003798,
        3: 0.001273,
        4: 0.000635,
        5: 0.000032,
        6: 0.000015,
    },
}

# Cargos de energía 3.0TD (€/kWh) — Orden TED/1524/2025, segmento 2
CARGOS_ENERGIA_30TD = {
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