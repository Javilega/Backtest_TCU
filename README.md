# Backtest TCU-PVPC

Modelo de estimación del precio medio de energía 3.0TD indexada a partir del TCU del PVPC (ESIOS 1001) y del Mercado Diario (ESIOS 600).

## Resumen de resultados (17 meses, walk-forward, ventana 6)

| Indicador | Modelo | MAPE walk-forward | Sesgo | Peor mes | Estado |
|---|---|---|---|---|---|
| **SPOT (600)** | fee mediana robusta | **5,20%** | −0,98% | 13,5% | **APTO** |
| **PVPC (1001)** | fee mediana robusta | **11,98%** | +0,15% | 23,0% | Mejor disponible, no apto |

**Modelo de producción:** fee aditivo robusto (mediana winsorizada al 10%) + walk-forward con ventana móvil de 6 meses. Sin ancla. 2026-04 excluido del entrenamiento (sin desglose por periodos).

## Modelos descartados con evidencia

| Modelo | MAPE SPOT (test) | MAPE PVPC (test) | Motivo del descarte |
|---|---|---|---|
| Fee media (original) | 5,52% | 6,31% | Inestable en walk-forward (PVPC: 15,21%) |
| Fee por bloque (P1P2/P3P4/P5P6) | 14,67% | 18,37% | Sobreajuste: 2-3× peor que aditivo con 11-17 meses |
| Ancla a media móvil 3m | 9,34% | 14,37% | Empeora ambos indicadores; arrastra régimen anterior |
| Sin fee | 32,35% | 18,56% | El fee captura peajes/cargos que el pool no incluye |

## Justificación normativa de los periodos P1–P6

Las diferencias de precio entre periodos están establecidas por la Circular 3/2020 de la CNMC y modificadas por la Circular 7/2020. Cada periodo tiene peajes, cargos y coeficientes de pérdidas distintos que reflejan el coste real que impone al sistema eléctrico.

## Limitaciones conocidas

- **17 meses de datos:** insuficiente para estimar estacionalidad fina o fee por bloque. El cambio de régimen 2025→2026 (precios más bajos en 2026) hace que el modelo entrenado con 2025 sobrecorrija 2026.
- **PVPC con error estructural ~12%:** la ponderación por kWh de PVPC no refleja la estructura real 2.0TD/3.0TD del PVPC. El diagnóstico `INCLUYE_PERDIDAS` es trivial (pendiente 1,000 por construcción al comparar PVPC consigo mismo). Para mejorar PVPC haría falta auditar `precio_medio_ponderado` con `periodo_20td` o usar el PVPC publicado directamente.
- **Diagnóstico de pérdidas AMBIGUO en SPOT:** los pendientes (P1: 0,925, P2: 0,891, P3: 0,768) están entre los dos valores teóricos. Se fuerza `incluye=False` por definición de REE (el SPOT 600 no incluye pérdidas 2.0TD).

## Instalación y ejecución

```bash
pip install -r requirements.txt
cp .env.example .env
# Editar .env con tu token de ESIOS
python backtest_tcu.py facturas_actualizadas.csv --train-hasta 2026-03 --ventana 6
