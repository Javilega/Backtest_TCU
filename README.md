# Backtest TCU-PVPC / SPOT 600

Modelo de estimación del coste de energía (Término de Energía Variable, TEV) a partir del precio del Mercado Diario (ESIOS 600) y del TCU del PVPC (ESIOS 1001).

## Modelo de producción

**SPOT 600 + fee aditivo robusto (mediana winsorizada al 10%) con ventana completa de entrenamiento.**

- Se predice el **TEV** (pool + pérdidas), no el precio final de la factura.
- El **TETA** (peajes + cargos) se toma directamente de la factura, no se predice.
- El precio final se reconstruye como: `TEV_predicho + TETA_real + (precio_real_original - TEV_real - TETA_real)`.
  El último término agrupa potencia + impuestos, que se asumen conocidos (es lo que paga el cliente).
- **2026-04 excluido del entrenamiento**: sin desglose por periodos, contamina la calibración.

### Resultados out-of-sample (train hasta 2026-03, test 2026-05 a 2026-09)

| Indicador | MAPE TEV | Sesgo TEV | Veredicto |
|---|---|---|---|
| **SPOT 600 (producción)** | **~7,7%** | **+2,8%** | **APTO** (criterio MAPE≤10%, \|sesgo\|≤5%) |
| PVPC 1001 (diagnóstico) | ~14,8% | −14,8% | **DESCARTADO** |

## Modelos descartados con evidencia

| Modelo | Resultado | Motivo del descarte |
|---|---|---|
| Fee por bloque (P1P2/P3P4/P5P6) | MAPE 2–3× peor | Sobreajuste con 11–17 meses |
| Ancla a media móvil 3m | Empeora ambos | Arrastra régimen anterior |
| Ventana móvil 3 meses para fee | Inestable en 2025 | Fee se queda corto en cambios de régimen |
| PVPC 1001 como modelo | MAPE ~15% | Costes regulados no capturables con fee lineal |

## Hallazgos clave de las simulaciones

1. **`precio_real` es un precio ponderado por consumo con impuestos**, no el pool puro. Comparar pool contra `precio_real` mete un sesgo del 20–40% que ningún fee puede corregir.
2. **El desglose TEV/TETA de las facturas es imprescindible**. Sin él, el fee absorbe peajes, cargos, potencia e impuestos indistintamente.
3. **El diagnóstico de pérdidas es AMBIGUO en SPOT** (pendientes P1: 0,925, P2: 0,891, P3: 0,768). Se fuerza `incluye=False` por definición de REE (SPOT 600 no incluye pérdidas 2.0TD).
4. **El diagnóstico de pérdidas es trivial en PVPC** (pendientes 1,000 por comparar PVPC consigo mismo). No aporta información.
5. **ESIOS devuelve huecos en algunos periodos** (P3 y P4 vacíos en varios meses). El modelo imputa con la media de los 3 meses anteriores; si no hay histórico, ignora el periodo y repondera.
6. **17 meses es insuficiente para estacionalidad fina o fee por bloque**. Con 36 meses se podría reintentar el fee estacional con garantías.

## Justificación normativa de los periodos P1–P6

Las diferencias de precio entre periodos están establecidas por la Circular 3/2020 de la CNMC y modificadas por la Circular 7/2020. Cada periodo tiene peajes, cargos y coeficientes de pérdidas distintos que reflejan el coste real que impone al sistema eléctrico.

- **P1**: temporada alta, horas punta (ene, feb, jul, dic).
- **P2**: temporada alta, horas llano + temporada media-alta punta (mar, nov).
- **P3**: temporada media, horas punta (jun, ago, sep).
- **P4–P5**: temporada baja (abr, may, oct).
- **P6**: valle nocturno y fines de semana (todos los meses).

## Instalación y ejecución

```bash
pip install -r requirements.txt
cp .env.example .env
# Editar .env con ESIOS_TOKEN
python backtest_tcu.py facturas_desglosadas.csv --train-hasta 2026-03
