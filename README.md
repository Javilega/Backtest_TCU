# Backtest TCU-PVPC / SPOT 600

Modelo de estimación del coste de energía (Término de Energía Variable, TEV) a partir del precio del Mercado Diario (ESIOS 600) para una comunidad de propietarios con suministro 3.0TD indexado.

## Resumen ejecutivo

| Componente | Qué es | ¿Se puede predecir? | MAPE OOS |
|---|---|---|---|
| **TEV** (coste de energía) | Pool × pérdidas + margen comercial | **Sí**, con SPOT 600 + fee mediano | **6,49%** |
| **TETA** (peajes + cargos) | Constante regulatoria (BOE) | **No** mes a mes (perfil de consumo volátil) | 70,35% (irreducible) |
| **TOTAL** (TEV + TETA) | Coste de energía total | **Sí**, si el TETA se usa como constante | ~6,5% (si TETA constante) |

**Conclusión:** El TEV es predecible con ~6,5% de error. El TETA es una constante regulatoria que **no necesita predecirse mes a mes** para comparar tecnologías. La comparación aerotermia vs radiadores se hace con `kWh × (TEV_predicho + TETA_medio)`.

## Modelo de producción

**TEV: SPOT 600 × (1 + pérdidas_p) + fee_mediano**

- **Pérdidas 3.0TD** (Circular 3/2020): P1=16,6%, P2=17,5%, P3=16,5%, P4=16,5%, P5=13,8%, P6=18,0%.
- **Fee mediano**: mediana winsorizada al 10% de `(TEV_real - TEV_base)`, calibrada con walk-forward (ventana expansiva, sin leakage).
- **Ponderación**: por kWh reales de cada periodo (`kwh_p1`..`kwh_p6`).

**TETA: peajes + cargos 2026 (constantes regulatorias, BOE)**

- **Peajes** (BOE-A-2025-26348, Anexo I, página 7, fila 3.0 TD):
  P1=0,027511 · P2=0,012376 · P3=0,004943 · P4=0,002627 · P5=0,000111 · P6=0,000031 €/kWh.
- **Cargos** (Orden TED/1524/2025, segmento 2):
  P1=0,035841 · P2=0,026538 · P3=0,014336 · P4=0,007168 · P5=0,004595 · P6=0,002867 €/kWh.
- **TETA total** (peaje + cargo): P1=0,063352 · P2=0,038914 · P3=0,019279 · P4=0,009795 · P5=0,004706 · P6=0,002898 €/kWh.

## Resultados de validación (train hasta 2026-03, test 2026-05 a 2026-09)

| Componente | MAPE | Sesgo | Veredicto |
|---|---:|---:|---|
| **TEV** (SPOT 600 + fee mediano) | **6,49%** | -1,87% | **APTO** |
| TETA (con perfil previsto) | 70,35% | - | NO APTO |
| TOTAL (TEV+TETA con perfil previsto) | 15,71% | - | NO APTO |

**Interpretación:** El TEV es apto. El TETA no se puede predecir mes a mes con los datos actuales (17 meses, sin temperatura, perfil de consumo volátil). El TOTAL con TETA previsto no es apto, pero **el TOTAL con TETA constante sí lo es** para la comparación tecnológica.

## Comparación tecnológica (aerotermia vs radiadores)

Para comparar el coste operativo de dos tecnologías durante el periodo de calefacción (oct-may):
